"""Корзина и оформление заказа."""

from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CartItem, Customer, Order, OrderItem, PriceOffer
from app.services.pricing.base import PriceProviderError, normalize_code
from app.services.pricing.policy import PricePolicy
from app.services.pricing.service import PricingService

MAX_QTY = 999


class CartError(Exception):
    pass


class CheckoutReview(CartError):
    def __init__(self, messages: list[str], *, unavailable: bool = False):
        super().__init__("Проверьте изменения перед оформлением")
        self.messages = messages
        self.unavailable = unavailable


def _delivery_day(value):
    if value is None:
        return None
    # SQLite теряет tzinfo; PostgreSQL сохраняет абсолютное время.
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(
        ZoneInfo(get_settings().timezone)
    ).date()


async def recheck_items(items: list[CartItem], pricing: PricingService) -> list[str]:
    """Только свежие предложения; не подменять исчезнувший ключ другим складом."""
    found = {}
    messages = []
    updates = []
    blocked = False
    for item in items:
        label = f"{item.brand} {item.code}"
        if item.provider != pricing.provider.name:
            messages.append(f"{label}: источник предложения изменился. Выберите товар заново.")
            blocked = True
            continue
        key = (item.brand.upper(), normalize_code(item.code))
        if key not in found:
            try:
                found[key] = await pricing.provider.search_items(
                    item.brand, item.code, with_crosses=False
                )
            except PriceProviderError as exc:
                raise CheckoutReview([
                    ("Не удалось проверить цену и наличие у поставщика. "
                     "Корзина сохранена. Повторите подтверждение позже.")
                ], unavailable=True) from exc
        matches = [o for o in found[key] if o.offer_key == item.offer_key
                   and o.brand.upper() == item.brand.upper()
                   and normalize_code(o.code) == normalize_code(item.code)]
        if len(matches) != 1:
            messages.append(f"{label}: предложение больше недоступно. Выберите товар заново.")
            blocked = True
            continue
        raw = matches[0]
        if raw.currency != "RUB" or not Decimal(str(raw.price)).is_finite() or raw.price <= 0:
            messages.append(f"{label}: поставщик не подтвердил корректную цену.")
            blocked = True
            continue
        if raw.amount < item.qty:
            messages.append(f"{label}: доступно {max(0, raw.amount)} шт., в корзине {item.qty} шт. "
                            "Измените количество или удалите позицию в корзине.")
            blocked = True
            continue
        if raw.packing > 1 and item.qty % raw.packing:
            messages.append(f"{label}: количество должно быть кратно {raw.packing}. "
                            "Измените количество в корзине.")
            blocked = True
            continue
        offer = pricing.policy.apply(raw)
        price = Decimal(str(offer.price))
        if price != item.price:
            messages.append(f"{label}: цена изменилась с {item.price:.2f} ₽ на {price:.2f} ₽ за шт.")
        if _delivery_day(offer.delivery_time) != _delivery_day(item.delivery_time):
            day = _delivery_day(offer.delivery_time)
            messages.append(f"{label}: новый срок поставки — "
                            + (day.strftime("%d.%m.%Y") if day else "уточняется") + ".")
        updates.append((item, offer, price))
    if blocked:
        raise CheckoutReview(messages)
    for item, offer, price in updates:
        item.price = price
        item.supplier_price = Decimal(str(offer.supplier_price))
        item.delivery_time = offer.delivery_time
    return messages


@dataclass(frozen=True)
class CheckoutForm:
    delivery_method: str  # pickup | delivery
    contact_name: str
    phone: str
    address: str = ""
    comment: str = ""

    def validate(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        if self.delivery_method not in ("pickup", "delivery"):
            errors["delivery_method"] = "Выберите способ получения"
        if self.delivery_method == "delivery" and len(self.address.strip()) < 5:
            errors["address"] = "Укажите адрес доставки"
        if len(self.contact_name.strip()) < 2:
            errors["contact_name"] = "Укажите контактное лицо"
        if len("".join(ch for ch in self.phone if ch.isdigit())) < 10:
            errors["phone"] = "Укажите телефон"
        return errors


def _owner_filter(customer: Customer | None, cart_token: str | None):
    if customer is not None:
        return CartItem.customer_id == customer.id
    return CartItem.cart_token == cart_token


async def get_cart_items(
    session: AsyncSession, customer: Customer | None, cart_token: str | None
) -> list[CartItem]:
    if customer is None and not cart_token:
        return []
    return list(
        (await session.scalars(select(CartItem).where(_owner_filter(customer, cart_token)).order_by(CartItem.id))).all()
    )


async def cart_count(session: AsyncSession, customer: Customer | None, cart_token: str | None) -> int:
    if customer is None and not cart_token:
        return 0
    return int(
        await session.scalar(select(func.coalesce(func.sum(CartItem.qty), 0)).where(_owner_filter(customer, cart_token)))
        or 0
    )


def cart_total(items: list[CartItem]) -> Decimal:
    return sum((Decimal(str(i.price)) * i.qty for i in items), Decimal(0))


async def add_to_cart(
    session: AsyncSession,
    *,
    customer: Customer | None,
    cart_token: str | None,
    provider: str,
    offer_key: str,
    qty: int,
    policy: PricePolicy,
) -> CartItem:
    """Положить предложение в корзину. Цена берётся из кэша предложений, а не от клиента."""
    if qty < 1 or qty > MAX_QTY:
        raise CartError("Количество должно быть от 1 до 999")
    if customer is None and not cart_token:
        raise CartError("Нет сессии корзины")
    row = await session.scalar(
        select(PriceOffer)
        .where(PriceOffer.provider == provider, PriceOffer.offer_key == offer_key)
        .order_by(PriceOffer.fetched_at.desc())
        .limit(1)
    )
    if row is None:
        raise CartError("Предложение устарело. Повторите поиск и добавьте товар заново")
    offer = policy.apply(PricingService._row_to_offer(row))

    existing = await session.scalar(
        select(CartItem).where(
            _owner_filter(customer, cart_token), CartItem.provider == provider, CartItem.offer_key == offer_key
        )
    )
    if existing is not None:
        existing.qty = min(existing.qty + qty, MAX_QTY)
        existing.price = Decimal(str(offer.price))
        existing.supplier_price = Decimal(str(offer.supplier_price))
        existing.delivery_time = offer.delivery_time
        await session.commit()
        return existing

    item = CartItem(
        customer_id=customer.id if customer else None,
        cart_token=None if customer else cart_token,
        provider=provider,
        offer_key=offer_key,
        brand=offer.brand,
        code=offer.code,
        name=offer.name,
        supplier=offer.supplier,
        is_stock=offer.is_stock,
        supplier_price=Decimal(str(offer.supplier_price)),
        price=Decimal(str(offer.price)),
        qty=qty,
        delivery_time=offer.delivery_time,
    )
    session.add(item)
    await session.commit()
    return item


async def set_qty(
    session: AsyncSession, customer: Customer | None, cart_token: str | None, item_id: int, qty: int
) -> None:
    item = await session.scalar(select(CartItem).where(CartItem.id == item_id, _owner_filter(customer, cart_token)))
    if item is None:
        raise CartError("Позиция не найдена")
    if qty <= 0:
        await session.delete(item)
    else:
        item.qty = min(qty, MAX_QTY)
    await session.commit()


async def remove_item(
    session: AsyncSession, customer: Customer | None, cart_token: str | None, item_id: int
) -> None:
    await set_qty(session, customer, cart_token, item_id, 0)


async def merge_cart_on_login(session: AsyncSession, customer: Customer, cart_token: str | None) -> None:
    """После входа анонимная корзина переезжает к клиенту; одинаковые предложения складываются."""
    if not cart_token:
        return
    anon_items = list(
        (await session.scalars(select(CartItem).where(CartItem.cart_token == cart_token))).all()
    )
    if not anon_items:
        return
    own = {
        (i.provider, i.offer_key): i
        for i in (await session.scalars(select(CartItem).where(CartItem.customer_id == customer.id))).all()
    }
    for item in anon_items:
        twin = own.get((item.provider, item.offer_key))
        if twin is not None:
            twin.qty = min(twin.qty + item.qty, MAX_QTY)
            await session.delete(item)
        else:
            item.customer_id = customer.id
            item.cart_token = None
    await session.commit()


async def checkout(
    session: AsyncSession, customer: Customer, form: CheckoutForm, pricing: PricingService
) -> Order:
    """Создать заказ из корзины клиента и очистить корзину."""
    # Сериализовать повторные подтверждения одного клиента до создания заказа.
    await session.execute(select(Customer.id).where(Customer.id == customer.id).with_for_update())
    items = list((await session.scalars(
        select(CartItem).where(CartItem.customer_id == customer.id)
        .order_by(CartItem.id).with_for_update().execution_options(populate_existing=True)
    )).all())
    if not items:
        raise CartError("Корзина пуста")
    messages = await recheck_items(items, pricing)
    if messages:
        # Новые условия сохраняются, но заказ требует отдельного подтверждения.
        await session.commit()
        raise CheckoutReview(messages)
    order = Order(
        customer_id=customer.id,
        number=f"TMP-{uuid4().hex[:24]}",
        status="new",
        total=cart_total(items),
        delivery_method=form.delivery_method,
        address=form.address.strip() or None,
        contact_name=form.contact_name.strip(),
        phone=form.phone.strip(),
        comment=form.comment.strip() or None,
    )
    for i in items:
        order.items.append(
            OrderItem(
                provider=i.provider,
                offer_key=i.offer_key,
                brand=i.brand,
                code=i.code,
                name=i.name,
                supplier=i.supplier,
                supplier_price=i.supplier_price,
                price=i.price,
                qty=i.qty,
                delivery_time=i.delivery_time,
            )
        )
        await session.delete(i)
    session.add(order)
    await session.flush()
    order.number = f"DAP-{10000 + order.id}"
    await session.commit()
    await session.refresh(order)
    return order


async def attach_manager_defaults(session: AsyncSession, customer: Customer) -> None:
    """Новому клиенту назначается менеджер по умолчанию (пока один на всех)."""
    if not customer.manager_name:
        await session.execute(
            update(Customer)
            .where(Customer.id == customer.id)
            .values(manager_name="Дмитрий", manager_phone="+7 (911) 297-94-71")
        )
        await session.commit()
