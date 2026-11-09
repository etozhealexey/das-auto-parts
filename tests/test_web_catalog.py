"""Страницы каталога по прайсу через HTTP на SQLite в памяти: обзор, категория, бренд, поиск."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import Brand, PriceListImport, PriceListItem
from app.routers.catalog import BrandCount, group_brands, page_numbers, stock_text
from app.services.pricelist import catalog
from app.services.pricelist.categories import CATEGORY_BY_SLUG
from app.services.pricing.base import normalize_code
from app.services.pricing.policy import PricePolicy
from app.templating import templates
from tests.pricelist_samples import sqlite_sessionmaker

WAREHOUSE = "Санкт-Петербург ЦС"
SUPPLIER = f"Склад Авто-Евро · {WAREHOUSE}"
FINISHED_AT = datetime(2026, 10, 27, 11, 42, tzinfo=UTC)
money = templates.env.filters["money"]
date = templates.env.filters["date"]

# brand, brand_slug, code, oem, name, category, supplier_price, qty, min_pack, unit
ITEMS = [
    ("MANN-FILTER", "mann-filter", "W 712/75", None, "Фильтр масляный OPEL ASTRA G/H",
     "filters", "486.00", 37, 1, "шт."),
    ("MANN-FILTER", "mann-filter", "C 25 114", None, "Фильтр воздушный BMW 3 (E46)",
     "filters", "1210.50", 4, 1, "шт."),
    ("Toyota", "toyota", "04465-33471", None, "Колодки торм. TOYOTA Camry (2006>)",
     "brakes", "2037.38", 8, 1, "шт."),
    ("Toyota", "toyota", "90915-YZZE1", None, "Фильтр масляный TOYOTA (оригинал)",
     "filters", "640.00", 12, 1, "шт."),
    ("KAYABA", "kayaba", "349079", "E6210-JD02A", "Амортизатор зад. NISSAN QASHQAI J10E 06>",
     "suspension", "7064.47", 2, 1, "шт."),
    ("4U", "4u", "FO-12345", None, "Втулка стабилизатора FORD FOCUS II",
     "suspension", "199.90", 5, 1, "шт."),
    ("LIQUI MOLY", "liqui-moly", "7516", None, "Масло моторное 5W-30 Molygen 4л",
     "oils", "5890.00", 48, 4, "шт."),
    ("Дело Техники", "delo-tekhniki", "513016", None, "Ключ свечной 16 мм",
     "tools", "350.00", 6, 1, "штука"),
]  # fmt: skip


def make_item(brand, brand_slug, code, oem, name, category, price, qty, min_pack, unit):
    return PriceListItem(
        provider="autoeuro",
        warehouse=WAREHOUSE,
        item_key=f"{brand}^{code}",
        brand=brand,
        brand_slug=brand_slug,
        code=code,
        code_norm=normalize_code(code),
        oem_code=oem,
        oem_code_norm=normalize_code(oem) if oem else None,
        name=name,
        category=category,
        supplier_price=Decimal(price),
        unit=unit,
        qty=qty,
        min_pack=min_pack,
        import_id=1,
    )


def make_sessionmaker(seed: bool):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        if not seed:
            return
        async with factory() as session:
            session.add(
                PriceListImport(
                    status="done", source="x.zip", warehouse=WAREHOUSE, rows_imported=len(ITEMS),
                    started_at=datetime(2026, 10, 27, 11, 40, tzinfo=UTC), finished_at=FINISHED_AT,
                )
            )  # fmt: skip
            # более поздний неудачный импорт не меняет дату актуальности прайса
            session.add(PriceListImport(status="failed", source="y.zip", warehouse=WAREHOUSE))
            session.add_all([make_item(*row) for row in ITEMS])
            # марка автомобиля с главной, которой нет среди брендов прайса
            session.add(Brand(name="Audi", slug="audi", is_popular=True))
            await session.commit()

    asyncio.run(prepare())
    return factory


def make_client(factory) -> TestClient:
    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    # без with: фоновые циклы приложения (импорт прайса, отправка заказов) не запускаются
    return TestClient(app)


@pytest.fixture
def client():
    yield make_client(make_sessionmaker(seed=True))
    app.dependency_overrides.clear()


@pytest.fixture
def empty_client():
    yield make_client(make_sessionmaker(seed=False))
    app.dependency_overrides.clear()


def get_ok(client: TestClient, url: str, **params) -> str:
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.text


def codes(html: str) -> set[str]:
    """Артикулы строк таблицы на странице."""
    return {row[2] for row in ITEMS if f'<div class="offer-code">{row[2]}</div>' in html}


def test_overview_shows_categories_brands_and_price_date(client):
    html = get_ok(client, "/catalog")
    assert "Каталог запчастей" in html
    for slug, count in (
        ("filters", "3 позиции"),
        ("suspension", "2 позиции"),
        ("oils", "1 позиция"),
    ):
        assert f'href="/catalog/category/{slug}"' in html
        assert CATEGORY_BY_SLUG[slug].title in html
        assert count in html
    assert 'href="/catalog/category/engine"' not in html  # пустые категории не показываются
    assert "8 позиций в прайсе · 6 брендов" in html
    assert f"Склад {WAREHOUSE}. Загружено {date(FINISHED_AT, '%d.%m.%Y %H:%M')}" in html
    assert 'href="/catalog/brand/mann-filter">MANN-FILTER <span class="count">2</span>' in html
    assert 'href="/catalog/brand/delo-tekhniki">Дело Техники' in html
    # группы брендов: цифры, затем латиница, затем кириллица
    letters = ["0–9", "K", "L", "M", "T", "Д"]
    positions = [html.index(f'<div class="brand-letter-title">{x}</div>') for x in letters]
    assert positions == sorted(positions)
    assert 'action="/catalog/search"' in html


def test_overview_without_price_list(empty_client):
    html = get_ok(empty_client, "/catalog")
    assert "Прайс склада ещё не загружен" in html
    assert "/catalog/category/" not in html and "обновлён" not in html


def test_old_category_link_from_home_opens_category(client):
    response = client.get("/catalog", params={"category": "brakes"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/catalog/category/brakes"
    assert client.get("/catalog", params={"category": "tecdoc"}).status_code == 200


def test_category_page_filters_by_brand_and_query(client):
    html = get_ok(client, "/catalog/category/filters")
    assert CATEGORY_BY_SLUG["filters"].title in html
    assert (
        codes(html) == {"W 712/75", "C 25 114", "90915-YZZE1"}
        and html.count('class="catalog-name"') == 3
    )
    assert "Все бренды (3)" in html
    assert '<option value="mann-filter">MANN-FILTER (2)</option>' in html
    assert '<option value="toyota">Toyota (1)</option>' in html
    assert "kayaba" not in html  # бренды других категорий в списке не показываются

    html = get_ok(client, "/catalog/category/filters", brand="mann-filter")
    assert '<option value="mann-filter" selected>' in html
    assert html.count('class="catalog-name"') == 2 and "90915-YZZE1" not in html

    html = get_ok(client, "/catalog/category/filters", brand="mann-filter", q="bmw")
    assert "C 25 114" in html and "W 712/75" not in html
    assert 'value="bmw"' in html and "Сбросить" in html

    html = get_ok(client, "/catalog/category/filters", q="nothing-like-this")
    assert "В этой категории ничего не найдено" in html


def test_unknown_category_is_404(client):
    response = client.get("/catalog/category/spaceships")
    assert response.status_code == 404
    assert "Категория не найдена" in response.text


def test_brand_page_with_category_chips(client):
    html = get_ok(client, "/catalog/brand/toyota")
    assert '<h1 class="page-title">Toyota</h1>' in html
    assert html.count('class="catalog-name"') == 2
    filters_chip = 'href="/catalog/brand/toyota?category=filters">Фильтры <span class="count">1'
    brakes_chip = f'?category=brakes">{CATEGORY_BY_SLUG["brakes"].title} <span class="count">1'
    assert filters_chip in html and brakes_chip in html
    assert html.index(filters_chip) < html.index(brakes_chip)  # порядок CATEGORIES
    assert 'class="chip active" href="/catalog/brand/toyota">Все категории' in html

    html = get_ok(client, "/catalog/brand/toyota", category="brakes")
    assert "04465-33471" in html and "90915-YZZE1" not in html
    assert '<input type="hidden" name="category" value="brakes">' in html

    html = get_ok(client, "/catalog/brand/toyota", q="camry")
    assert "04465-33471" in html and "90915-YZZE1" not in html
    assert 'href="/catalog/brand/toyota?category=filters&amp;q=camry"' in html

    # неизвестная категория в адресе — показываем все позиции бренда
    html = get_ok(client, "/catalog/brand/toyota", category="nope")
    assert html.count('class="catalog-name"') == 2


def test_unknown_brand_is_404(client):
    response = client.get("/catalog/brand/no-such-brand")
    assert response.status_code == 404
    assert "Бренд не найден" in response.text


def test_search_by_oem_name_and_code(client):
    html = get_ok(client, "/catalog/search", q="E6210JD02A")  # OEM без разделителей
    assert "349079" in html and "OEM E6210-JD02A" in html
    assert html.count('class="catalog-name"') == 1

    html = get_ok(client, "/catalog/search", q="масляный")
    assert {"W 712/75", "90915-YZZE1"} <= {row[2] for row in ITEMS if row[2] in html}
    assert html.count('class="catalog-name"') == 2

    html = get_ok(client, "/catalog/search", q="04465")
    assert "04465-33471" in html and "Найдено: <b>1</b> позиция" in html

    html = get_ok(client, "/catalog/search", q="no-such-part")
    assert "ничего не найдено" in html
    assert 'href="/search?q=no-such-part&amp;mode=article"' in html


def test_search_without_query_redirects_to_catalog(client):
    for params in ({}, {"q": "   "}):
        response = client.get("/catalog/search", params=params, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/catalog"


def test_rows_show_client_price_stock_supplier_and_buy_link(client):
    html = get_ok(client, "/catalog/brand/kayaba")
    expected = PricePolicy(markup_percent=Decimal(15)).client_price(Decimal("7064.47"))
    assert expected == Decimal("8124.14")
    assert f'<td class="price">{money(expected)}</td>' in html
    assert money(Decimal("7064.47")) not in html and "7064" not in html  # закупочной цены нет
    assert SUPPLIER in html
    assert "По прайсу: 2 шт." in html
    assert (
        'href="/search?q=349079&amp;mode=article&amp;refresh=1"' in html
        and ">Цены и сроки</a>" in html
    )
    assert 'href="/catalog/brand/kayaba">KAYABA</a>' in html

    html = get_ok(client, "/catalog/category/oils")
    assert "По прайсу: больше 10 шт." in html and "кратно 4" in html
    assert money(Decimal("6773.50")) in html and money(Decimal("5890.00")) not in html

    html = get_ok(client, "/catalog/brand/delo-tekhniki")
    assert "По прайсу: 6 шт." in html  # «штука» из прайса показываем как «шт.»

    html = get_ok(client, "/catalog/search", q="W 712/75")
    assert 'href="/search?q=W%20712/75&amp;mode=article&amp;refresh=1"' in html


def test_pagination_links_keep_query_params(client):
    html = get_ok(client, "/catalog/category/filters", per_page=1)
    assert "Найдено: <b>3</b> позиции" in html and "страница 1 из 3" in html
    assert html.count('class="catalog-name"') == 1
    assert '<span class="pager-link disabled">← Назад</span>' in html
    assert '<span class="pager-link current" aria-current="page">1</span>' in html
    assert 'href="/catalog/category/filters?per_page=1&amp;page=3">3</a>' in html
    assert 'href="/catalog/category/filters?per_page=1&amp;page=2" rel="next">Вперёд' in html

    html = get_ok(
        client, "/catalog/brand/mann-filter", per_page=1, category="filters", q="", page=2
    )
    assert "страница 2 из 2" in html
    # первая страница — без page, пустой q отбрасывается, остальные параметры сохраняются
    assert 'href="/catalog/brand/mann-filter?per_page=1&amp;category=filters" rel="prev">' in html
    assert '<span class="pager-link disabled">Вперёд →</span>' in html

    # номер больше последней страницы — показываем последнюю
    html = get_ok(client, "/catalog/search", q="масляный", per_page=1, page=99)
    assert "страница 2 из 2" in html

    html = get_ok(client, "/catalog/category/filters")
    assert 'class="pager"' not in html  # одна страница — без пагинации
    assert client.get("/catalog/category/filters", params={"per_page": 1000}).status_code == 422


def test_legacy_catalog_slug(client):
    response = client.get("/catalog/toyota", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/catalog/brand/toyota"

    response = client.get("/catalog/audi")  # марка с главной, запчастей бренда нет
    assert response.status_code == 404
    assert 'href="/catalog/search?q=Audi"' in response.text

    response = client.get("/catalog/unknown")
    assert response.status_code == 404 and "Бренд не найден" in response.text


def test_page_numbers_compact():
    assert page_numbers(1, 1) == [1]
    assert page_numbers(1, 4) == [1, 2, 3, 4]
    assert page_numbers(1, 10) == [1, 2, 3, None, 10]
    assert page_numbers(5, 10) == [1, 2, 3, 4, 5, 6, 7, None, 10]
    assert page_numbers(6, 20) == [1, None, 4, 5, 6, 7, 8, None, 20]
    assert page_numbers(20, 20) == [1, None, 18, 19, 20]


def test_group_brands_digits_latin_cyrillic():
    names = ["4U", "555", "ABS", "febi", "Zekkert", "АВТОВАЗ", "Ёлка", "Дело Техники", "(Б/У)"]
    groups = group_brands([BrandCount(n, n.lower(), 1) for n in names])
    assert [(g.letter, [b.name for b in g.brands]) for g in groups] == [
        ("0–9", ["4U", "555"]),
        ("A", ["ABS"]),
        ("F", ["febi"]),
        ("Z", ["Zekkert"]),
        ("А", ["АВТОВАЗ"]),
        ("Б", ["(Б/У)"]),  # первая буква после скобки
        ("Д", ["Дело Техники"]),
        ("Е", ["Ёлка"]),
    ]


def test_stock_text():
    assert stock_text(10, "шт.") == "По прайсу: 10 шт."
    assert stock_text(11, "комп.") == "По прайсу: больше 10 комп."
    assert stock_text(3, "штука") == "По прайсу: 3 шт."
    assert stock_text(1, "") == "По прайсу: 1 шт."


async def test_list_categories_for_brand():
    async with sqlite_sessionmaker() as factory, factory() as session:
        session.add_all([make_item(*row) for row in ITEMS])
        await session.commit()
        result = await catalog.list_categories(session, brand_slug="toyota")
    assert [(c.slug, c.count) for c in result] == [("filters", 1), ("brakes", 1)]


def test_home_links_to_imported_brands_and_categories(client):
    html = get_ok(client, "/")
    assert "Бренды запчастей" in html
    assert 'href="/catalog/brand/mann-filter"' in html
    assert 'href="/catalog/category/filters"' in html
    assert 'href="/catalog/audi"' not in html


def test_search_multiple_words_and_brand(client):
    for q in ("Колодки camry", "camry Колодки", "KAYABA NISSAN"):
        html = get_ok(client, "/catalog/search", q=q)
        assert html.count('class="catalog-name"') == 1
    assert "Прайс старше суток" in get_ok(client, "/catalog")


def test_source_date_survives_local_reimport():
    record = PriceListImport(
        finished_at=datetime(2026, 10, 30, tzinfo=UTC),
        file_name="PriceAE_TEST_2026-10-27_14-42-24.csv",
    )
    assert record.source_updated_at.year == 2026
    assert record.source_updated_at.day == 27
    assert record.source_updated_at.hour == 14
    record.last_modified = "Tue, 27 Oct 2026 11:42:24 GMT"
    assert record.source_updated_at.hour == 11
    record.last_modified = "invalid"
    assert record.source_updated_at.day == 27
