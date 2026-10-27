"""Передача заказов в autoeuro: формат запроса create_order, статусы заказа, повтор, оформление."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base
from app.models import Customer, Order, OrderItem
from app.services import supplier_orders
from app.services.pricing.autoeuro import AutoEuroProvider
from app.services.pricing.base import PriceProviderError, SupplierOrderItem, SupplierOrderResult
from app.services.pricing.demo import DemoProvider

META = {"resource": "x", "version": "2", "section": "x", "parameters": {}, "date": "", "user_id": "1"}
DELIVERIES = [{"delivery_key": "DK1", "delivery_name": "г.Санкт-Петербург", "time_shift_msk": "0"}]
PAYERS = [{"payer_key": "PK1", "payer_name": "ООО «Дас-Авто»"}, {"payer_key": "PK2", "payer_name": "ИП"}]


def make_provider(create_order_reply, calls: list, payer_key=""):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/get_deliveries/"):
            return httpx.Response(200, json={"META": META, "DATA": DELIVERIES})
        if path.endswith("/get_payers/"):
            return httpx.Response(200, json={"META": META, "DATA": PAYERS})
        if path.endswith("/create_order/"):
            if isinstance(create_order_reply, Exception):
                raise create_order_reply
            return httpx.Response(200, json=create_order_reply)
        return httpx.Response(404, json={"META": META, "ERROR": {"code": 404, "message": "no"}})

    return AutoEuroProvider(
        api_key="k", delivery_key="DK1", base_url="https://api.autoeuro.test/api/v2/json",
        transport=httpx.MockTransport(handler), payer_key=payer_key,
    )


async def test_create_order_posts_json_and_parses_accept():
    calls: list[httpx.Request] = []
    provider = make_provider(
        {"META": META, "DATA": [{"order_id": 123456, "result": True, "result_description": "Заказ принят"}]}, calls
    )
    result = await provider.create_order(
        [SupplierOrderItem(offer_key="OK1", quantity=2), SupplierOrderItem(offer_key="OK2", quantity=1, comment="срочно")],
        comment="DAP-10001; ООО Тест",
    )
    assert result == SupplierOrderResult(accepted=True, order_id="123456", message="Заказ принят")
    create = next(c for c in calls if c.url.path.endswith("/create_order/"))
    assert create.method == "POST"
    assert create.headers["key"] == "k"
    assert create.headers["content-type"].startswith("application/json")
    body = json.loads(create.content)
    assert body == {
        "delivery_key": "DK1", "payer_key": "PK1", "wait_all_goods": 1, "comment": "DAP-10001; ООО Тест",
        "stock_items": [{"offer_key": "OK1", "quantity": 2}, {"offer_key": "OK2", "quantity": 1, "comment": "срочно"}],
    }
    # плательщик запрошен один раз на несколько заказов
    await provider.create_order([SupplierOrderItem(offer_key="OK1", quantity=1)])
    assert len([c for c in calls if c.url.path.endswith("/get_payers/")]) == 1
    assert provider.payer_name == "ООО «Дас-Авто»"
    await provider.aclose()


async def test_create_order_uses_configured_payer_and_handles_rejection():
    calls: list = []
    provider = make_provider(
        {"META": META, "DATA": [{"order_id": None, "result": False, "result_description": "Нет на складе"}]},
        calls, payer_key="PK2",
    )
    result = await provider.create_order([SupplierOrderItem(offer_key="OK1", quantity=1)])
    assert result.accepted is False and result.order_id is None and result.message == "Нет на складе"
    body = json.loads(next(c for c in calls if c.url.path.endswith("/create_order/")).content)
    assert body["payer_key"] == "PK2"
    await provider.aclose()


async def test_create_order_api_error_raises():
    provider = make_provider({"META": META, "ERROR": {"code": 403, "message": "Действие не разрешено"}}, [])
    with pytest.raises(PriceProviderError) as exc:
        await provider.create_order([SupplierOrderItem(offer_key="OK1", quantity=1)])
    assert exc.value.code == 403
    with pytest.raises(PriceProviderError):
        await provider.create_order([])
    await provider.aclose()


# ---------- статусы заказа в БД ----------


class StubProvider:
    name = "autoeuro"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def search_brands(self, code):
        return []

    async def search_items(self, brand, code, with_crosses=True):
        return []

    async def create_order(self, items, comment="", wait_all=True):
        self.calls.append((items, comment))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            customer = Customer(name="ООО «Тест»", initials="ТС", email="t@t.ru")
            session.add(customer)
            await session.flush()
            order = Order(
                customer_id=customer.id, number="DAP-10001", status="new", total=Decimal(100),
                contact_name="Юрий", phone="+79001234567", comment="к подъезду", created_at=datetime.now(UTC),
            )
            order.items.append(OrderItem(provider="autoeuro", offer_key="OK1", brand="TOYOTA", code="1", supplier_price=10, price=11.5, qty=2))
            order.items.append(OrderItem(provider="autoeuro", offer_key="OK2", brand="TOYOTA", code="2", supplier_price=10, price=11.5, qty=1))
            order.items.append(OrderItem(provider="demo", offer_key="D1", brand="X", code="3", supplier_price=1, price=1, qty=1))
            session.add(order)
            await session.commit()
            return order.id

    order_id = asyncio.run(prepare())
    return factory, order_id


def run(coro):
    return asyncio.run(coro)


def test_send_order_disabled_marks_status(db, monkeypatch):
    factory, order_id = db
    monkeypatch.setattr(get_settings(), "supplier_orders_enabled", False)
    provider = StubProvider(result=SupplierOrderResult(accepted=True, order_id="1"))

    async def go():
        async with factory() as session:
            order = await supplier_orders.load_order(session, order_id)
            await supplier_orders.send_order(session, order, provider)
            return order

    order = run(go())
    assert order.supplier_status == "disabled" and provider.calls == []
    assert "SUPPLIER_ORDERS_ENABLED" in order.supplier_message


def test_send_order_accepted(db, monkeypatch):
    factory, order_id = db
    monkeypatch.setattr(get_settings(), "supplier_orders_enabled", True)
    provider = StubProvider(result=SupplierOrderResult(accepted=True, order_id="555", message="ok"))

    async def go():
        async with factory() as session:
            order = await supplier_orders.load_order(session, order_id)
            await supplier_orders.send_order(session, order, provider)
            await supplier_orders.send_order(session, order, provider)  # повторно не шлём
            return order

    order = run(go())
    assert order.supplier_status == "sent" and order.supplier_order_id == "555" and order.supplier == "autoeuro"
    assert order.supplier_attempts == 1 and order.supplier_sent_at is not None
    assert "пропущено позиций: 1" in order.supplier_message  # demo-позиция не ушла
    assert len(provider.calls) == 1
    items, comment = provider.calls[0]
    assert [(i.offer_key, i.quantity) for i in items] == [("OK1", 2), ("OK2", 1)]
    assert comment == "DAP-10001; ООО «Тест»; Юрий; к подъезду"


def test_send_order_failure_then_retry(db, monkeypatch):
    factory, order_id = db
    monkeypatch.setattr(get_settings(), "supplier_orders_enabled", True)
    failing = StubProvider(error=PriceProviderError("autoeuro недоступен"))
    rejecting = StubProvider(result=SupplierOrderResult(accepted=False, message="Предложение устарело"))
    ok = StubProvider(result=SupplierOrderResult(accepted=True, order_id="777"))

    async def go():
        async with factory() as session:
            order = await supplier_orders.load_order(session, order_id)
            await supplier_orders.send_order(session, order, failing)
            s1, m1, a1 = order.supplier_status, order.supplier_message, order.supplier_attempts
            await supplier_orders.send_order(session, order, rejecting)
            s2, m2 = order.supplier_status, order.supplier_message
            monkeypatch.setattr(supplier_orders, "get_pricing_service", lambda: type("S", (), {"provider": ok})())
            sent = await supplier_orders.resend_pending(session)
            await session.refresh(order)
            return (s1, m1, a1, s2, m2, sent, order.supplier_status, order.supplier_order_id, order.supplier_attempts)

    s1, m1, a1, s2, m2, sent, s3, oid, attempts = run(go())
    assert (s1, a1) == ("failed", 1) and "недоступен" in m1
    assert s2 == "failed" and m2 == "Предложение устарело"
    assert sent == 1 and s3 == "sent" and oid == "777" and attempts == 3


def test_resend_skips_exhausted_attempts(db, monkeypatch):
    factory, order_id = db
    monkeypatch.setattr(get_settings(), "supplier_orders_enabled", True)
    ok = StubProvider(result=SupplierOrderResult(accepted=True, order_id="1"))
    monkeypatch.setattr(supplier_orders, "get_pricing_service", lambda: type("S", (), {"provider": ok})())

    async def go():
        async with factory() as session:
            order = await supplier_orders.load_order(session, order_id)
            order.supplier_status = "failed"
            order.supplier_attempts = get_settings().supplier_orders_max_attempts
            await session.commit()
            return await supplier_orders.resend_pending(session)

    assert run(go()) == 0 and ok.calls == []


async def test_demo_provider_create_order():
    result = await DemoProvider().create_order([SupplierOrderItem(offer_key="a", quantity=1)])
    assert result.accepted and result.order_id.isdigit()
