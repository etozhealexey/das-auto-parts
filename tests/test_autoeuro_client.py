"""AutoEuroProvider поверх httpx.MockTransport: протокол запросов, ошибки, выбор точки получения."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.pricing.autoeuro import AutoEuroProvider
from app.services.pricing.base import PriceProviderError
from tests.fake_autoeuro import (
    DELIVERY_EKATERINBURG,
    DELIVERY_SAMARA,
    DELIVERY_VORONEZH,
    FakeAutoEuro,
)
from tests.helpers import MSK

API_KEY = "i1ah-test-key-not-real"
BASE_URL = "https://api.autoeuro.test/api/v2/json"


@pytest.fixture
async def provider(fake_api: FakeAutoEuro):
    p = AutoEuroProvider(api_key=API_KEY, base_url=BASE_URL, transport=fake_api.transport)
    yield p
    await p.aclose()


def test_empty_api_key_is_rejected_at_construction(fake_api):
    with pytest.raises(PriceProviderError, match="AUTOEURO_API_KEY"):
        AutoEuroProvider(api_key="", transport=fake_api.transport)


# --- протокол запросов --------------------------------------------------------------------


async def test_key_is_sent_in_header_on_every_request(provider, fake_api):
    await provider.get_balance()
    await provider.search_brands("0446533471")
    await provider.search_items("TOYOTA", "0446533471")
    assert len(fake_api.requests) == 4  # balance, brands, deliveries (внутри search_items), items
    for request in fake_api.requests:
        assert request.headers["key"] == API_KEY
        assert request.method == "GET"
    # ключ не утекает в URL
    assert all(API_KEY not in str(r.url) for r in fake_api.requests)


async def test_action_url_layout_and_trailing_slash(fake_api):
    p = AutoEuroProvider(api_key=API_KEY, base_url=BASE_URL + "/", transport=fake_api.transport)
    await p.get_deliveries()
    assert str(fake_api.requests[0].url) == BASE_URL + "/get_deliveries/"
    await p.aclose()


async def test_search_items_params(provider, fake_api):
    await provider.search_items("TOYOTA", "0446533471")
    (request,) = fake_api.calls("search_items")
    params = request.url.params
    assert params["brand"] == "TOYOTA"
    assert params["code"] == "0446533471"
    assert params["delivery_key"] == DELIVERY_VORONEZH["delivery_key"]
    assert params["with_crosses"] == "1"
    assert params["with_offers"] == "1"

    await provider.search_items("TOYOTA", "0446533471", with_crosses=False)
    assert fake_api.calls("search_items")[1].url.params["with_crosses"] == "0"


async def test_search_brands_params_and_parsing(provider, fake_api):
    brands = await provider.search_brands("0446533471")
    (request,) = fake_api.calls("search_brands")
    assert request.url.params["code"] == "0446533471"
    assert [(b.brand, b.code, b.name) for b in brands] == [
        ("TOYOTA", "0446533471", "Колодки торм.TOYOTA CAMRY (2006>)"),
        ("DAIHATSU", "0446533471", "Колодки тормозные"),
    ]


async def test_search_brands_skips_rows_without_brand_and_falls_back_to_query_code(provider, fake_api):
    fake_api.data["search_brands"] = [
        {"brand": "", "code": "X"},
        "garbage",
        {"brand": "TOYOTA"},
        {"brand": "DENSO", "code": None},
    ]
    brands = await provider.search_brands("0446533471")
    assert [(b.brand, b.code) for b in brands] == [("TOYOTA", "0446533471"), ("DENSO", "0446533471")]


async def test_search_items_parses_rows_and_skips_null_price(provider, fake_api):
    offers = await provider.search_items("TOYOTA", "0446533471")
    # из пяти строк образца одна (price null) отбрасывается
    assert [o.brand for o in offers] == ["TOYOTA", "NISSHINBO", "SANGSIN", "DAIHATSU"]
    assert [o.cross_type for o in offers] == [None, 0, 3, 2]
    assert offers[0].price == 2037.38
    assert offers[0].delivery_time == datetime(2026, 10, 31, 9, 0, tzinfo=MSK)


async def test_search_items_ignores_non_dict_rows_and_empty_data(provider, fake_api):
    fake_api.data["search_items"] = ["oops", None, 42]
    assert await provider.search_items("TOYOTA", "0446533471") == []
    fake_api.data["search_items"] = None
    assert await provider.search_items("TOYOTA", "0446533471") == []


# --- ошибки ---------------------------------------------------------------------------------


async def test_error_payload_raises_provider_error_with_code(provider, fake_api):
    fake_api.errors["search_brands"] = {"code": 403, "message": "Неверный ключ доступа"}
    with pytest.raises(PriceProviderError) as info:
        await provider.search_brands("0446533471")
    assert info.value.code == 403
    assert "Неверный ключ доступа" in str(info.value)
    assert str(info.value).startswith("autoeuro:")


async def test_error_code_as_string_is_converted(provider, fake_api):
    fake_api.errors["get_deliveries"] = {"code": "429", "message": "Превышен лимит запросов"}
    with pytest.raises(PriceProviderError) as info:
        await provider.get_deliveries()
    assert info.value.code == 429


async def test_error_as_plain_string(provider, fake_api):
    fake_api.errors["get_balance"] = "Сервис недоступен"
    with pytest.raises(PriceProviderError, match="Сервис недоступен") as info:
        await provider.get_balance()
    assert info.value.code is None


async def test_error_without_message_uses_placeholder(provider, fake_api):
    fake_api.errors["get_balance"] = {"code": 500}
    with pytest.raises(PriceProviderError, match="autoeuro: ошибка") as info:
        await provider.get_balance()
    assert info.value.code == 500


async def test_error_in_search_items_propagates(provider, fake_api):
    fake_api.errors["search_items"] = {"code": 404, "message": "Бренд не найден"}
    with pytest.raises(PriceProviderError) as info:
        await provider.search_items("TOYOTA", "0446533471")
    assert info.value.code == 404


async def test_non_json_response(provider, fake_api):
    fake_api.raw_responses["get_balance"] = httpx.Response(502, text="<html>Bad Gateway</html>")
    with pytest.raises(PriceProviderError, match="не JSON \\(HTTP 502\\)"):
        await provider.get_balance()


async def test_http_error_without_error_payload(provider, fake_api):
    fake_api.raw_responses["get_balance"] = httpx.Response(500, json={"META": {}, "DATA": []})
    with pytest.raises(PriceProviderError, match="HTTP 500"):
        await provider.get_balance()


async def test_transport_error_is_wrapped(provider, fake_api):
    fake_api.exceptions["get_deliveries"] = httpx.ConnectError("connection refused")
    with pytest.raises(PriceProviderError, match="недоступен") as info:
        await provider.get_deliveries()
    assert info.value.code is None
    assert isinstance(info.value.__cause__, httpx.ConnectError)


async def test_timeout_is_wrapped(provider, fake_api):
    fake_api.exceptions["search_items"] = httpx.ReadTimeout("timed out")
    with pytest.raises(PriceProviderError, match="недоступен"):
        await provider.search_items("TOYOTA", "0446533471")


# --- get_deliveries / get_balance -------------------------------------------------------------


async def test_get_deliveries_returns_rows_with_delivery_name(provider):
    deliveries = await provider.get_deliveries()
    assert len(deliveries) == 3
    assert deliveries[0]["delivery_name"] == "г. Воронеж (ул. Героев Сибиряков, 1Д)"
    assert "name" not in deliveries[0]  # API отдаёт delivery_name, не name
    assert deliveries[1]["time_shift_msk"] == "2"


async def test_get_deliveries_filters_non_dicts(provider, fake_api):
    fake_api.data["get_deliveries"] = ["x", DELIVERY_SAMARA, None]
    assert await provider.get_deliveries() == [DELIVERY_SAMARA]


async def test_get_balance_unwraps_data_array(provider):
    balance = await provider.get_balance()
    assert balance["balance"] == "15000.00"
    assert balance["active"] == 1


async def test_get_balance_accepts_dict_or_empty(provider, fake_api):
    fake_api.data["get_balance"] = {"balance": "1.00"}
    assert await provider.get_balance() == {"balance": "1.00"}
    fake_api.data["get_balance"] = []
    assert await provider.get_balance() == {}


# --- выбор delivery_key ---------------------------------------------------------------------


async def test_default_delivery_is_first_and_sets_name_and_timezone(provider, fake_api):
    assert provider.delivery_key == ""
    assert provider.delivery_name is None

    await provider.search_items("TOYOTA", "0446533471")

    assert provider.delivery_key == DELIVERY_VORONEZH["delivery_key"]
    assert provider.delivery_name == "г. Воронеж (ул. Героев Сибиряков, 1Д)"
    assert provider._tz == MSK


async def test_configured_delivery_key_is_kept_and_sets_its_timezone(fake_api):
    p = AutoEuroProvider(
        api_key=API_KEY, delivery_key=DELIVERY_EKATERINBURG["delivery_key"], transport=fake_api.transport
    )
    offers = await p.search_items("TOYOTA", "0446533471")
    assert p.delivery_key == DELIVERY_EKATERINBURG["delivery_key"]
    assert p.delivery_name == "г.Екатеринбург (п. Большой исток, ул. Трудовая 63)"
    assert fake_api.calls("search_items")[0].url.params["delivery_key"] == DELIVERY_EKATERINBURG["delivery_key"]
    ekb = timezone(timedelta(hours=5))
    assert offers[0].delivery_time == datetime(2026, 10, 31, 9, 0, tzinfo=ekb)
    assert offers[0].order_before == datetime(2026, 10, 27, 15, 0, tzinfo=ekb)
    await p.aclose()


async def test_samara_shift_one_hour(fake_api):
    p = AutoEuroProvider(api_key=API_KEY, delivery_key=DELIVERY_SAMARA["delivery_key"], transport=fake_api.transport)
    offers = await p.search_items("TOYOTA", "0446533471")
    assert offers[0].delivery_time.utcoffset() == timedelta(hours=4)
    await p.aclose()


async def test_unknown_delivery_key_is_replaced_by_first(fake_api, caplog):
    p = AutoEuroProvider(api_key=API_KEY, delivery_key="no-such-key", transport=fake_api.transport)
    with caplog.at_level(logging.WARNING, logger="app.services.pricing.autoeuro"):
        await p.search_items("TOYOTA", "0446533471")
    assert p.delivery_key == DELIVERY_VORONEZH["delivery_key"]
    assert p.delivery_name == DELIVERY_VORONEZH["delivery_name"]
    assert fake_api.calls("search_items")[0].url.params["delivery_key"] == DELIVERY_VORONEZH["delivery_key"]
    assert "no-such-key" in caplog.text and "берём первый" in caplog.text
    await p.aclose()


async def test_delivery_name_falls_back_to_documented_name_field(provider, fake_api):
    fake_api.data["get_deliveries"] = [{"delivery_key": "k1", "name": "Склад по документации", "time_shift_msk": 0}]
    await provider.search_items("TOYOTA", "0446533471")
    assert provider.delivery_key == "k1"
    assert provider.delivery_name == "Склад по документации"


async def test_delivery_key_is_stringified(provider, fake_api):
    fake_api.data["get_deliveries"] = [{"delivery_key": 12345, "delivery_name": "N", "time_shift_msk": None}]
    await provider.search_items("TOYOTA", "0446533471")
    assert provider.delivery_key == "12345"
    assert provider._tz == MSK  # time_shift_msk null → сдвиг 0


async def test_no_deliveries_raises(provider, fake_api):
    fake_api.data["get_deliveries"] = []
    with pytest.raises(PriceProviderError, match="нет вариантов получения"):
        await provider.search_items("TOYOTA", "0446533471")
    assert fake_api.calls("search_items") == []


async def test_get_deliveries_requested_once_for_sequential_searches(provider, fake_api):
    await provider.search_items("TOYOTA", "0446533471")
    await provider.search_items("DAIHATSU", "0446533471")
    await provider.search_items("NISSHINBO", "PF-1521")
    assert len(fake_api.calls("get_deliveries")) == 1
    assert len(fake_api.calls("search_items")) == 3


async def test_get_deliveries_requested_once_for_concurrent_searches(provider, fake_api):
    brands = ["TOYOTA", "DAIHATSU", "NISSHINBO", "SANGSIN", "BREMBO"]
    results = await asyncio.gather(*(provider.search_items(b, "0446533471") for b in brands))
    assert len(results) == 5
    assert len(fake_api.calls("get_deliveries")) == 1
    assert len(fake_api.calls("search_items")) == 5
    keys = {r.url.params["delivery_key"] for r in fake_api.calls("search_items")}
    assert keys == {DELIVERY_VORONEZH["delivery_key"]}


async def test_failed_delivery_resolution_is_retried_next_time(provider, fake_api):
    fake_api.errors["get_deliveries"] = {"code": 500, "message": "временно"}
    with pytest.raises(PriceProviderError):
        await provider.search_items("TOYOTA", "0446533471")
    del fake_api.errors["get_deliveries"]
    offers = await provider.search_items("TOYOTA", "0446533471")
    assert offers
    assert len(fake_api.calls("get_deliveries")) == 2


async def test_explicit_get_deliveries_does_not_resolve_delivery(provider, fake_api):
    await provider.get_deliveries()
    assert provider.delivery_key == ""
    await provider.search_items("TOYOTA", "0446533471")
    assert len(fake_api.calls("get_deliveries")) == 2
