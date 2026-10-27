"""PricingService и dedupe_offers: дедупликация, сортировка, наценка при выдаче, обработка ошибок.

База данных заменена простой заглушкой сессии: кэш всегда пуст, записи накапливаются в списке.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import PriceOffer
from app.services.pricing.base import BrandMatch, Offer, PriceProviderError
from app.services.pricing.policy import PricePolicy
from app.services.pricing.service import EMPTY_MARKER, PricingService, dedupe_offers
from tests.helpers import dt, make_offer

# --- dedupe_offers -----------------------------------------------------------------------------

KEY = "FOMF9qs9IJBF1Ft2uczKE6romQoUlfwY2oikQNusp5R2ABQWneoasSTNKNvsm13ERk2udfrJSWOQAVe6IOcreC7"


def test_same_offer_key_across_brands_collapses_to_one():
    toyota = make_offer(brand="TOYOTA", cross_type=None, offer_key=KEY, price=2037.38)
    via_daihatsu = make_offer(brand="TOYOTA", cross_type=2, offer_key=KEY, price=2037.38)
    assert dedupe_offers([toyota, via_daihatsu]) == [toyota]


def test_original_wins_over_earlier_analog_copy():
    analog_copy = make_offer(cross_type=0, offer_key=KEY)
    original = make_offer(cross_type=None, offer_key=KEY)
    result = dedupe_offers([analog_copy, original])
    assert result == [original]
    assert result[0].cross_type is None


def test_first_original_is_kept_when_duplicated():
    first = make_offer(cross_type=None, offer_key=KEY, amount=8)
    second = make_offer(cross_type=None, offer_key=KEY, amount=5)
    assert dedupe_offers([first, second]) == [first]


def test_first_analog_is_kept_when_both_are_analogs():
    cross0 = make_offer(cross_type=0, offer_key=KEY)
    cross3 = make_offer(cross_type=3, offer_key=KEY)
    assert dedupe_offers([cross0, cross3]) == [cross0]
    assert dedupe_offers([cross3, cross0]) == [cross3]


def test_replacement_keeps_position_of_first_occurrence():
    a_copy = make_offer(cross_type=0, offer_key="A")
    b = make_offer(cross_type=None, offer_key="B")
    a = make_offer(cross_type=None, offer_key="A")
    assert dedupe_offers([a_copy, b, a]) == [a, b]


def test_without_offer_key_identity_is_by_fields():
    base = {"offer_key": None, "brand": "TOYOTA", "code": "0446533471", "supplier": "Партнёр Авто-Евро",
            "price": 2037.38, "delivery_time": dt(31), "amount": 8}
    twin = make_offer(**base, cross_type=2)
    original = make_offer(**base, cross_type=None)
    other_price = make_offer(**{**base, "price": 2100.0})
    other_date = make_offer(**{**base, "delivery_time": dt(30)})
    other_supplier = make_offer(**{**base, "supplier": "Склад Авто-Евро"})
    other_amount = make_offer(**{**base, "amount": 1})

    result = dedupe_offers([twin, original, other_price, other_date, other_supplier, other_amount])
    assert result == [original, other_price, other_date, other_supplier, other_amount]


def test_offers_with_and_without_key_never_collide():
    keyed = make_offer(offer_key="K")
    unkeyed = make_offer(offer_key=None)
    assert dedupe_offers([keyed, unkeyed]) == [keyed, unkeyed]


def test_dedupe_empty():
    assert dedupe_offers([]) == []


def test_dedupe_realistic_overlap_between_brands():
    """search_items по TOYOTA и по DAIHATSU возвращают пересекающиеся offer_key."""
    toyota_rows = [
        make_offer(brand="TOYOTA", cross_type=None, offer_key="T1"),
        make_offer(brand="NISSHINBO", code="PF-1521", cross_type=0, offer_key="N1"),
        make_offer(brand="DAIHATSU", cross_type=2, offer_key="D1"),
    ]
    daihatsu_rows = [
        make_offer(brand="DAIHATSU", cross_type=None, offer_key="D1"),
        make_offer(brand="TOYOTA", cross_type=2, offer_key="T1"),
        make_offer(brand="NISSHINBO", code="PF-1521", cross_type=0, offer_key="N1"),
    ]
    result = dedupe_offers(toyota_rows + daihatsu_rows)
    assert [o.offer_key for o in result] == ["T1", "N1", "D1"]
    assert [o.cross_type for o in result] == [None, 0, None]


# --- PricingService без базы -----------------------------------------------------------------


class FakeSession:
    """Заглушка AsyncSession: кэш пуст, сохранённые строки копятся в added."""

    def __init__(self) -> None:
        self.added: list[PriceOffer] = []
        self.executed: list = []
        self.commits = 0

    async def scalar(self, stmt):
        return None

    async def scalars(self, stmt):
        raise AssertionError("при пустом кэше строки не читаются")

    async def execute(self, stmt):
        self.executed.append(stmt)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


class FakeProvider:
    name = "fake"

    def __init__(self, brands: list[BrandMatch], items: dict[str, list[Offer] | Exception]):
        self.brands = brands
        self.items = items
        self.brand_queries: list[str] = []
        self.item_queries: list[tuple[str, str]] = []

    async def search_brands(self, code: str) -> list[BrandMatch]:
        self.brand_queries.append(code)
        return self.brands

    async def search_items(self, brand: str, code: str, with_crosses: bool = True) -> list[Offer]:
        self.item_queries.append((brand, code))
        result = self.items[brand]
        if isinstance(result, Exception):
            raise result
        return result


BRANDS = [BrandMatch(brand="TOYOTA", code="0446533471"), BrandMatch(brand="DAIHATSU", code="0446533471")]


def make_service(items: dict, policy: PricePolicy | None = None, brands=BRANDS) -> tuple[PricingService, FakeProvider]:
    provider = FakeProvider(brands, items)
    service = PricingService(provider=provider, cache_ttl=timedelta(minutes=30), policy=policy or PricePolicy())
    return service, provider


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


async def test_search_by_code_normalizes_code_and_queries_each_brand(session):
    service, provider = make_service({"TOYOTA": [], "DAIHATSU": []})
    assert await service.search_by_code(session, " 04465-33471 ") == []
    assert provider.brand_queries == ["0446533471"]
    assert sorted(provider.item_queries) == [("DAIHATSU", "0446533471"), ("TOYOTA", "0446533471")]


async def test_empty_or_punctuation_code_does_not_call_provider(session):
    service, provider = make_service({})
    assert await service.search_by_code(session, "  ---  ") == []
    assert provider.brand_queries == []
    assert session.added == []


async def test_supplier_offers_are_deduped_and_sorted(session):
    toyota = [
        make_offer(brand="TOYOTA", cross_type=None, offer_key="T-partner", price=2037.38, is_stock=False),
        make_offer(brand="TOYOTA", cross_type=None, offer_key="T-stock", price=2500.0, is_stock=True),
        make_offer(brand="NISSHINBO", code="PF-1521", cross_type=0, offer_key="N1", price=9250.05),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, offer_key="S1", price=1395.47, is_stock=True),
    ]
    daihatsu = [
        make_offer(brand="DAIHATSU", cross_type=None, offer_key="D1", price=19391.28),
        make_offer(brand="TOYOTA", cross_type=2, offer_key="T-partner", price=2037.38),  # дубль
        make_offer(brand="TOYOTA", cross_type=2, offer_key="T-cheap", price=1900.0),  # аналог
    ]
    service, _ = make_service({"TOYOTA": toyota, "DAIHATSU": daihatsu})

    offers = await service.search_supplier_offers(session, "0446533471")

    assert [o.offer_key for o in offers] == ["T-stock", "T-partner", "D1", "S1", "T-cheap", "N1"]
    assert all(o.supplier_price is None for o in offers)  # без наценки
    assert offers[1].price == 2037.38


async def test_search_by_code_applies_markup_but_caches_supplier_prices(session):
    toyota = [make_offer(cross_type=None, offer_key="T1", price=2037.38, delivery_time=dt(31))]
    service, _ = make_service({"TOYOTA": toyota, "DAIHATSU": []})

    offers = await service.search_by_code(session, "0446533471")

    assert len(offers) == 1
    assert offers[0].price == 2342.99
    assert offers[0].supplier_price == 2037.38
    assert offers[0].delivery_time == dt(1, month=11)

    assert session.commits == 1
    assert len(session.executed) == 1  # удаление старого кэша
    (row,) = session.added
    assert isinstance(row, PriceOffer)
    assert row.provider == "fake"
    assert row.query_code == "0446533471"
    assert row.price == Decimal("2037.38")  # в кэше цена поставщика
    assert row.delivery_time == dt(31)
    assert row.offer_key == "T1"


async def test_cache_stores_empty_marker_when_no_brands(session):
    service, provider = make_service({}, brands=[])
    assert await service.search_by_code(session, "ZZZ999") == []
    assert provider.item_queries == []
    (row,) = session.added
    assert row.offer_key == EMPTY_MARKER
    assert row.query_code == "ZZZ999"
    assert session.commits == 1


async def test_cache_stores_empty_marker_when_brands_have_no_offers(session):
    service, _ = make_service({"TOYOTA": [], "DAIHATSU": []})
    assert await service.search_by_code(session, "0446533471") == []
    assert [r.offer_key for r in session.added] == [EMPTY_MARKER]


async def test_partial_brand_failure_returns_remaining_offers(session, caplog):
    toyota = [make_offer(cross_type=None, offer_key="T1", price=100.0)]
    service, _ = make_service({"TOYOTA": toyota, "DAIHATSU": PriceProviderError("autoeuro: сбой", code=500)})
    offers = await service.search_by_code(session, "0446533471")
    assert [o.offer_key for o in offers] == ["T1"]
    assert offers[0].price == 115.0
    assert "Ошибка цен для DAIHATSU" in caplog.text
    assert [r.offer_key for r in session.added] == ["T1"]


async def test_all_brands_failing_raises_first_provider_error_and_keeps_cache(session):
    first = PriceProviderError("autoeuro: лимит", code=429)
    service, _ = make_service({"TOYOTA": first, "DAIHATSU": PriceProviderError("другая")})
    with pytest.raises(PriceProviderError) as info:
        await service.search_by_code(session, "0446533471")
    assert info.value is first
    assert info.value.code == 429
    assert session.added == [] and session.commits == 0 and session.executed == []


async def test_all_brands_failing_with_unexpected_error_is_wrapped(session):
    service, _ = make_service({"TOYOTA": RuntimeError("boom"), "DAIHATSU": ValueError("bad")})
    with pytest.raises(PriceProviderError, match="Источник цен не ответил: boom"):
        await service.search_by_code(session, "0446533471")


async def test_force_refresh_skips_cache_lookup(session):
    calls = []

    async def scalar(stmt):
        calls.append(stmt)

    session.scalar = scalar
    service, _ = make_service({"TOYOTA": [], "DAIHATSU": []})
    await service.search_by_code(session, "0446533471", force_refresh=True)
    assert calls == []
    await service.search_by_code(session, "0446533471")
    assert len(calls) == 1


def test_row_to_offer_round_trip():
    offer = make_offer(
        cross_type=3, offer_key="K", price=1395.47, rejects=23.41, delivery_time=dt(31),
        supplier="Официальный дилер через Авто-Евро", warehouse="Воронеж", is_dealer=True, is_stock=True,
        packing=2, unit="комп.", raw={"ttl": 1},
    )
    row = PriceOffer(
        provider="autoeuro", query_code="0446533471", brand=offer.brand, code=offer.code, name=offer.name,
        price=Decimal("1395.47"), currency="RUB", amount=offer.amount, is_stock=True, is_dealer=True,
        supplier=offer.supplier, warehouse=offer.warehouse, cross_type=3, delivery_time=dt(31),
        delivery_time_max=None, order_before=None, rejects=Decimal("23.41"), packing=2, unit="комп.",
        offer_key="K", raw={"ttl": 1},
    )
    restored = PricingService._row_to_offer(row)
    assert restored == offer
    assert isinstance(restored.price, float) and isinstance(restored.rejects, float)
