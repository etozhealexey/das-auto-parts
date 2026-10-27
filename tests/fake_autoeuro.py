"""Поддельный сервер autoeuro.ru v2 для httpx.MockTransport.

Формат ответа как у реального API: {"META": {...}, "DATA": [...]} либо
{"META": {...}, "ERROR": {"code": int, "message": str}}. DATA — всегда массив.
"""

from typing import Any

import httpx

from tests.test_autoeuro_parse import (
    ROW_CROSS0_PARTNER,
    ROW_CROSS2_BRAND_SYNONYM,
    ROW_CROSS3_DEALER_NO_DATES,
    ROW_ORIGINAL_PARTNER,
    ROW_PRICE_NULL_DEALER,
)

META = {"version": "2", "time": "2026-10-26 16:15:00"}

# Три варианта получения из реального ответа get_deliveries (сдвиг от Москвы 0, 2 и 1 час)
DELIVERY_VORONEZH = {
    "delivery_key": "TEST-DELIVERY-KEY-VORONEZH",
    "delivery_name": "г. Воронеж (ул. Героев Сибиряков, 1Д)",
    "time_shift_msk": "0",
}
DELIVERY_EKATERINBURG = {
    "delivery_key": "QS59xUr7fbkuHwkhoYZm5lyhkGf8oPlNyxCbgkkDgHbY9hrszkUNTsEuZYBmJUwOEPb2iIb01uSVTJYQWkRv05qrVm4c",
    "delivery_name": "г.Екатеринбург (п. Большой исток, ул. Трудовая 63)",
    "time_shift_msk": "2",
}
DELIVERY_SAMARA = {
    "delivery_key": "ehFuTwpuKmDdS9f6IPYXPZKyUHrvT0JOhA4bgkkDgHbY9hrszkUNTsEuZYBmJUwOEPb2iIb01uSVTJYQWkRv05qrVm4c",
    "delivery_name": "г.Самара (ул. Механиков, 18)",
    "time_shift_msk": "1",
}
DELIVERIES = [DELIVERY_VORONEZH, DELIVERY_EKATERINBURG, DELIVERY_SAMARA]

BRANDS = [
    {"brand": "TOYOTA", "code": "0446533471", "name": "Колодки торм.TOYOTA CAMRY (2006>)"},
    {"brand": "DAIHATSU", "code": "0446533471", "name": "Колодки тормозные"},
]

ITEMS = [
    ROW_PRICE_NULL_DEALER,
    ROW_ORIGINAL_PARTNER,
    ROW_CROSS0_PARTNER,
    ROW_CROSS3_DEALER_NO_DATES,
    ROW_CROSS2_BRAND_SYNONYM,
]

BALANCE = [{"balance": "15000.00", "credit": "0.00", "limit": "100000.00", "active": 1}]


class FakeAutoEuro:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.data: dict[str, Any] = {
            "get_deliveries": [dict(d) for d in DELIVERIES],
            "get_balance": BALANCE,
            "search_brands": BRANDS,
            "search_items": ITEMS,
        }
        self.errors: dict[str, Any] = {}  # action -> содержимое поля ERROR
        self.raw_responses: dict[str, httpx.Response] = {}  # action -> ответ как есть
        self.exceptions: dict[str, Exception] = {}  # action -> исключение транспорта

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def action_of(request: httpx.Request) -> str:
        return request.url.path.rstrip("/").rsplit("/", 1)[-1]

    def calls(self, action: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.action_of(r) == action]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        action = self.action_of(request)
        if action in self.exceptions:
            raise self.exceptions[action]
        if action in self.raw_responses:
            return self.raw_responses[action]
        if action in self.errors:
            return httpx.Response(200, json={"META": META, "ERROR": self.errors[action]})
        return httpx.Response(200, json={"META": META, "DATA": self.data.get(action, [])})
