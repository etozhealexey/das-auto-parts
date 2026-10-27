"""PricePolicy: наценка к цене поставщика и сдвиг сроков."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.pricing.policy import PricePolicy
from tests.helpers import MSK, dt, make_offer


@pytest.mark.parametrize(
    ("supplier", "client"),
    [
        (2037.38, "2342.99"),  # 2342.987 → вверх
        (0.01, "0.01"),  # 0.0115 → вниз, не 0.02
        (0.30, "0.35"),  # ровно 0.345: во float 0.3*1.15 = 0.34499…, Decimal даёт 0.35
        (1.10, "1.27"),  # ровно 1.265: half-even дал бы 1.26
        (100, "115.00"),
        (9250.05, "10637.56"),  # 10637.5575
        (19391.28, "22299.97"),  # 22299.972
        (1395.47, "1604.79"),  # 1604.7905
        (0, "0.00"),
    ],
)
def test_client_price_markup_15_rounds_half_up_to_kopeck(supplier, client):
    assert PricePolicy().client_price(supplier) == Decimal(client)


def test_client_price_accepts_decimal_and_float_equally():
    policy = PricePolicy()
    assert policy.client_price(Decimal("2037.38")) == policy.client_price(2037.38) == Decimal("2342.99")


@pytest.mark.parametrize(
    ("supplier", "client"),
    [
        (Decimal("999.995"), "1000.00"),  # половина копейки — вверх
        (999.995, "1000.00"),
        (999.994, "999.99"),
        (Decimal("0.005"), "0.01"),
        (Decimal("0.004"), "0.00"),
    ],
)
def test_client_price_markup_zero_only_rounds(supplier, client):
    assert PricePolicy.from_values(0, 0).client_price(supplier) == Decimal(client)


def test_client_price_result_has_two_decimals():
    price = PricePolicy().client_price(123.4)
    assert price == Decimal("141.91")
    assert price.as_tuple().exponent == -2


def test_from_values_converts_settings_types():
    policy = PricePolicy.from_values(15.0, 1)
    assert policy.markup_percent == Decimal("15.0")
    assert policy.extra_days == 1
    assert PricePolicy.from_values("12.5", "2") == PricePolicy(Decimal("12.5"), 2)
    assert PricePolicy().markup_percent == Decimal(15) and PricePolicy().extra_days == 1


def test_client_date_shifts_by_extra_days_and_keeps_timezone():
    policy = PricePolicy()
    assert policy.client_date(dt(31)) == dt(1, month=11)
    assert policy.client_date(dt(31)).tzinfo == MSK
    assert policy.client_date(None) is None

    ekb = timezone(timedelta(hours=5))
    shifted = PricePolicy.from_values(15, 3).client_date(datetime(2026, 10, 31, 9, tzinfo=ekb))
    assert shifted == datetime(2026, 11, 3, 9, tzinfo=ekb)
    assert PricePolicy.from_values(15, 0).client_date(dt(31)) == dt(31)


def test_apply_sets_client_price_and_keeps_supplier_price():
    offer = make_offer(price=2037.38, delivery_time=dt(31), delivery_time_max=dt(31), order_before=dt(27, 15))
    client = PricePolicy().apply(offer)

    assert client.price == 2342.99
    assert isinstance(client.price, float)
    assert client.supplier_price == 2037.38
    assert client.delivery_time == dt(1, month=11)
    assert client.delivery_time_max == dt(1, month=11)
    assert client.order_before == dt(27, 15)  # срок приёма заказа не сдвигается
    # остальные поля не тронуты
    assert (client.brand, client.code, client.amount, client.currency) == ("TOYOTA", "0446533471", 1, "RUB")


def test_apply_does_not_mutate_source_offer():
    offer = make_offer(price=2037.38, delivery_time=dt(31))
    PricePolicy().apply(offer)
    assert offer.price == 2037.38
    assert offer.supplier_price is None
    assert offer.delivery_time == dt(31)


def test_apply_is_idempotent():
    policy = PricePolicy()
    offer = make_offer(price=2037.38, delivery_time=dt(31), delivery_time_max=dt(31))
    once = policy.apply(offer)
    twice = policy.apply(once)
    assert twice is once
    assert twice.price == 2342.99
    assert twice.supplier_price == 2037.38
    assert twice.delivery_time == dt(1, month=11)


def test_apply_keeps_none_dates():
    client = PricePolicy().apply(make_offer(price=10))
    assert client.delivery_time is None
    assert client.delivery_time_max is None
    assert client.order_before is None


def test_apply_with_zero_markup_keeps_price_but_marks_offer_as_applied():
    policy = PricePolicy.from_values(0, 1)
    client = policy.apply(make_offer(price=2037.38, delivery_time=dt(31)))
    assert client.price == 2037.38
    assert client.supplier_price == 2037.38
    assert client.delivery_time == dt(1, month=11)
    assert policy.apply(client) is client


def test_apply_utc_dates_shift_in_utc():
    client = PricePolicy().apply(make_offer(price=1, delivery_time=datetime(2026, 12, 31, 23, 30, tzinfo=UTC)))
    assert client.delivery_time == datetime(2027, 1, 1, 23, 30, tzinfo=UTC)
