"""Регистрация, вход, корзина и оформление заказа через HTTP на SQLite в памяти."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import Customer, Order, PriceOffer
from app.services import supplier_orders
from app.services.auth import hash_password

OFFER_KEY = "offer-toyota-1"
SUPPLIER_PRICE = Decimal("2037.38")


@pytest.fixture
def db_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(
                Customer(
                    name="ООО «Тест»", initials="ТС", email="demo@test.ru", password_hash=hash_password("demo1234"),
                    contact_name="Юрий", phone="+79001234567", inn="7811000000",
                )
            )
            session.add(
                PriceOffer(
                    provider="demo", query_code="0446533471", brand="TOYOTA", code="0446533471",
                    name="Колодки", price=SUPPLIER_PRICE, currency="RUB", amount=8, is_stock=False,
                    supplier="Партнёр Авто-Евро", offer_key=OFFER_KEY,
                    delivery_time=datetime(2026, 10, 31, 9, 0, tzinfo=UTC), fetched_at=datetime.now(UTC),
                )
            )
            await session.commit()

    import asyncio

    asyncio.run(prepare())
    return factory


@pytest.fixture
def client(db_sessionmaker, monkeypatch):
    async def override_session():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    # фоновая отправка поставщику открывает свою сессию — направляем её в ту же тестовую базу
    monkeypatch.setattr(supplier_orders, "SessionLocal", db_sessionmaker)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def add(client, key=OFFER_KEY, qty=1, provider="demo"):
    return client.post(
        "/cart/add", json={"provider": provider, "offer_key": key, "qty": qty},
        headers={"Accept": "application/json"},
    )


def test_anonymous_cart_add_count_and_markup(client):
    assert client.get("/api/v1/cart/count").json() == {"count": 0}
    r = add(client, qty=2)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 2}
    r = add(client, qty=1)  # то же предложение — количество складывается
    assert r.json()["count"] == 3
    page = client.get("/cart")
    assert page.status_code == 200
    assert "2 343 ₽" in page.text  # 2037.38 × 1.15 = 2342.99 → округление в шаблоне
    assert "7 029 ₽" in page.text  # 3 шт.


def test_add_unknown_offer_is_409(client):
    r = add(client, key="nope")
    assert r.status_code == 409
    assert "устарело" in r.json()["detail"]


def test_add_rejects_bad_qty(client):
    assert add(client, qty=0).status_code == 422
    assert add(client, qty=1000).status_code == 422


def test_checkout_requires_login_and_keeps_cart_after_login(client, db_sessionmaker):
    add(client, qty=2)
    r = client.get("/checkout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fcheckout")

    r = client.post("/login", data={"email": "DEMO@test.ru", "password": "wrong", "next": "/checkout"})
    assert r.status_code == 401

    r = client.post(
        "/login", data={"email": "DEMO@test.ru", "password": "demo1234", "next": "/checkout"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/checkout"
    assert client.get("/api/v1/cart/count").json() == {"count": 2}  # корзина переехала к клиенту

    r = client.post(
        "/checkout",
        data={"delivery_method": "delivery", "address": "", "contact_name": "Юрий", "phone": "+79001234567"},
    )
    assert r.status_code == 400 and "Укажите адрес доставки" in r.text

    r = client.post(
        "/checkout",
        data={"delivery_method": "pickup", "contact_name": "Юрий", "phone": "+79001234567", "comment": "срочно"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/orders/") and location.endswith("?created=1")

    detail = client.get(location)
    assert detail.status_code == 200
    assert "DAP-10001" in detail.text and "Заказ принят" in detail.text
    # фоновая отправка поставщику отработала после ответа; локально выключена настройкой
    assert "Не передаётся" in detail.text and "SUPPLIER_ORDERS_ENABLED" in detail.text
    assert "4 686 ₽" in detail.text  # 2 × 2342.99 = 4685.98
    assert client.get("/api/v1/cart/count").json() == {"count": 0}
    assert client.get("/checkout", follow_redirects=False).headers["location"] == "/cart"

    async def supplier_status():
        async with db_sessionmaker() as session:
            order = await session.get(Order, int(location.split("/")[2].split("?")[0]))
            return order.supplier_status, order.supplier_message

    status, message = asyncio.run(supplier_status())
    assert status == "disabled" and "SUPPLIER_ORDERS_ENABLED" in message


def test_register_logs_in_and_rejects_duplicate_email(client):
    form = {
        "company": "ООО «Новый сервис»", "inn": "7800000001", "contact_name": "Анна", "email": "new@test.ru",
        "phone": "+7 (900) 000-00-00", "password": "password1", "password2": "password1", "next": "/garage",
    }
    r = client.post("/register", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/garage"
    assert client.get("/garage").status_code == 200  # уже залогинен
    header = client.get("/").text
    assert "Анна" in header and "Выйти" in header

    client.post("/logout")
    r = client.post("/register", data=form)
    assert r.status_code == 400 and "уже зарегистрирован" in r.text


def test_order_detail_is_private(client, db_sessionmaker):
    import asyncio

    async def make_other_order():
        async with db_sessionmaker() as session:
            other = Customer(name="Чужой", initials="ЧЖ", email="other@test.ru", password_hash=hash_password("x" * 8))
            session.add(other)
            await session.flush()
            session.add(Order(customer_id=other.id, number="DAP-99999", status="new", total=1))
            await session.commit()

    asyncio.run(make_other_order())
    client.post("/login", data={"email": "demo@test.ru", "password": "demo1234"})
    assert client.get("/orders/1").status_code == 404


def test_protected_pages_redirect_to_login(client):
    for path in ("/garage", "/estimates", "/orders"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path


def test_login_next_only_internal(client):
    r = client.post(
        "/login", data={"email": "demo@test.ru", "password": "demo1234", "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"
