"""Фабрики тестовых объектов."""

from datetime import datetime, timedelta, timezone

from app.services.pricing.base import Offer

MSK = timezone(timedelta(hours=3))


def dt(day: int, hour: int = 9, month: int = 10, year: int = 2026, tz: timezone = MSK) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=tz)


def make_offer(**overrides) -> Offer:
    base = {
        "brand": "TOYOTA",
        "code": "0446533471",
        "name": "Колодки торм.TOYOTA CAMRY (2006>)",
        "price": 1000.0,
        "amount": 1,
        "is_stock": False,
    }
    base.update(overrides)
    return Offer(**base)
