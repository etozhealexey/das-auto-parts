"""Сценарии кабинетов и границы доступа, SQLite без реальных API и оплат."""

import asyncio
import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Customer, Estimate, Order, Payment, Vehicle
from tests import test_cart_flow as fixtures

checkout_provider = fixtures.checkout_provider
client = fixtures.client
db_sessionmaker = fixtures.db_sessionmaker


@pytest.fixture
def signed_in(client):
    client.post("/login", data={"email": "demo@test.ru", "password": "demo1234"})
    return client


def token(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]


def promote(factory):
    async def run():
        async with factory() as session:
            customer = await session.get(Customer, 1)
            customer.is_manager = True
            await session.commit()

    asyncio.run(run())


def test_vehicle_lifecycle_and_validation(signed_in):
    c = signed_in
    csrf = token(c, "/garage/new")
    values = {
        "csrf": csrf,
        "brand": "Toyota",
        "model": "Camry",
        "year": "2020",
        "vin": "JTDBR32E720123456",
    }
    assert c.post("/garage/new", data={**values, "vin": "wrong"}).status_code == 422
    assert c.post("/garage/new", data=values).status_code == 200
    assert "Toyota Camry" in c.get("/garage").text
    assert c.post("/garage/1/edit", data={**values, "model": "Corolla"}).status_code == 200
    assert "Corolla" in c.get("/garage").text
    assert c.post("/garage/1/delete", data={"csrf": csrf}).status_code == 200
    assert "Corolla" not in c.get("/garage").text


def test_estimate_lines_total_status_and_delete(signed_in, db_sessionmaker):
    c = signed_in
    csrf = token(c, "/estimates")
    r = c.post("/estimates/new", data={"csrf": csrf, "title": "ТО"})
    assert r.status_code == 200, r.text
    assert (
        c.post(
            "/estimates/1/lines",
            data={"csrf": csrf, "name": "Фильтр", "qty": "2", "price": "123.45"},
        ).status_code
        == 200
    )
    assert "246.90 ₽" in c.get("/estimates/1").text
    r = c.post(
        "/estimates/1/lines/1",
        data={"csrf": csrf, "name": "Работа", "qty": "3", "price": "100.01", "kind": "labor"},
    )
    assert r.status_code == 200 and "300.03 ₽" in r.text
    assert (
        c.post(
            "/estimates/1/edit", data={"csrf": csrf, "title": "ТО", "status": "confirmed"}
        ).status_code
        == 200
    )
    assert (
        c.post("/estimates/1/lines/1", data={"csrf": csrf, "action": "delete"}).status_code == 409
    )
    c.post("/estimates/1/edit", data={"csrf": csrf, "title": "ТО", "status": "open"})
    assert (
        c.post("/estimates/1/lines/1", data={"csrf": csrf, "action": "delete"}).status_code == 200
    )
    assert "0.00 ₽" in c.get("/estimates/1").text
    assert c.post("/estimates/1/delete", data={"csrf": csrf}).status_code == 200
    assert c.get("/estimates/1").status_code == 404


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "1.001", "10000000000"])
def test_estimate_rejects_invalid_amounts(signed_in, price):
    c = signed_in
    csrf = token(c, "/estimates")
    c.post("/estimates/new", data={"csrf": csrf, "title": "ТО"})
    assert (
        c.post(
            "/estimates/1/lines", data={"csrf": csrf, "name": "Деталь", "qty": "1", "price": price}
        ).status_code
        == 422
    )


def test_ownership_and_manager_boundary(signed_in, db_sessionmaker):
    async def other_data():
        async with db_sessionmaker() as session:
            other = Customer(name="Другой", initials="ДР", email="other@example.test")
            session.add(other)
            await session.flush()
            session.add(Vehicle(customer_id=other.id, brand="Other", model="Private"))
            session.add(Estimate(customer_id=other.id, title="Private", total=0))
            await session.commit()

    asyncio.run(other_data())
    c = signed_in
    csrf = token(c, "/estimates")
    for path in (
        "/garage/1/edit",
        "/estimates/1",
        "/manager",
        "/manager/customers/2",
        "/manager/orders/1",
    ):
        assert c.get(path).status_code == (403 if path.startswith("/manager") else 404)
    assert c.post("/garage/1/delete", data={"csrf": csrf}).status_code == 404
    assert (
        c.post("/estimates/new", data={"csrf": csrf, "title": "X", "vehicle_id": "1"}).status_code
        == 404
    )
    assert (
        c.post(
            "/manager/customers/2/payments", data={"csrf": csrf, "amount": "100", "reference": "X"}
        ).status_code
        == 403
    )


def test_forms_require_csrf(signed_in):
    assert signed_in.post("/garage/new", data={"brand": "A", "model": "B"}).status_code == 403
    assert (
        signed_in.post("/estimates/new", data={"csrf": "forged", "title": "X"}).status_code == 403
    )


def test_manager_payments_idempotency_reversal_and_balance(signed_in, db_sessionmaker):
    promote(db_sessionmaker)
    c = signed_in
    csrf = token(c, "/manager/customers/1")
    values = {"csrf": csrf, "amount": "123.45", "reference": "ПП-1", "comment": "Поступление"}
    assert c.post("/manager/customers/1/payments", data=values).status_code == 200
    assert c.post("/manager/customers/1/payments", data=values).status_code == 409
    page = c.get("/settlements")
    assert page.status_code == 200 and "Аванс" in page.text and "123.45 ₽" in page.text
    assert (
        c.post("/manager/payments/1/reverse", data={"csrf": csrf, "reason": "Ошибка"}).status_code
        == 200
    )
    assert (
        c.post("/manager/payments/1/reverse", data={"csrf": csrf, "reason": "Повтор"}).status_code
        == 409
    )
    assert (
        c.post("/manager/payments/2/reverse", data={"csrf": csrf, "reason": "Повтор"}).status_code
        == 409
    )

    async def total():
        async with db_sessionmaker() as session:
            payments = (await session.scalars(select(Payment))).all()
            return sum((p.amount for p in payments), Decimal(0)), len(payments)

    assert asyncio.run(total()) == (Decimal("0.00"), 2)


def test_manager_internal_status_hidden_from_customer(signed_in, db_sessionmaker):
    async def create():
        async with db_sessionmaker() as session:
            session.add(
                Order(
                    customer_id=1,
                    number="DAP-TEST",
                    total=Decimal("234.56"),
                    supplier_status="failed",
                    supplier_message="PRIVATE FAILURE",
                    supplier_order_id="PRIVATE-ID",
                )
            )
            await session.commit()

    asyncio.run(create())
    c = signed_in
    assert "В работе" in c.get("/orders/1").text
    for path in ("/orders", "/orders/1"):
        html = c.get(path).text
        assert "PRIVATE" not in html and "Ошибка передачи" not in html
    promote(db_sessionmaker)
    csrf = token(c, "/manager/orders/1")
    assert "PRIVATE FAILURE" in c.get("/manager/orders/1").text
    assert (
        c.post("/manager/orders/1", data={"csrf": csrf, "status": "cancelled"}).status_code == 200
    )
    assert "В работе" in c.get("/orders/1").text
    assert "Исключён из расчёта" in c.get("/settlements").text


def test_removing_vehicle_preserves_estimate(signed_in):
    c = signed_in
    csrf = token(c, "/garage/new")
    c.post("/garage/new", data={"csrf": csrf, "brand": "A", "model": "B"})
    c.post("/estimates/new", data={"csrf": csrf, "title": "Keep", "vehicle_id": "1"})
    c.post("/garage/1/delete", data={"csrf": csrf})
    assert c.get("/estimates/1").status_code == 200


@pytest.mark.parametrize("path", ["/garage", "/estimates", "/settlements", "/manager"])
def test_account_requires_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
