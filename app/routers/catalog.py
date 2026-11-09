"""Страницы каталога на основе прайс-листа склада: обзор, категория, бренд, поиск.

Цены в прайсе закупочные: в шаблоны попадает только цена клиента (PricePolicy), строки
таблицы собираются здесь же в CatalogRow без supplier_price. Кнопка «Цены и сроки» ведёт на /search,
где по артикулу запрашиваются живые предложения поставщика с добавлением в корзину.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import get_current_customer
from app.models import Brand, Customer, PriceListItem
from app.services.pricelist import catalog
from app.services.pricelist.catalog import BrandCount
from app.services.pricelist.categories import CATEGORY_BY_SLUG
from app.services.pricing import PricePolicy
from app.services.pricing.autoeuro import supplier_label
from app.services.pricing.service import build_policy
from app.templating import templates

router = APIRouter()

PER_PAGE = 50
MAX_PER_PAGE = 100
STOCK_EXACT_LIMIT = 10  # остаток больше — показываем «больше 10»
PAGES_AROUND = 2  # сколько номеров страниц показывать по обе стороны от текущей
DIGITS_GROUP = "0–9"
OTHER_GROUP = "#"
_UNITS = {"штука": "шт."}


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status_code)


# ---- Строки таблицы ----


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """Позиция прайса для показа: цена уже клиентская, закупочной здесь нет."""

    brand: str
    brand_slug: str
    code: str
    oem_code: str | None
    name: str
    stock: str  # «По прайсу: 5 шт.»
    pack: int | None  # кратность отпуска, если больше 1
    supplier: str
    price: Decimal


def stock_text(qty: int, unit: str | None) -> str:
    """«По прайсу: 5 шт.», при остатке больше STOCK_EXACT_LIMIT — «По прайсу: больше 10 шт.»."""
    shown = f"больше {STOCK_EXACT_LIMIT}" if qty > STOCK_EXACT_LIMIT else str(qty)
    unit = (unit or "").strip()
    return f"По прайсу: {shown} {_UNITS.get(unit.lower(), unit) or 'шт.'}"


def make_rows(items: Iterable[PriceListItem], policy: PricePolicy) -> list[CatalogRow]:
    return [
        CatalogRow(
            brand=item.brand,
            brand_slug=item.brand_slug,
            code=item.code,
            oem_code=item.oem_code,
            name=item.name,
            stock=stock_text(item.qty, item.unit),
            pack=item.min_pack if item.min_pack and item.min_pack > 1 else None,
            supplier=supplier_label(True, False, item.warehouse),
            price=policy.client_price(item.supplier_price),
        )
        for item in items
    ]


# ---- Пагинация ----


@dataclass(frozen=True, slots=True)
class PageLink:
    number: int | None  # None — многоточие
    url: str = ""
    current: bool = False


@dataclass(frozen=True, slots=True)
class Pager:
    prev_url: str | None
    next_url: str | None
    links: list[PageLink]


def page_numbers(page: int, pages: int, around: int = PAGES_AROUND) -> list[int | None]:
    """Номера страниц: первая, последняя и ±around от текущей; None — пропуск («…»).

    Пропуск ровно в одну страницу заменяется самой страницей: «1 2 3 4», а не «1 … 3 4».
    """
    shown = {1, pages, *range(max(1, page - around), min(pages, page + around) + 1)}
    result: list[int | None] = []
    previous = 0
    for number in sorted(shown):
        if number - previous == 2:
            result.append(previous + 1)
        elif number - previous > 2:
            result.append(None)
        result.append(number)
        previous = number
    return result


def page_url(request: Request, page: int) -> str:
    """Адрес текущей страницы с другим номером; прочие непустые параметры сохраняются."""
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "page" and v]
    if page > 1:
        params.append(("page", str(page)))
    query = urlencode(params)
    return request.url.path + (f"?{query}" if query else "")


def build_pager(request: Request, result: catalog.Page) -> Pager | None:
    if result.pages <= 1:
        return None
    links = [
        PageLink(n, page_url(request, n), n == result.page) if n else PageLink(None)
        for n in page_numbers(result.page, result.pages)
    ]
    return Pager(
        prev_url=page_url(request, result.page - 1) if result.page > 1 else None,
        next_url=page_url(request, result.page + 1) if result.page < result.pages else None,
        links=links,
    )


# ---- Индекс брендов ----


@dataclass(frozen=True, slots=True)
class BrandGroup:
    letter: str
    brands: list[BrandCount]


def _group_key(name: str) -> tuple[int, str]:
    """Группа бренда по первой букве или цифре: цифры, латиница, кириллица, остальное."""
    ch = next((c for c in name if c.isalnum()), "").upper()
    if not ch:
        return 4, OTHER_GROUP
    if ch.isdigit():
        return 0, DIGITS_GROUP
    if "A" <= ch <= "Z":
        return 1, ch
    if "А" <= ch <= "Я" or ch == "Ё":
        return 2, "Е" if ch == "Ё" else ch
    return 3, ch


def group_brands(brands: Iterable[BrandCount]) -> list[BrandGroup]:
    """Бренды по группам «0–9», A…Z, А…Я; внутри группы порядок исходного списка."""
    groups: dict[tuple[int, str], list[BrandCount]] = {}
    for brand in brands:
        groups.setdefault(_group_key(brand.name), []).append(brand)
    return [BrandGroup(key[1], groups[key]) for key in sorted(groups)]


# ---- Страницы ----


def _policy() -> PricePolicy:
    return build_policy(get_settings())


async def _list_page(
    request: Request,
    session: AsyncSession,
    customer: Customer | None,
    template: str,
    result: catalog.Page,
    **context,
):
    """Общая часть страниц со списком позиций: строки, пагинация, дата прайса."""
    return render(
        request,
        template,
        active="catalog",
        customer=customer,
        result=result,
        rows=make_rows(result.items, _policy()),
        pager=build_pager(request, result),
        price_import=await catalog.last_import(session),
        **context,
    )


def _not_found(request: Request, customer: Customer | None, title: str, text: str, **context):
    return render(
        request,
        "catalog_missing.html",
        status_code=404,
        active="catalog",
        customer=customer,
        title=title,
        text=text,
        **context,
    )


@router.get("/catalog", response_class=HTMLResponse)
async def overview(
    request: Request,
    category: str = Query("", max_length=50),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    # Старые ссылки вида /catalog?category=<slug>: известную категорию открываем сразу
    if category in CATEGORY_BY_SLUG:
        return RedirectResponse(f"/catalog/category/{category}", status_code=307)
    categories = await catalog.list_categories(session)
    brands = await catalog.list_brands(session) if categories else []
    return render(
        request,
        "catalog.html",
        active="catalog",
        customer=customer,
        categories=categories,
        items_total=sum(c.count for c in categories),
        brands_count=len(brands),
        brand_groups=group_brands(brands),
        price_import=await catalog.last_import(session),
    )


@router.get("/catalog/search", response_class=HTMLResponse)
async def catalog_search(
    request: Request,
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE, ge=1, le=MAX_PER_PAGE),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    q = q.strip()
    if not q:
        return RedirectResponse("/catalog", status_code=303)
    result = await catalog.search_items(session, q=q, page=page, per_page=per_page)
    return await _list_page(request, session, customer, "catalog_search.html", result, q=q)


@router.get("/catalog/category/{slug}", response_class=HTMLResponse)
async def category_page(
    request: Request,
    slug: str,
    brand: str = Query("", max_length=100),
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE, ge=1, le=MAX_PER_PAGE),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    rule = CATEGORY_BY_SLUG.get(slug)
    if rule is None:
        return _not_found(
            request, customer, "Категория не найдена", "Такой категории нет в каталоге."
        )
    q = q.strip()
    brands = await catalog.list_brands(session, category=slug)
    result = await catalog.search_items(
        session, category=slug, brand_slug=brand or None, q=q, page=page, per_page=per_page
    )
    return await _list_page(
        request,
        session,
        customer,
        "catalog_category.html",
        result,
        category=rule,
        brands=brands,
        category_total=sum(b.count for b in brands),
        brand=brand,
        q=q,
    )


@router.get("/catalog/brand/{brand_slug}", response_class=HTMLResponse)
async def brand_page(
    request: Request,
    brand_slug: str,
    category: str = Query("", max_length=50),
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE, ge=1, le=MAX_PER_PAGE),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    brand = await catalog.get_brand(session, brand_slug)
    if brand is None:
        return _not_found(
            request, customer, "Бренд не найден", "Позиций этого бренда в прайсе склада нет."
        )
    category = category if category in CATEGORY_BY_SLUG else ""
    q = q.strip()
    categories = await catalog.list_categories(session, brand_slug=brand_slug)
    result = await catalog.search_items(
        session,
        brand_slug=brand_slug,
        category=category or None,
        q=q,
        page=page,
        per_page=per_page,
    )
    return await _list_page(
        request,
        session,
        customer,
        "catalog_brand.html",
        result,
        brand=brand,
        categories=categories,
        category=category,
        q=q,
    )


@router.get("/catalog/{slug}", response_class=HTMLResponse)
async def legacy_brand(
    request: Request,
    slug: str,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    """Старые адреса /catalog/<марка> с главной: бренд из прайса — на его страницу, иначе 404."""
    if await catalog.get_brand(session, slug) is not None:
        return RedirectResponse(f"/catalog/brand/{slug}", status_code=307)
    # Марка автомобиля, которой нет среди брендов прайса: предлагаем искать по применяемости
    make = await session.scalar(select(Brand.name).where(Brand.slug == slug))
    return _not_found(
        request,
        customer,
        f"Каталог {make}" if make else "Бренд не найден",
        f"Запчастей бренда {make} в прайсе склада нет, но детали для этой марки "
        "можно найти по названию."
        if make
        else "Позиций этого бренда в прайсе склада нет.",
        search_q=make,
    )
