"""Модели данных. Один модуль, пока их немного; при росте разнесём по пакету."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Brand(Base):
    """Марка автомобиля для каталогов."""

    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    is_popular: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Category(Base):
    """Сопутствующая категория товаров на главной."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    icon: Mapped[str] = mapped_column(String(50), default="box")
    accent: Mapped[str] = mapped_column(String(20), default="steel")  # steel | orange | green
    sort: Mapped[int] = mapped_column(Integer, default=0)


class NewsItem(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    highlight: Mapped[str | None] = mapped_column(String(100))  # слово, выделяемое оранжевым
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Promo(Base):
    """Акция. kind=card — тёмная карточка справа от марок, kind=slide — слайд карусели."""

    __tablename__ = "promos"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="slide")
    kicker: Mapped[str | None] = mapped_column(String(150))
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str | None] = mapped_column(Text)
    cta_text: Mapped[str | None] = mapped_column(String(100))
    cta_url: Mapped[str | None] = mapped_column(String(300))
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Customer(Base):
    """Клиент платформы (юрлицо: СТО или магазин)."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # название компании
    initials: Mapped[str] = mapped_column(String(4))
    email: Mapped[str | None] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(300))
    inn: Mapped[str | None] = mapped_column(String(12))
    contact_name: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    manager_name: Mapped[str | None] = mapped_column(String(100))
    manager_phone: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="customer")
    estimates: Mapped[list["Estimate"]] = relationship(back_populates="customer")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    cart_items: Mapped[list["CartItem"]] = relationship(back_populates="customer")


class Vehicle(Base):
    """Автомобиль в гараже клиента."""

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    brand: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    year: Mapped[int | None] = mapped_column(Integer)
    vin: Mapped[str | None] = mapped_column(String(17))
    plate: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="vehicles")


class Estimate(Base):
    """Смета / заказ-наряд."""

    __tablename__ = "estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="open")  # open | confirmed | closed
    total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="estimates")
    vehicle: Mapped[Vehicle | None] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="new")  # new | confirmed | shipped | done | cancelled
    total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    delivery_method: Mapped[str] = mapped_column(String(20), default="pickup", server_default="pickup")
    address: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Передача заказа поставщику (autoeuro) от имени нашего аккаунта
    supplier: Mapped[str | None] = mapped_column(String(30))
    supplier_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )  # pending | sent | failed | disabled
    supplier_order_id: Mapped[str | None] = mapped_column(String(50))
    supplier_message: Mapped[str | None] = mapped_column(Text)
    supplier_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    supplier_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )


class OrderItem(Base):
    """Позиция заказа. Цены фиксируются на момент оформления."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(30))
    offer_key: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(300))
    supplier: Mapped[str | None] = mapped_column(String(150))
    supplier_price: Mapped[float] = mapped_column(Numeric(12, 2))
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    delivery_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="items")

    @property
    def total(self) -> float:
        return float(self.price) * self.qty


class CartItem(Base):
    """Корзина. До входа привязана к cart_token из cookie-сессии, после входа — к клиенту."""

    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    cart_token: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    offer_key: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(300))
    supplier: Mapped[str | None] = mapped_column(String(150))
    is_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    supplier_price: Mapped[float] = mapped_column(Numeric(12, 2))
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    delivery_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer | None] = relationship(back_populates="cart_items")

    @property
    def total(self) -> float:
        return float(self.price) * self.qty


class PriceOffer(Base):
    """Кэш предложений внешнего поставщика по паре бренд + артикул.

    Хранятся исходные значения поставщика (цена и сроки без наценки); наценка и сдвиг
    сроков применяются при выдаче (см. services.pricing.policy).
    """

    __tablename__ = "price_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    query_code: Mapped[str] = mapped_column(String(100), index=True)  # нормализованный артикул запроса
    brand: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(300))
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    amount: Mapped[int] = mapped_column(Integer, default=0)
    is_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    cross_type: Mapped[int | None] = mapped_column(Integer)  # NULL — искомый товар, иначе тип аналога
    delivery_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_time_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejects: Mapped[float | None] = mapped_column(Numeric(5, 2))
    supplier: Mapped[str | None] = mapped_column(String(150))  # кто поставляет: склад АЕ, партнёр, дилер
    warehouse: Mapped[str | None] = mapped_column(String(150))
    is_dealer: Mapped[bool] = mapped_column(Boolean, default=False)
    packing: Mapped[int] = mapped_column(Integer, default=0)
    unit: Mapped[str | None] = mapped_column(String(20))
    offer_key: Mapped[str | None] = mapped_column(Text)  # у autoeuro ключ длиннее 200 символов
    raw: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
