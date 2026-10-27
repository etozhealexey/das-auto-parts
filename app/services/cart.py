"""Корзина и оформление заказа."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CartItem, Customer, Order, OrderItem, PriceOffer
from app.services.pricing.policy import PricePolicy
from app.services.pricing.service import PricingService

MAX_QTY = 999


class CartError(Exception):
    pass


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


async def next_order_number(session: AsyncSession) -> str:
    last_id = await session.scalar(select(func.max(Order.id))) or 0
    return f"DAP-{10000 + last_id + 1}"


async def checkout(session: AsyncSession, customer: Customer, form: CheckoutForm) -> Order:
    """Создать заказ из корзины клиента и очистить корзину."""
    items = await get_cart_items(session, customer, None)
    if not items:
        raise CartError("Корзина пуста")
    order = Order(
        customer_id=customer.id,
        number=await next_order_number(session),
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
