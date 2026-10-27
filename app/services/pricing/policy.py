"""Ценовая политика: наценка к цене поставщика и сдвиг сроков поставки."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.services.pricing.base import Offer

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class PricePolicy:
    markup_percent: Decimal = Decimal(15)
    extra_days: int = 1

    @classmethod
    def from_values(cls, markup_percent: float | str, extra_days: int) -> "PricePolicy":
        return cls(markup_percent=Decimal(str(markup_percent)), extra_days=int(extra_days))

    def client_price(self, supplier_price: float | Decimal) -> Decimal:
        """Цена для клиента: цена поставщика + наценка, округление до копейки (half-up)."""
        base = supplier_price if isinstance(supplier_price, Decimal) else Decimal(str(supplier_price))
        factor = (Decimal(100) + self.markup_percent) / Decimal(100)
        return (base * factor).quantize(_CENT, rounding=ROUND_HALF_UP)

    def client_date(self, supplier_date: datetime | None) -> datetime | None:
        """Срок для клиента: срок поставщика + extra_days. None остаётся None."""
        if supplier_date is None:
            return None
        return supplier_date + timedelta(days=self.extra_days)

    def apply(self, offer: Offer) -> Offer:
        """Вернуть копию предложения с клиентской ценой и сроками.

        Идемпотентно: если наценка уже применена (supplier_price заполнен), возвращает как есть.
        """
        if offer.supplier_price is not None:
            return offer
        return offer.model_copy(
            update={
                "supplier_price": offer.price,
                "price": float(self.client_price(offer.price)),
                "delivery_time": self.client_date(offer.delivery_time),
                "delivery_time_max": self.client_date(offer.delivery_time_max),
            }
        )
