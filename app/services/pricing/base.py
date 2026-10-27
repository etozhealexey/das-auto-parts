"""Общий контракт источника цен. Любой поставщик реализует PriceProvider."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class BrandMatch(BaseModel):
    brand: str
    code: str
    name: str | None = None


class Offer(BaseModel):
    """Предложение по детали.

    Провайдер заполняет исходные значения поставщика (price, delivery_time, ...).
    После применения PricePolicy `price` и сроки становятся клиентскими, а исходная цена
    поставщика сохраняется в `supplier_price`.
    """

    brand: str
    code: str
    name: str | None = None
    price: float
    currency: str = "RUB"
    supplier_price: float | None = None  # цена поставщика до наценки; None — наценка ещё не применена
    amount: int = 0
    is_stock: bool = False  # True — собственный склад поставщика, False — заказная позиция партнёра
    is_dealer: bool = False
    supplier: str | None = None  # человекочитаемо: «Склад Авто-Евро · Шушары», «Партнёр Авто-Евро»
    warehouse: str | None = None
    cross_type: int | None = None  # None — искомая деталь, иначе тип аналога (см. autoeuro)
    delivery_time: datetime | None = None
    delivery_time_max: datetime | None = None
    order_before: datetime | None = None
    rejects: float | None = None  # вероятность отказа поставщика, %
    packing: int = 0  # кратность отгрузки, 0 — поштучно
    unit: str | None = None
    offer_key: str | None = None
    raw: dict | None = None

    @property
    def is_original_request(self) -> bool:
        return self.cross_type is None


class OfferOut(BaseModel):
    """Публичное представление предложения для JSON API: без цены поставщика и сырого ответа."""

    brand: str
    code: str
    name: str | None = None
    price: float
    currency: str = "RUB"
    amount: int = 0
    is_stock: bool = False
    is_dealer: bool = False
    supplier: str | None = None
    warehouse: str | None = None
    cross_type: int | None = None
    delivery_time: datetime | None = None
    delivery_time_max: datetime | None = None
    order_before: datetime | None = None
    rejects: float | None = None
    packing: int = 0
    unit: str | None = None
    offer_key: str | None = None


class SupplierOrderItem(BaseModel):
    offer_key: str
    quantity: int
    comment: str | None = None


class SupplierOrderResult(BaseModel):
    accepted: bool
    order_id: str | None = None
    message: str = ""


class PriceProviderError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class PriceProvider(Protocol):
    name: str

    async def search_brands(self, code: str) -> list[BrandMatch]:
        """Какие бренды выпускают деталь с таким артикулом."""

    async def search_items(self, brand: str, code: str, with_crosses: bool = True) -> list[Offer]:
        """Предложения (цена, наличие, срок) по паре бренд + артикул."""

    async def create_order(
        self, items: list[SupplierOrderItem], comment: str = "", wait_all: bool = True
    ) -> SupplierOrderResult:
        """Оформить заказ у поставщика от имени нашего аккаунта."""


def normalize_code(code: str) -> str:
    """Артикулы сравниваются без разделителей и регистра: 04465-33471 == 0446533471."""
    return "".join(ch for ch in code if ch.isalnum()).upper()
