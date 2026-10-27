"""AutoEuroProvider.parse_item на строках реального ответа search_items (TOYOTA 0446533471)."""

import logging
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from app.services.pricing.autoeuro import (
    AutoEuroProvider,
    _to_bool,
    _to_dt,
    _to_float,
    _to_int,
    normalize_currency,
    supplier_label,
)
from app.services.pricing.base import Offer
from tests.helpers import MSK

# --- строки скопированы из items_toyota.json без изменений ---------------------------------

# Склад Авто-Евро, дилерская позиция, price: null, packing строкой, cross: null
ROW_PRICE_NULL_DEALER = {
    "offer_key": "apZCALNlSg8y6HfcScExLGfQJ8wSVk12Ar4XO4TK3D6A71RGJdpuetr9sN4kLFCbrdsMAvvzC0WhCEcDr4i3G5wCNNM4gOkpi4m0mIn8qJHLLqZT8blpjBtGWIlTv25SN3eKUgC9886pbluRrgPFod5kfj1yXGuIQKwaYG9TFMhOypX7N71EILf41QWMlgzS96GjLOk1tQb0pRdKN5rfe5TQy2qx2dL6yUHU2cFgm1RDt2JLyWv05aawO1",
    "stock": 1,
    "cross": None,
    "brand": "TOYOTA",
    "code": "0446533471",
    "name": "Колодки торм.TOYOTA CAMRY (2006>)",
    "packing": "1",
    "price": None,
    "currency": "руб.",
    "amount": 1,
    "unit": "шт.",
    "return": "1",
    "order_before": "2026-10-26 19:30",
    "delivery_time": "2026-10-27 09:00",
    "delivery_time_max": "2026-10-27 09:00",
    "rejects": 0,
    "dealer": 1,
    "warehouse_name": "Москва ЦС (Новая Рига)",
    "warehouse_key": "ixFf1vGbGVQrQAPulRAzF",
    "product_id": "13117580",
    "ttl": 11673,
}

# Искомая деталь у партнёра: price строкой, warehouse_name null, rejects float
ROW_ORIGINAL_PARTNER = {
    "offer_key": "FOMF9qs9IJBF1Ft2uczKE6romQoUlfwY2oikQNusp5R2ABQWneoasSTNKNvsm13ERk2udfrJSWOQAVe6IOcreC7PaxGEeQclVmwtu1RHwx3wG89WDxHauTgaOIZnBzntp1ldCkQdz1DJUwO2NrEzjlLYGiHvBLAhn3ByyaC05gbi1TULbn98PZAKxY4exMI0FhHWTX4oOhPdTuwMYghv6bkAvBA1lFtGTD0KPQaDgIygJSPetNGf3PwfEKXia1",
    "stock": 0,
    "cross": None,
    "brand": "TOYOTA",
    "code": "0446533471",
    "name": "Колодки торм.TOYOTA CAMRY (2006>)",
    "packing": 1,
    "price": "2037.38",
    "currency": "руб.",
    "amount": 8,
    "unit": "шт.",
    "return": "1",
    "order_before": "2026-10-27 15:00",
    "delivery_time": "2026-10-31 09:00",
    "delivery_time_max": "2026-10-31 09:00",
    "rejects": 23.41,
    "dealer": 0,
    "warehouse_name": None,
    "warehouse_key": "iqrLkKmRcn0hQShWJio5LYO4",
    "product_id": "13117580",
    "ttl": 81872,
}

# Кросс (cross "0" строкой) у партнёра
ROW_CROSS0_PARTNER = {
    "offer_key": "70PwXRL8qMI4ppdlydiyTdmdgA8Apl4pq9Kr93SL08s0DTOJgZtHMX1lravwmqYF0mHfSULrsHkfcYNkFZVjjFd5Tm6PTs7tkEmIpb9451ILsXAWSr14c6VkJWVxvknc1ms9WFyZ4kWFKo5k4as42Q9riPMg73NozOzFULiDuQIsEA7Td5vNFe2JmBxI5qoo1RB8dgOoXaMVJAekL3P2CRx71NYlPdBYtuXuuegNIcWjG6b8aNKTmeawO1",
    "stock": 0,
    "cross": "0",
    "brand": "NISSHINBO",
    "code": "PF-1521",
    "name": "Колодки пер.TOYOTA CAMRY 06=>",
    "packing": 1,
    "price": "9250.05",
    "currency": "руб.",
    "amount": 1,
    "unit": "шт.",
    "return": "1",
    "order_before": "2026-10-27 15:00",
    "delivery_time": "2026-11-05 09:00",
    "delivery_time_max": "2026-11-05 09:00",
    "rejects": 35.19,
    "dealer": 0,
    "warehouse_name": None,
    "warehouse_key": "ixFf1vGbGVQrQAPulRAzF",
    "product_id": "414666",
    "ttl": 81873,
}

# Проверенный кросс (cross "3") со склада дилера, все даты null
ROW_CROSS3_DEALER_NO_DATES = {
    "offer_key": "scbCj7195jWct9sUfGElZyLEnup9fCoRLEEObPS8kFDlYX1EBtTzxOzINXWAEo7wR96XQNzXN2pSBInoeWVIv5xykmenNRv8KQ4zs2LEVpBlm3RxzIYzsyqHVZ1y5t1yKm1A8Q8hphnwgGzQB7DVsXT8qcrSy3JljiA3ZKAnLymqP7VVjWrLjMLhWRxzKEiL1n1LpiwvoJFZvKF3btJhG5lBeoaWIeThdEgZMkcJY4anLuTMXsX2UvFCcDXia1",
    "stock": 1,
    "cross": "3",
    "brand": "SANGSIN",
    "code": "SP2080",
    "name": "Колодки TOYOTA CAMRY 06=>",
    "packing": "1",
    "price": "1395.47",
    "currency": "руб.",
    "amount": 1,
    "unit": "шт.",
    "return": "1",
    "order_before": None,
    "delivery_time": None,
    "delivery_time_max": None,
    "rejects": 0,
    "dealer": 1,
    "warehouse_name": "Воронеж",
    "warehouse_key": "zXdLjnQDfaeLwThWJio5LYO4",
    "product_id": "9865005",
    "ttl": 1392,
}

# Синоним бренда (cross "2"), amount 9999, срок через два месяца
ROW_CROSS2_BRAND_SYNONYM = {
    "offer_key": "mM2h2CmVZMrMyi4350wDpmD5P77sKhPod3Ud9cdzaCiYx6w0MVuoHzINwbdmM46vaYQfnzAiD3fScS88Rd31uaanuqdDvT5m5ebmoF36u1CU8moxON7gFiFldsdwbQeyI4I1zMoiKhNDpn0m6hDAxsZpXOF4dzMZQzAi4GBSaNcZHktBe2xtGAa1J75TwtYrvhscYuLNZe8XpGmhYIYOkWxy8FDAN5qIHuJuEFV7mFrCsfy0yeh76GM1obJHzfWznS7",
    "stock": 0,
    "cross": "2",
    "brand": "DAIHATSU",
    "code": "0446533471",
    "name": "Колодки тормозные",
    "packing": 1,
    "price": "19391.28",
    "currency": "руб.",
    "amount": 9999,
    "unit": "шт.",
    "return": "1",
    "order_before": "2026-10-27 15:00",
    "delivery_time": "2027-01-08 09:00",
    "delivery_time_max": "2027-01-08 09:00",
    "rejects": 58.27,
    "dealer": 0,
    "warehouse_name": None,
    "warehouse_key": "ixFf1vGbGVQrQAPulRAzF",
    "product_id": "33598731",
    "ttl": 81872,
}

EKB = timezone(timedelta(hours=5))


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"parse_item не должен ходить в сеть: {request.url}")


@pytest.fixture
def provider() -> AutoEuroProvider:
    return AutoEuroProvider(api_key="test-key", transport=httpx.MockTransport(_never_called))


@pytest.fixture
def provider_ekb(provider: AutoEuroProvider) -> AutoEuroProvider:
    """Провайдер после выбора точки получения в Екатеринбурге (time_shift_msk=2 → UTC+5)."""
    provider._apply_delivery(
        {
            "delivery_key": "QS59xUr7fbkuHwkhoYZm5lyhkGf8oPlNyxCbgkkDgHbY9hrszkUNTsEuZYBmJUwOEPb2iIb01uSVTJYQWkRv05qrVm4c",
            "delivery_name": "г.Екатеринбург (п. Большой исток, ул. Трудовая 63)",
            "time_shift_msk": "2",
        }
    )
    return provider


def test_row_with_null_price_is_skipped(provider):
    assert provider.parse_item(ROW_PRICE_NULL_DEALER, "TOYOTA", "0446533471") is None


def test_original_partner_row_fields_and_types(provider):
    offer = provider.parse_item(ROW_ORIGINAL_PARTNER, "TOYOTA", "0446533471")
    assert isinstance(offer, Offer)

    assert offer.brand == "TOYOTA"
    assert offer.code == "0446533471"
    assert offer.name == "Колодки торм.TOYOTA CAMRY (2006>)"
    assert offer.price == 2037.38 and isinstance(offer.price, float)
    assert offer.supplier_price is None  # наценка ещё не применена
    assert offer.currency == "RUB"
    assert offer.amount == 8 and isinstance(offer.amount, int)
    assert offer.unit == "шт."
    assert offer.packing == 1 and isinstance(offer.packing, int)
    assert offer.rejects == 23.41 and isinstance(offer.rejects, float)
    assert offer.is_stock is False
    assert offer.is_dealer is False
    assert offer.cross_type is None
    assert offer.is_original_request is True
    assert offer.warehouse is None
    assert offer.supplier == "Партнёр Авто-Евро"
    assert offer.offer_key == ROW_ORIGINAL_PARTNER["offer_key"]
    assert len(offer.offer_key) == 254  # длиннее прежнего VARCHAR(200) в кэше
    assert offer.raw == ROW_ORIGINAL_PARTNER


def test_dates_are_moscow_time_by_default(provider):
    offer = provider.parse_item(ROW_ORIGINAL_PARTNER, "TOYOTA", "0446533471")
    assert offer.delivery_time == datetime(2026, 10, 31, 9, 0, tzinfo=MSK)
    assert offer.delivery_time_max == datetime(2026, 10, 31, 9, 0, tzinfo=MSK)
    assert offer.order_before == datetime(2026, 10, 27, 15, 0, tzinfo=MSK)
    assert offer.delivery_time.utcoffset() == timedelta(hours=3)
    assert offer.delivery_time.astimezone(UTC) == datetime(2026, 10, 31, 6, 0, tzinfo=UTC)


def test_dates_use_delivery_point_timezone_utc_plus_5(provider_ekb):
    offer = provider_ekb.parse_item(ROW_ORIGINAL_PARTNER, "TOYOTA", "0446533471")
    assert offer.delivery_time == datetime(2026, 10, 31, 9, 0, tzinfo=EKB)
    assert offer.delivery_time.utcoffset() == timedelta(hours=5)
    assert offer.delivery_time.astimezone(UTC) == datetime(2026, 10, 31, 4, 0, tzinfo=UTC)
    assert offer.order_before == datetime(2026, 10, 27, 15, 0, tzinfo=EKB)
    assert provider_ekb.delivery_name == "г.Екатеринбург (п. Большой исток, ул. Трудовая 63)"


def test_cross0_string_is_analog_zero(provider):
    offer = provider.parse_item(ROW_CROSS0_PARTNER, "TOYOTA", "0446533471")
    assert offer.cross_type == 0 and isinstance(offer.cross_type, int)
    assert offer.is_original_request is False
    assert offer.brand == "NISSHINBO"
    assert offer.code == "PF-1521"
    assert offer.price == 9250.05
    assert offer.rejects == 35.19
    assert offer.supplier == "Партнёр Авто-Евро"
    assert offer.delivery_time == datetime(2026, 11, 5, 9, 0, tzinfo=MSK)


def test_cross3_dealer_row_without_dates(provider):
    offer = provider.parse_item(ROW_CROSS3_DEALER_NO_DATES, "TOYOTA", "0446533471")
    assert offer.cross_type == 3
    assert offer.is_stock is True
    assert offer.is_dealer is True
    assert offer.supplier == "Склад Авто-Евро · Воронеж · официальный дилер"
    assert offer.warehouse == "Воронеж"
    assert offer.packing == 1  # packing приходит строкой "1"
    assert offer.rejects == 0.0 and isinstance(offer.rejects, float)
    assert offer.delivery_time is None
    assert offer.delivery_time_max is None
    assert offer.order_before is None
    assert offer.price == 1395.47


def test_cross2_brand_synonym_row(provider):
    offer = provider.parse_item(ROW_CROSS2_BRAND_SYNONYM, "TOYOTA", "0446533471")
    assert offer.cross_type == 2
    assert offer.brand == "DAIHATSU"
    assert offer.amount == 9999
    assert offer.price == 19391.28
    assert offer.delivery_time == datetime(2027, 1, 8, 9, 0, tzinfo=MSK)


@pytest.mark.parametrize(
    ("cross", "expected"),
    [(None, None), ("null", None), (0, 0), ("0", 0), (1, 1), ("2", 2), (3, 3), (10, 10), (11, 11), (12, 12)],
)
def test_cross_values_including_string_null(provider, cross, expected):
    row = {**ROW_ORIGINAL_PARTNER, "cross": cross}
    assert provider.parse_item(row, "TOYOTA", "0446533471").cross_type == expected


def test_warehouse_string_null_becomes_none(provider):
    row = {**ROW_ORIGINAL_PARTNER, "warehouse_name": "null"}
    offer = provider.parse_item(row, "TOYOTA", "0446533471")
    assert offer.warehouse is None
    assert offer.supplier == "Партнёр Авто-Евро"


def test_supplier_label_for_autoeuro_own_stock(provider):
    own_stock = {**ROW_CROSS3_DEALER_NO_DATES, "dealer": 0}
    assert provider.parse_item(own_stock, "TOYOTA", "0446533471").supplier == "Склад Авто-Евро · Воронеж"
    own_stock_no_wh = {**own_stock, "warehouse_name": None}
    assert provider.parse_item(own_stock_no_wh, "TOYOTA", "0446533471").supplier == "Склад Авто-Евро"


def test_supplier_label_matrix():
    assert supplier_label(False, False, None) == "Партнёр Авто-Евро"
    assert supplier_label(False, False, "Воронеж") == "Партнёр Авто-Евро"
    assert supplier_label(True, False, None) == "Склад Авто-Евро"
    assert supplier_label(True, False, "Шушары") == "Склад Авто-Евро · Шушары"
    assert supplier_label(True, True, "Москва ЦС") == "Склад Авто-Евро · Москва ЦС · официальный дилер"
    assert supplier_label(False, True, None) == "Партнёр Авто-Евро · официальный дилер"


def test_brand_and_code_fallback_to_query(provider):
    row = {**ROW_ORIGINAL_PARTNER, "brand": None, "code": ""}
    offer = provider.parse_item(row, "TOYOTA", "0446533471")
    assert (offer.brand, offer.code) == ("TOYOTA", "0446533471")


def test_missing_optional_fields_get_defaults(provider):
    row = {"price": "10", "brand": "X", "code": "Y", "name": "", "unit": "", "offer_key": ""}
    offer = provider.parse_item(row, "X", "Y")
    assert offer.amount == 0
    assert offer.packing == 0
    assert offer.name is None
    assert offer.unit is None
    assert offer.offer_key is None
    assert offer.rejects is None
    assert offer.is_stock is False and offer.is_dealer is False
    assert offer.cross_type is None
    assert offer.delivery_time is None
    assert offer.currency == "RUB"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("руб.", "RUB"),
        ("руб", "RUB"),
        (" РУБ. ", "RUB"),
        ("RUB", "RUB"),
        ("rur", "RUB"),
        ("", "RUB"),
        (None, "RUB"),
        ("usd", "USD"),
        (" eur ", "EUR"),
    ],
)
def test_normalize_currency(value, expected):
    assert normalize_currency(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-27 18:00", datetime(2026, 10, 27, 18, 0, tzinfo=MSK)),
        ("2026-10-27 18:00:30", datetime(2026, 10, 27, 18, 0, 30, tzinfo=MSK)),
        ("2026-10-27T18:00", datetime(2026, 10, 27, 18, 0, tzinfo=MSK)),
        ("2026-10-27", datetime(2026, 10, 27, 0, 0, tzinfo=MSK)),
        (None, None),
        ("", None),
        ("null", None),
    ],
)
def test_to_dt_formats(value, expected):
    assert _to_dt(value, MSK) == expected


def test_to_dt_unknown_format_returns_none_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.pricing.autoeuro"):
        assert _to_dt("27.10.2026 18:00", MSK) is None
    assert "не распознан формат даты" in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2037.38", 2037.38),
        ("1250,50", 1250.5),
        (12, 12.0),
        (23.41, 23.41),
        (None, None),
        ("", None),
        ("null", None),
        ("abc", None),
    ],
)
def test_to_float(value, expected):
    assert _to_float(value) == expected


def test_to_int_and_to_bool():
    assert _to_int("7") == 7
    assert _to_int(7.9) == 7
    assert _to_int("1.0") == 1
    assert _to_int(None) is None
    assert _to_int("null") is None
    assert _to_bool(1) is True
    assert _to_bool("1") is True
    assert _to_bool(0) is False
    assert _to_bool("0") is False
    assert _to_bool(None) is False
