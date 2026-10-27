"""Передача заказов клиентов поставщику (autoeuro) от имени нашего аккаунта.

Заказ создаётся на сайте всегда; отправка поставщику — отдельный шаг со статусом в Order:
  pending  — ещё не отправляли
  sent     — принят поставщиком, supplier_order_id — номер у поставщика
  failed   — попытка не удалась (сбой API или заказ не принят); повторяется фоновым циклом
  disabled — отправка выключена настройкой SUPPLIER_ORDERS_ENABLED
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.models import Order
from app.services.pricing import PriceProviderError, get_pricing_service
from app.services.pricing.base import PriceProvider, SupplierOrderItem

log = logging.getLogger(__name__)

RETRYABLE = ("pending", "failed")


def build_comment(order: Order) -> str:
    parts = [order.number, order.customer.name if order.customer else "", order.contact_name or ""]
    if order.comment:
        parts.append(order.comment)
    return "; ".join(p for p in parts if p)[:255]


async def send_order(session: AsyncSession, order: Order, provider: PriceProvider | None = None) -> Order:
    """Отправить один заказ. Меняет supplier_* поля и коммитит."""
    settings = get_settings()
    if order.supplier_status == "sent":
        return order
    provider = provider or get_pricing_service().provider
    order.supplier = provider.name

    if not settings.supplier_orders_enabled:
        order.supplier_status = "disabled"
        order.supplier_message = "Отправка поставщику выключена (SUPPLIER_ORDERS_ENABLED=false)"
        await session.commit()
        return order

    items = [
        SupplierOrderItem(offer_key=i.offer_key, quantity=i.qty)
        for i in order.items
        if i.offer_key and i.provider == provider.name
    ]
    skipped = len(order.items) - len(items)
    order.supplier_attempts += 1
    if not items:
        order.supplier_status = "failed"
        order.supplier_message = "В заказе нет позиций текущего поставщика"
        await session.commit()
        return order

    try:
        result = await provider.create_order(items, comment=build_comment(order))
    except PriceProviderError as exc:
        order.supplier_status = "failed"
        order.supplier_message = str(exc)
        log.warning("Заказ %s не отправлен в %s: %s", order.number, provider.name, exc)
    else:
        if result.accepted:
            order.supplier_status = "sent"
            order.supplier_order_id = result.order_id
            order.supplier_sent_at = datetime.now(UTC)
            order.supplier_message = result.message + (f"; пропущено позиций: {skipped}" if skipped else "")
            log.info("Заказ %s передан в %s как №%s", order.number, provider.name, result.order_id)
        else:
            order.supplier_status = "failed"
            order.supplier_message = result.message or "Поставщик не принял заказ"
            log.warning("Заказ %s отклонён %s: %s", order.number, provider.name, result.message)
    await session.commit()
    return order


async def load_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .where(Order.id == order_id)
    )


async def send_order_by_id(order_id: int) -> None:
    """Фоновая задача после оформления: своя сессия БД, ошибки только в лог."""
    try:
        async with SessionLocal() as session:
            order = await load_order(session, order_id)
            if order is not None:
                await send_order(session, order)
    except Exception:
        log.exception("Сбой фоновой отправки заказа %s", order_id)


async def resend_pending(session: AsyncSession, *, max_age: timedelta = timedelta(days=3)) -> int:
    """Повторить отправку неотправленных заказов. Возвращает число отправленных."""
    settings = get_settings()
    since = datetime.now(UTC) - max_age
    orders = (
        await session.scalars(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.customer))
            .where(
                Order.supplier_status.in_(RETRYABLE),
                Order.supplier_attempts < settings.supplier_orders_max_attempts,
                Order.created_at >= since,
            )
            .order_by(Order.id)
        )
    ).all()
    sent = 0
    for order in orders:
        await send_order(session, order)
        sent += order.supplier_status == "sent"
    return sent


async def retry_loop(stop: asyncio.Event) -> None:
    """Фоновый цикл в процессе приложения: раз в N минут дожимает неотправленные заказы."""
    settings = get_settings()
    interval = max(60, settings.supplier_orders_retry_minutes * 60)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        if not settings.supplier_orders_enabled:
            continue
        try:
            async with SessionLocal() as session:
                sent = await resend_pending(session)
                if sent:
                    log.info("Повторная отправка: передано заказов %s", sent)
        except Exception:
            log.exception("Сбой цикла повторной отправки заказов")
