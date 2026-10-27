"""Демо-источник цен: детерминированные тестовые данные, чтобы интерфейс работал без ключа API."""

import hashlib
from datetime import UTC, datetime, timedelta

from app.services.pricing.base import (
    BrandMatch,
    Offer,
    SupplierOrderItem,
    SupplierOrderResult,
    normalize_code,
)

_DEMO_BRANDS = ["TOYOTA", "DENSO", "LYNXauto", "SAKURA", "MANN-FILTER"]
_DEMO_NAMES = {
    "TOYOTA": "Фильтр масляный (оригинал)",
    "DENSO": "Фильтр масляный",
    "LYNXauto": "Фильтр масляный LC-1230",
    "SAKURA": "Фильтр масляный C-1230",
    "MANN-FILTER": "Фильтр масляный W 68/3",
}


def _seed(*parts: str) -> int:
    digest = hashlib.md5(":".join(parts).encode()).hexdigest()
    return int(digest[:8], 16)


class DemoProvider:
    name = "demo"

    async def search_brands(self, code: str) -> list[BrandMatch]:
        normalized = normalize_code(code)
        if not normalized:
            return []
        count = 2 + _seed(normalized) % 4
        return [BrandMatch(brand=b, code=normalized, name=_DEMO_NAMES[b]) for b in _DEMO_BRANDS[:count]]

    async def search_items(self, brand: str, code: str, with_crosses: bool = True) -> list[Offer]:
        normalized = normalize_code(code)
        base_price = 300 + _seed(normalized, brand) % 4700
        now = datetime.now(UTC)
        offers = [
            Offer(
                brand=brand,
                code=normalized,
                name=_DEMO_NAMES.get(brand, "Деталь"),
                price=round(base_price * 1.0, 2),
                amount=1 + _seed(normalized, brand, "a") % 12,
                is_stock=True,
                delivery_time=now + timedelta(hours=4),
                rejects=0,
                warehouse="Шушары",
                offer_key=f"demo-{normalized}-{brand}-stock",
            ),
            Offer(
                brand=brand,
                code=normalized,
                name=_DEMO_NAMES.get(brand, "Деталь"),
                price=round(base_price * 0.91, 2),
                amount=20 + _seed(normalized, brand, "b") % 80,
                is_stock=False,
                delivery_time=now + timedelta(days=2),
                rejects=3.5,
                warehouse=None,
                offer_key=f"demo-{normalized}-{brand}-partner",
            ),
        ]
        if with_crosses and brand != _DEMO_BRANDS[0]:
            offers.append(
                Offer(
                    brand=brand,
                    code=normalized + "X",
                    name="Аналог: " + _DEMO_NAMES.get(brand, "Деталь"),
                    price=round(base_price * 0.72, 2),
                    amount=5,
                    is_stock=False,
                    cross_type=3,
                    delivery_time=now + timedelta(days=3),
                    rejects=5.0,
                    offer_key=f"demo-{normalized}-{brand}-cross",
                )
            )
        return offers

    async def create_order(
        self, items: list[SupplierOrderItem], comment: str = "", wait_all: bool = True
    ) -> SupplierOrderResult:
        """Демо: заказ «принимается» с детерминированным номером, ничего никуда не уходит."""
        if not items:
            return SupplierOrderResult(accepted=False, message="нет позиций")
        number = 900000 + _seed(*(i.offer_key for i in items)) % 100000
        return SupplierOrderResult(accepted=True, order_id=str(number), message="Демо-заказ принят")
