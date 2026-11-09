"""Слой запросов каталога: категории, бренды, поиск с пагинацией, последний импорт."""

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import PriceListImport
from app.services.pricelist import catalog
from app.services.pricelist.categories import CATEGORIES, CATEGORY_BY_SLUG, OTHER_SLUG
from app.services.pricelist.importer import replace_items
from app.services.pricelist.slug import slugify
from tests.pricelist_samples import WAREHOUSE, make_row, sqlite_sessionmaker

NAMES = {
    "KAYABA^349079": "Амортизатор зад.NISSAN QASHQAI J10E 06> (газ.)",
    "MANN-FILTER^W 712/75": "Фильтр масляный OPEL ASTRA G/H, CHEVROLET LACETTI",
    "MANN-FILTER^C 25 114": "Фильтр воздушный BMW 3 (E46)",
    "Hyundai/Kia^26300-35503": "Фильтр масляный HYUNDAI/KIA (оригинал)",
    "ELRING^530.571": "Прокладка ГБЦ BMW N47 (комплект)",
    "LIQUI MOLY^7516": "Масло моторное 5W-30 Molygen New Generation 4л",
    "Toyota^04465-33471": "Колодки торм.TOYOTA Camry (2006>)",
}
OEM = {"KAYABA^349079": "E6210JD02A", "Hyundai/Kia^26300-35503": "2630035503"}
# категории заданы явно: здесь проверяется слой запросов, а не правила assign_category
CATEGORY = {
    "KAYABA^349079": "suspension",
    "MANN-FILTER^W 712/75": "filters",
    "MANN-FILTER^C 25 114": "filters",
    "Hyundai/Kia^26300-35503": "filters",
    "ELRING^530.571": "engine",
    "LIQUI MOLY^7516": "oils",
    "Toyota^04465-33471": "brakes",
}


def rows():
    result = []
    for key, name in NAMES.items():
        brand, _, code = key.partition("^")
        result.append(
            make_row(
                item_key=key,
                brand=brand,
                brand_slug=slugify(brand),
                code=code,
                oem_code=OEM.get(key),
                name=name,
                category=CATEGORY[key],
                supplier_price=Decimal("100.00"),
            )
        )
    return result


@pytest.fixture
async def db_sessionmaker():
    async with sqlite_sessionmaker() as factory:
        yield factory


@pytest.fixture
async def session(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add_all(
            [
                PriceListImport(status="failed", source="x.zip", warehouse=WAREHOUSE),
                PriceListImport(
                    status="done", source="a.zip", warehouse=WAREHOUSE, rows_imported=7,
                    finished_at=datetime(2026, 10, 27, 12, 0, tzinfo=UTC),
                ),
                PriceListImport(status="running", source="b.zip", warehouse=WAREHOUSE),
            ]
        )  # fmt: skip
        await s.commit()
        await replace_items(s, rows(), import_id=2, warehouse=WAREHOUSE)
        yield s


async def test_list_categories_in_order_without_empty(session):
    expected = Counter(CATEGORY.values())
    result = await catalog.list_categories(session)
    assert [c.slug for c in result] == [c.slug for c in CATEGORIES if expected[c.slug]]
    assert {c.slug: c.count for c in result} == dict(expected)
    assert all(c.title and c.icon for c in result)
    filters = next(c for c in result if c.slug == "filters")
    assert filters.count == 3
    assert (filters.title, filters.icon) == (
        CATEGORY_BY_SLUG["filters"].title,
        CATEGORY_BY_SLUG["filters"].icon,
    )


async def test_list_categories_other_is_last(session, monkeypatch):
    other = make_row(item_key="X^1", code="1", brand="X", brand_slug="x", category=OTHER_SLUG)
    await replace_items(session, [other], import_id=None, warehouse="Москва")
    # даже если «Прочее» окажется не в конце списка правил, на странице она последняя
    reordered = [CATEGORY_BY_SLUG[OTHER_SLUG]] + [c for c in CATEGORIES if c.slug != OTHER_SLUG]
    monkeypatch.setattr(catalog, "CATEGORIES", reordered)
    result = await catalog.list_categories(session)
    assert result[-1] == catalog.CategoryCount(
        OTHER_SLUG, CATEGORY_BY_SLUG[OTHER_SLUG].title, CATEGORY_BY_SLUG[OTHER_SLUG].icon, 1
    )
    assert len(result) == len(set(CATEGORY.values())) + 1


async def test_list_categories_empty_table(db_sessionmaker):
    async with db_sessionmaker() as session:
        assert await catalog.list_categories(session) == []
        assert await catalog.list_brands(session) == []
        assert await catalog.last_import(session) is None
        page = await catalog.search_items(session, q="x")
    assert (page.items, page.total, page.page, page.pages) == ([], 0, 1, 1)


async def test_list_brands_alphabetical_with_counts(session):
    brands = await catalog.list_brands(session)
    assert [(b.name, b.slug, b.count) for b in brands] == [
        ("ELRING", "elring", 1),
        ("Hyundai/Kia", "hyundai-kia", 1),
        ("KAYABA", "kayaba", 1),
        ("LIQUI MOLY", "liqui-moly", 1),
        ("MANN-FILTER", "mann-filter", 2),
        ("Toyota", "toyota", 1),
    ]


async def test_list_brands_order_is_case_insensitive_latin_then_cyrillic(session):
    extra = [
        make_row(
            item_key="Дело Техники^1", code="1", brand="Дело Техники", brand_slug="delo-tekhniki"
        ),
        make_row(item_key="febi^2", code="2", brand="febi", brand_slug="febi"),
        make_row(item_key="АТВ^3", code="3", brand="АТВ", brand_slug="atv"),
    ]
    await replace_items(session, extra, import_id=None, warehouse="Москва")
    names = [b.name for b in await catalog.list_brands(session)]
    assert names == [
        "ELRING", "febi", "Hyundai/Kia", "KAYABA", "LIQUI MOLY", "MANN-FILTER", "Toyota",
        "АТВ", "Дело Техники",
    ]  # fmt: skip


async def test_list_brands_filtered_by_category_and_query(session):
    by_category = await catalog.list_brands(session, category="filters")
    assert [(b.slug, b.count) for b in by_category] == [("hyundai-kia", 1), ("mann-filter", 2)]
    by_q = await catalog.list_brands(session, q="масляный")
    assert [b.slug for b in by_q] == ["hyundai-kia", "mann-filter"]
    assert await catalog.list_brands(session, category="filters", q="BMW") == [
        catalog.BrandCount("MANN-FILTER", "mann-filter", 1)
    ]


async def test_get_brand(session):
    assert await catalog.get_brand(session, "mann-filter") == catalog.BrandCount(
        "MANN-FILTER", "mann-filter", 2
    )
    assert (await catalog.get_brand(session, "hyundai-kia")).name == "Hyundai/Kia"
    assert await catalog.get_brand(session, "nope") is None


async def test_search_items_by_brand_and_category(session):
    page = await catalog.search_items(session, brand_slug="mann-filter")
    assert [i.item_key for i in page.items] == ["MANN-FILTER^C 25 114", "MANN-FILTER^W 712/75"]
    assert page.total == 2 and page.pages == 1 and page.per_page == 50
    page = await catalog.search_items(session, brand_slug="mann-filter", category="filters")
    assert page.total == 2
    page = await catalog.search_items(session, brand_slug="mann-filter", category="oils")
    assert page.total == 0 and page.items == []


async def test_search_items_query_name_code_oem(session):
    assert await _keys(session, q="масляный") == [
        "Hyundai/Kia^26300-35503", "MANN-FILTER^W 712/75",
    ]  # fmt: skip
    assert await _keys(session, q="camry") == ["Toyota^04465-33471"]  # регистр латиницы
    assert await _keys(session, q="w712/75") == ["MANN-FILTER^W 712/75"]  # артикул без разделителей
    assert await _keys(session, q="0446533471") == ["Toyota^04465-33471"]
    assert await _keys(session, q="e6210-jd02a") == ["KAYABA^349079"]  # по OEM
    assert await _keys(session, q="  2630035503 ") == ["Hyundai/Kia^26300-35503"]
    assert await _keys(session, q="BMW", brand_slug="elring") == ["ELRING^530.571"]
    assert await _keys(session, q="   ") == await _keys(session)  # пустой запрос — всё


async def test_search_items_escapes_like_wildcards(session):
    assert await _keys(session, q="%") == []
    assert await _keys(session, q="_") == []
    assert await _keys(session, q="100%") == []
    assert await _keys(session, q="Фильтр%масляный") == []
    assert await _keys(session, q="W 712/75") == ["MANN-FILTER^W 712/75"]


async def test_search_items_sorted_by_brand_and_name(session):
    page = await catalog.search_items(session)
    assert [(i.brand, i.name) for i in page.items] == sorted((i.brand, i.name) for i in page.items)
    assert page.items[0].brand == "ELRING"


async def test_search_items_pagination(session):
    page = await catalog.search_items(session, per_page=3)
    assert (page.total, page.pages, page.page, page.per_page) == (7, 3, 1, 3)
    assert len(page.items) == 3
    last = await catalog.search_items(session, page=3, per_page=3)
    assert len(last.items) == 1 and last.page == 3
    beyond = await catalog.search_items(session, page=99, per_page=3)
    assert beyond.page == 3 and [i.id for i in beyond.items] == [i.id for i in last.items]
    assert (await catalog.search_items(session, page=0, per_page=3)).page == 1
    assert (await catalog.search_items(session, per_page=1000)).per_page == 200
    assert (await catalog.search_items(session, per_page=0)).per_page == 1
    all_keys = []
    for number in (1, 2, 3):
        all_keys += [
            i.item_key for i in (await catalog.search_items(session, page=number, per_page=3)).items
        ]
    assert sorted(all_keys) == sorted(NAMES)  # страницы не пересекаются и покрывают всё


async def test_last_import_is_latest_done(session):
    record = await catalog.last_import(session)
    assert record is not None and record.id == 2 and record.status == "done"
    assert record.rows_imported == 7


async def test_items_by_code(session):
    items = await catalog.items_by_code(session, "w-712/75")
    assert [i.item_key for i in items] == ["MANN-FILTER^W 712/75"]
    assert await catalog.items_by_code(session, "nope") == []
    assert await catalog.items_by_code(session, "---") == []


async def _keys(session, **kwargs) -> list[str]:
    return [i.item_key for i in (await catalog.search_items(session, **kwargs)).items]
