"""Запросы к импортированному прайсу для страниц каталога: категории, бренды, позиции.

Только данные, без HTML. Все запросы работают и в SQLite (тесты), и в PostgreSQL.
Поиск по названию без учёта регистра опирается на lower()/ILIKE базы: в PostgreSQL
это полноценный Unicode, в SQLite — только латиница.
"""

from dataclasses import dataclass
from math import ceil

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.models import PriceListImport, PriceListItem
from app.services.pricelist.categories import CATEGORIES, OTHER_SLUG
from app.services.pricing.base import normalize_code

MAX_PER_PAGE = 200


@dataclass(frozen=True, slots=True)
class CategoryCount:
    slug: str
    title: str
    icon: str
    count: int


@dataclass(frozen=True, slots=True)
class BrandCount:
    name: str
    slug: str
    count: int


@dataclass(frozen=True, slots=True)
class Page:
    items: list[PriceListItem]
    total: int
    page: int
    pages: int
    per_page: int


def _filters(
    *, brand_slug: str | None = None, category: str | None = None, q: str | None = None
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if brand_slug:
        conditions.append(PriceListItem.brand_slug == brand_slug)
    if category:
        conditions.append(PriceListItem.category == category)
    text = (q or "").strip()
    if text:
        # autoescape экранирует % и _ в запросе, чтобы они не работали как шаблон LIKE
        variants = [
            and_(
                *[
                    or_(
                        PriceListItem.name.icontains(word, autoescape=True),
                        PriceListItem.brand.icontains(word, autoescape=True),
                    )
                    for word in text.split()
                ]
            )
        ]
        code = normalize_code(text)
        if code:
            variants.append(PriceListItem.code_norm.contains(code, autoescape=True))
            variants.append(PriceListItem.oem_code_norm.contains(code, autoescape=True))
        conditions.append(or_(*variants))
    return conditions


async def list_categories(
    session: AsyncSession, *, brand_slug: str | None = None
) -> list[CategoryCount]:
    """Категории с числом позиций в порядке CATEGORIES, «Прочее» последней; пустые не выводятся.

    brand_slug — считать только позиции этого бренда (чипы категорий на странице бренда).
    """
    rows = await session.execute(
        select(PriceListItem.category, func.count())
        .where(*_filters(brand_slug=brand_slug))
        .group_by(PriceListItem.category)
    )
    counts = dict(rows.all())
    # порядок CATEGORIES задаёт порядок показа; «Прочее» на странице всегда в конце
    ordered = sorted(CATEGORIES, key=lambda c: c.slug == OTHER_SLUG)
    return [
        CategoryCount(c.slug, c.title, c.icon, counts[c.slug])
        for c in ordered
        if counts.get(c.slug)
    ]


async def list_brands(
    session: AsyncSession, *, category: str | None = None, q: str | None = None
) -> list[BrandCount]:
    """Бренды с числом позиций по алфавиту: латиница, затем кириллица; category и q — фильтры.

    Сортировка в Python, а не в базе: порядок ORDER BY зависит от collation (в PostgreSQL
    с en_US кириллица может оказаться раньше латиницы, в SQLite — порядок байтов).
    Брендов сотни, так что это дёшево.
    """
    stmt = (
        select(PriceListItem.brand, PriceListItem.brand_slug, func.count())
        .where(*_filters(category=category, q=q))
        .group_by(PriceListItem.brand, PriceListItem.brand_slug)
    )
    rows = await session.execute(stmt)
    brands = [BrandCount(name, slug, count) for name, slug, count in rows.all()]
    return sorted(brands, key=lambda b: (b.name.casefold(), b.name, b.slug))


async def get_brand(session: AsyncSession, slug: str) -> BrandCount | None:
    """Бренд по slug с числом позиций; None, если в прайсе такого нет."""
    rows = await session.execute(
        select(PriceListItem.brand, func.count())
        .where(PriceListItem.brand_slug == slug)
        .group_by(PriceListItem.brand)
        .order_by(func.count().desc(), PriceListItem.brand)
    )
    found = rows.all()
    if not found:
        return None
    return BrandCount(found[0][0], slug, sum(count for _, count in found))


async def search_items(
    session: AsyncSession,
    *,
    brand_slug: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> Page:
    """Страница позиций. q ищется в названии (без регистра) и по артикулу/OEM (нормализованно)."""
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    conditions = _filters(brand_slug=brand_slug, category=category, q=q)
    total = await session.scalar(select(func.count()).select_from(PriceListItem).where(*conditions))
    total = total or 0
    pages = max(1, ceil(total / per_page))
    page = min(max(page, 1), pages)
    items = await session.scalars(
        select(PriceListItem)
        .where(*conditions)
        .order_by(PriceListItem.brand, PriceListItem.name, PriceListItem.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    return Page(list(items.all()), total, page, pages, per_page)


async def last_import(session: AsyncSession) -> PriceListImport | None:
    """Последний успешный импорт — для даты актуальности прайса на страницах."""
    return await session.scalar(
        select(PriceListImport)
        .where(PriceListImport.status == "done")
        .order_by(PriceListImport.id.desc())
        .limit(1)
    )


async def items_by_code(session: AsyncSession, code: str) -> list[PriceListItem]:
    """Позиции прайса с таким артикулом (сравнение без разделителей и регистра)."""
    code_norm = normalize_code(code)
    if not code_norm:
        return []
    items = await session.scalars(
        select(PriceListItem)
        .where(PriceListItem.code_norm == code_norm)
        .order_by(PriceListItem.brand, PriceListItem.id)
    )
    return list(items.all())
