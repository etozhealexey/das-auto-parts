"""Сервис цен: ходит к провайдеру, кэширует исходные предложения в price_offers,
при выдаче применяет ценовую политику (наценка, сдвиг сроков)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import PriceOffer
from app.services.pricing.autoeuro import AutoEuroProvider
from app.services.pricing.base import (
    BrandMatch,
    Offer,
    PriceProvider,
    PriceProviderError,
    normalize_code,
)
from app.services.pricing.demo import DemoProvider
from app.services.pricing.policy import PricePolicy

log = logging.getLogger(__name__)

EMPTY_MARKER = "__empty__"


def dedupe_offers(offers: list[Offer]) -> list[Offer]:
    """Одно и то же предложение приходит по нескольким брендам (аналоги). Оставляем первое.

    Ключ — offer_key поставщика; если его нет, совпадение по бренду, артикулу, поставщику,
    цене и сроку. Предложение искомой детали (cross_type None) важнее своей копии-аналога.
    """
    by_key: dict[object, Offer] = {}
    for o in offers:
        key = o.offer_key or (o.brand, o.code, o.supplier, o.price, o.delivery_time, o.amount)
        seen = by_key.get(key)
        if seen is None or (seen.cross_type is not None and o.cross_type is None):
            by_key[key] = o
    return list(by_key.values())


def build_provider(settings: Settings) -> PriceProvider:
    if settings.price_provider == "autoeuro":
        return AutoEuroProvider(
            api_key=settings.autoeuro_api_key,
            delivery_key=settings.autoeuro_delivery_key,
            base_url=settings.autoeuro_base_url,
            payer_key=settings.autoeuro_payer_key,
        )
    return DemoProvider()


def build_policy(settings: Settings) -> PricePolicy:
    return PricePolicy.from_values(settings.price_markup_percent, settings.delivery_extra_days)


class PricingService:
    def __init__(self, provider: PriceProvider, cache_ttl: timedelta, policy: PricePolicy):
        self.provider = provider
        self.cache_ttl = cache_ttl
        self.policy = policy

    async def search_by_code(
        self, session: AsyncSession, code: str, *, force_refresh: bool = False
    ) -> list[Offer]:
        """Все предложения по артикулу у всех брендов, которые его выпускают (с наценкой)."""
        raw_offers = await self.search_supplier_offers(session, code, force_refresh=force_refresh)
        return [self.policy.apply(o) for o in raw_offers]

    async def search_supplier_offers(
        self, session: AsyncSession, code: str, *, force_refresh: bool = False
    ) -> list[Offer]:
        """Исходные предложения поставщика без наценки (из кэша или от провайдера)."""
        query_code = normalize_code(code)
        if not query_code:
            return []

        if not force_refresh:
            cached = await self._load_cached(session, query_code)
            if cached is not None:
                return cached

        brands: list[BrandMatch] = await self.provider.search_brands(query_code)
        if not brands:
            await self._store(session, query_code, [])
            return []

        results = await asyncio.gather(
            *(self.provider.search_items(b.brand, b.code) for b in brands),
            return_exceptions=True,
        )
        offers: list[Offer] = []
        failures = 0
        for brand, result in zip(brands, results, strict=True):
            if isinstance(result, BaseException):
                failures += 1
                log.warning("Ошибка цен для %s %s: %s", brand.brand, brand.code, result)
                continue
            offers.extend(result)

        if failures == len(brands):
            # Ни один бренд не ответил — это сбой источника, а не пустой результат. Кэш не трогаем.
            first_error = next(r for r in results if isinstance(r, BaseException))
            if isinstance(first_error, PriceProviderError):
                raise first_error
            raise PriceProviderError(f"Источник цен не ответил: {first_error}")

        offers = dedupe_offers(offers)
        offers.sort(key=lambda o: (o.cross_type is not None, not o.is_stock, o.price))
        await self._store(session, query_code, offers)
        return offers

    async def _load_cached(self, session: AsyncSession, query_code: str) -> list[Offer] | None:
        fresh_after = datetime.now(UTC) - self.cache_ttl
        latest = await session.scalar(
            select(func.max(PriceOffer.fetched_at)).where(
                PriceOffer.provider == self.provider.name, PriceOffer.query_code == query_code
            )
        )
        if latest is None or latest < fresh_after:
            return None
        rows = (
            await session.scalars(
                select(PriceOffer)
                .where(PriceOffer.provider == self.provider.name, PriceOffer.query_code == query_code)
                .order_by(PriceOffer.id)
            )
        ).all()
        return [self._row_to_offer(r) for r in rows if r.offer_key != EMPTY_MARKER]

    async def _store(self, session: AsyncSession, query_code: str, offers: list[Offer]) -> None:
        await session.execute(
            delete(PriceOffer).where(
                PriceOffer.provider == self.provider.name, PriceOffer.query_code == query_code
            )
        )
        now = datetime.now(UTC)
        if not offers:
            # Запоминаем и пустой результат, чтобы не дёргать API повторно в пределах TTL
            session.add(
                PriceOffer(
                    provider=self.provider.name, query_code=query_code, brand="", code=query_code,
                    price=Decimal(0), amount=0, offer_key=EMPTY_MARKER, fetched_at=now,
                )
            )
        for o in offers:
            session.add(
                PriceOffer(
                    provider=self.provider.name,
                    query_code=query_code,
                    brand=o.brand,
                    code=o.code,
                    name=o.name,
                    price=Decimal(str(o.price)),
                    currency=o.currency,
                    amount=o.amount,
                    is_stock=o.is_stock,
                    is_dealer=o.is_dealer,
                    supplier=o.supplier,
                    warehouse=o.warehouse,
                    cross_type=o.cross_type,
                    delivery_time=o.delivery_time,
                    delivery_time_max=o.delivery_time_max,
                    order_before=o.order_before,
                    rejects=Decimal(str(o.rejects)) if o.rejects is not None else None,
                    packing=o.packing,
                    unit=o.unit,
                    offer_key=o.offer_key,
                    raw=o.raw,
                    fetched_at=now,
                )
            )
        await session.commit()

    @staticmethod
    def _row_to_offer(row: PriceOffer) -> Offer:
        return Offer(
            brand=row.brand,
            code=row.code,
            name=row.name,
            price=float(row.price),
            currency=row.currency,
            amount=row.amount,
            is_stock=row.is_stock,
            is_dealer=row.is_dealer,
            supplier=row.supplier,
            warehouse=row.warehouse,
            cross_type=row.cross_type,
            delivery_time=row.delivery_time,
            delivery_time_max=row.delivery_time_max,
            order_before=row.order_before,
            rejects=float(row.rejects) if row.rejects is not None else None,
            packing=row.packing,
            unit=row.unit,
            offer_key=row.offer_key,
            raw=row.raw,
        )


_service: PricingService | None = None


def get_pricing_service() -> PricingService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = PricingService(
            provider=build_provider(settings),
            cache_ttl=timedelta(minutes=settings.price_cache_ttl_minutes),
            policy=build_policy(settings),
        )
    return _service


__all__ = ["PriceProviderError", "PricingService", "build_policy", "build_provider", "get_pricing_service"]
