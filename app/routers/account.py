"""Гараж, сметы, кабинет менеджера и ручной учёт оплат."""

import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hmac import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.deps import get_current_customer
from app.models import Customer, Estimate, EstimateLine, Order, Payment, Vehicle
from app.templating import templates

router = APIRouter()
ESTIMATE_STATUSES = {"open": "Черновик", "confirmed": "Подтверждена", "closed": "Закрыта"}
ORDER_STATUSES = {
    "new": "Новый",
    "confirmed": "Подтверждён",
    "shipped": "Отгружен",
    "done": "Выполнен",
    "cancelled": "Отменён",
}


async def member(request: Request, customer=Depends(get_current_customer)):
    if customer is None:
        raise HTTPException(303, headers={"Location": "/login?next=" + request.url.path})
    return customer


async def manager(customer=Depends(member)):
    if not customer.is_manager:
        raise HTTPException(403, "Доступ только для менеджера")
    return customer


def render(request, customer, template, **context):
    token = request.session.setdefault("form_token", secrets.token_urlsafe(32))
    return templates.TemplateResponse(
        request,
        template,
        {
            "customer": customer,
            "csrf": token,
            "statuses": ESTIMATE_STATUSES,
            "order_statuses": ORDER_STATUSES,
            **context,
        },
    )


async def form_data(request):
    data = await request.form()
    token = request.session.get("form_token", "")
    if not token or not compare_digest(str(data.get("csrf", "")), token):
        raise HTTPException(403, "Форма устарела. Обновите страницу и повторите действие.")
    return data


def text(data, key, limit, required=False):
    value = str(data.get(key, "")).strip()
    if (required and not value) or len(value) > limit:
        raise HTTPException(422, f"Поле {key}: заполните значение длиной до {limit} символов")
    return value or None


def integer(data, key, low, high, optional=False):
    value = data.get(key, "")
    if optional and not value:
        return None
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise HTTPException(422, f"Поле {key}: требуется целое число") from None
    if not low <= result <= high:
        raise HTTPException(422, f"Поле {key}: допустимо от {low} до {high}")
    return result


def money(data, key, positive=False):
    try:
        value = Decimal(str(data.get(key, "")).replace(",", "."))
        if not value.is_finite() or value < 0 or value > Decimal("9999999999.99"):
            raise ValueError
        if positive and value == 0:
            raise ValueError
        if value != value.quantize(Decimal("0.01")):
            raise ValueError
        return value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise HTTPException(
            422, f"Поле {key}: укажите сумму в рублях с точностью до копеек"
        ) from None


async def owned(session, model, row_id, customer):
    row = await session.scalar(
        select(model).where(model.id == row_id, model.customer_id == customer.id)
    )
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    return row


def redirect(path):
    return RedirectResponse(path, status_code=303)


@router.get("/garage")
async def garage(
    request: Request, customer=Depends(member), session: AsyncSession = Depends(get_session)
):
    vehicles = (
        await session.scalars(
            select(Vehicle).where(Vehicle.customer_id == customer.id).order_by(Vehicle.id)
        )
    ).all()
    return render(request, customer, "garage.html", active="garage", vehicles=vehicles)


@router.get("/garage/new")
@router.get("/garage/{vehicle_id}/edit")
async def vehicle_form(
    request: Request,
    vehicle_id: int | None = None,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    vehicle = await owned(session, Vehicle, vehicle_id, customer) if vehicle_id else None
    return render(request, customer, "vehicle_form.html", active="garage", vehicle=vehicle)


@router.post("/garage/new")
@router.post("/garage/{vehicle_id}/edit")
async def save_vehicle(
    request: Request,
    vehicle_id: int | None = None,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    vehicle = (
        await owned(session, Vehicle, vehicle_id, customer)
        if vehicle_id
        else Vehicle(customer_id=customer.id)
    )
    brand, model = text(data, "brand", 100, True), text(data, "model", 100, True)
    year = integer(data, "year", 1900, datetime.now(UTC).year + 1, optional=True)
    vin = text(data, "vin", 17)
    if vin and (
        len(vin) != 17 or any(c not in "ABCDEFGHJKLMNPRSTUVWXYZ0123456789" for c in vin.upper())
    ):
        raise HTTPException(422, "VIN должен содержать 17 латинских букв и цифр, без I, O, Q")
    vehicle.brand, vehicle.model, vehicle.year = brand, model, year
    vehicle.vin, vehicle.plate = vin.upper() if vin else None, text(data, "plate", 20)
    session.add(vehicle)
    await session.commit()
    return redirect("/garage")


@router.post("/garage/{vehicle_id}/delete")
async def remove_vehicle(
    vehicle_id: int,
    request: Request,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    await form_data(request)
    vehicle = await owned(session, Vehicle, vehicle_id, customer)
    await session.execute(
        update(Estimate).where(Estimate.vehicle_id == vehicle.id).values(vehicle_id=None)
    )
    await session.delete(vehicle)
    await session.commit()
    return redirect("/garage")


@router.get("/estimates")
async def estimates(
    request: Request, customer=Depends(member), session: AsyncSession = Depends(get_session)
):
    items = (
        await session.scalars(
            select(Estimate)
            .options(selectinload(Estimate.vehicle))
            .where(Estimate.customer_id == customer.id)
            .order_by(Estimate.id.desc())
        )
    ).all()
    vehicles = (
        await session.scalars(select(Vehicle).where(Vehicle.customer_id == customer.id))
    ).all()
    return render(
        request, customer, "estimates.html", active="estimates", estimates=items, vehicles=vehicles
    )


@router.post("/estimates/new")
async def new_estimate(
    request: Request, customer=Depends(member), session: AsyncSession = Depends(get_session)
):
    data = await form_data(request)
    vehicle_id = integer(data, "vehicle_id", 1, 2147483647, optional=True)
    if vehicle_id:
        await owned(session, Vehicle, vehicle_id, customer)
    item = Estimate(
        customer_id=customer.id,
        vehicle_id=vehicle_id,
        title=text(data, "title", 200, True),
        total=0,
    )
    session.add(item)
    await session.commit()
    return redirect(f"/estimates/{item.id}")


@router.get("/estimates/{estimate_id}")
async def estimate_detail(
    estimate_id: int,
    request: Request,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    item = await owned(session, Estimate, estimate_id, customer)
    lines = (
        await session.scalars(
            select(EstimateLine)
            .where(EstimateLine.estimate_id == item.id)
            .order_by(EstimateLine.id)
        )
    ).all()
    vehicles = (
        await session.scalars(select(Vehicle).where(Vehicle.customer_id == customer.id))
    ).all()
    return render(
        request,
        customer,
        "estimate_detail.html",
        active="estimates",
        estimate=item,
        lines=lines,
        vehicles=vehicles,
    )


@router.post("/estimates/{estimate_id}/edit")
async def edit_estimate(
    estimate_id: int,
    request: Request,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    item = await owned(session, Estimate, estimate_id, customer)
    vehicle_id = integer(data, "vehicle_id", 1, 2147483647, optional=True)
    if vehicle_id:
        await owned(session, Vehicle, vehicle_id, customer)
    status = data.get("status", "open")
    if status not in ESTIMATE_STATUSES:
        raise HTTPException(422, "Неизвестный статус сметы")
    item.title, item.vehicle_id, item.status = text(data, "title", 200, True), vehicle_id, status
    await session.commit()
    return redirect(f"/estimates/{item.id}")


async def locked_estimate(session, estimate_id, customer):
    item = await session.scalar(
        select(Estimate)
        .where(Estimate.id == estimate_id, Estimate.customer_id == customer.id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "Смета не найдена")
    if item.status != "open":
        raise HTTPException(409, "Для изменения позиций верните смету в черновик")
    return item


@router.post("/estimates/{estimate_id}/lines")
@router.post("/estimates/{estimate_id}/lines/{line_id}")
async def save_line(
    estimate_id: int,
    request: Request,
    line_id: int | None = None,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    item = await locked_estimate(session, estimate_id, customer)
    line = (
        await session.scalar(
            select(EstimateLine).where(
                EstimateLine.id == line_id, EstimateLine.estimate_id == item.id
            )
        )
        if line_id
        else EstimateLine(estimate_id=item.id)
    )
    if line is None:
        raise HTTPException(404, "Позиция не найдена")
    if data.get("action") == "delete" and line_id:
        await session.delete(line)
    else:
        kind = data.get("kind", "part")
        if kind not in ("part", "labor"):
            raise HTTPException(422, "Выберите запчасть или работу")
        line.kind, line.name, line.code = (
            kind,
            text(data, "name", 300, True),
            text(data, "code", 100),
        )
        line.qty, line.price = integer(data, "qty", 1, 9999), money(data, "price")
        session.add(line)
    await session.flush()
    total = await session.scalar(
        select(func.coalesce(func.sum(EstimateLine.price * EstimateLine.qty), 0)).where(
            EstimateLine.estimate_id == item.id
        )
    )
    if total > Decimal("9999999999.99"):
        raise HTTPException(422, "Сумма сметы слишком велика")
    item.total = total
    await session.commit()
    return redirect(f"/estimates/{item.id}")


@router.post("/estimates/{estimate_id}/delete")
async def remove_estimate(
    estimate_id: int,
    request: Request,
    customer=Depends(member),
    session: AsyncSession = Depends(get_session),
):
    await form_data(request)
    item = await owned(session, Estimate, estimate_id, customer)
    await session.execute(delete(EstimateLine).where(EstimateLine.estimate_id == item.id))
    await session.delete(item)
    await session.commit()
    return redirect("/estimates")


async def statement(session, customer_id):
    orders = (
        await session.scalars(
            select(Order).where(Order.customer_id == customer_id).order_by(Order.id.desc())
        )
    ).all()
    payments = (
        await session.scalars(
            select(Payment).where(Payment.customer_id == customer_id).order_by(Payment.id.desc())
        )
    ).all()
    charged = sum((Decimal(str(o.total)) for o in orders if o.status != "cancelled"), Decimal(0))
    paid = sum((Decimal(str(p.amount)) for p in payments), Decimal(0))
    return {
        "account_orders": orders,
        "payments": payments,
        "charged": charged,
        "paid": paid,
        "balance": charged - paid,
    }


@router.get("/settlements")
async def settlements(
    request: Request, customer=Depends(member), session: AsyncSession = Depends(get_session)
):
    return render(
        request,
        customer,
        "settlements.html",
        active="settlements",
        **await statement(session, customer.id),
    )


@router.get("/manager")
async def manager_home(
    request: Request, customer=Depends(manager), session: AsyncSession = Depends(get_session)
):
    clients = (await session.scalars(select(Customer).order_by(Customer.name))).all()
    orders = (
        await session.scalars(
            select(Order).options(selectinload(Order.customer)).order_by(Order.id.desc()).limit(200)
        )
    ).all()
    return render(
        request, customer, "manager.html", active="manager", clients=clients, orders=orders
    )


@router.get("/manager/orders/{order_id}")
async def manager_order(
    order_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .where(Order.id == order_id)
    )
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    return render(request, customer, "manager_order.html", active="manager", order=order)


@router.post("/manager/orders/{order_id}")
async def manager_order_save(
    order_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    if data.get("status") not in ORDER_STATUSES:
        raise HTTPException(422, "Неизвестный статус заказа")
    order.status = data["status"]
    await session.commit()
    return redirect(f"/manager/orders/{order_id}")


@router.get("/manager/customers/{client_id}")
async def manager_client(
    client_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    client = await session.get(Customer, client_id)
    if client is None:
        raise HTTPException(404, "Клиент не найден")
    return render(
        request,
        customer,
        "manager_client.html",
        active="manager",
        client=client,
        **await statement(session, client_id),
    )


@router.post("/manager/customers/{client_id}")
async def manager_client_save(
    client_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    client = await session.get(Customer, client_id)
    if client is None:
        raise HTTPException(404, "Клиент не найден")
    client.manager_name = text(data, "manager_name", 100)
    client.manager_phone = text(data, "manager_phone", 50)
    await session.commit()
    return redirect(f"/manager/customers/{client_id}")


@router.post("/manager/customers/{client_id}/payments")
async def record_payment(
    client_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    if await session.get(Customer, client_id) is None:
        raise HTTPException(404, "Клиент не найден")
    reference = text(data, "reference", 150, True)
    if reference.startswith("reversal:"):
        raise HTTPException(422, "Префикс reversal: зарезервирован")
    payment = Payment(
        customer_id=client_id,
        manager_id=customer.id,
        amount=money(data, "amount", True),
        reference=reference,
        comment=text(data, "comment", 500),
    )
    session.add(payment)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Оплата с таким номером документа уже записана") from None
    return redirect(f"/manager/customers/{client_id}")


@router.post("/manager/payments/{payment_id}/reverse")
async def reverse_payment(
    payment_id: int,
    request: Request,
    customer=Depends(manager),
    session: AsyncSession = Depends(get_session),
):
    data = await form_data(request)
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise HTTPException(404, "Оплата не найдена")
    reason = text(data, "reason", 500, True)
    if payment.reversal_of or await session.scalar(
        select(Payment.id).where(Payment.reversal_of == payment_id)
    ):
        raise HTTPException(409, "Оплата уже сторнирована или является сторно")
    session.add(
        Payment(
            customer_id=payment.customer_id,
            manager_id=customer.id,
            amount=-payment.amount,
            reference=f"reversal:{payment.id}",
            comment=reason,
            reversal_of=payment.id,
        )
    )
    client_id = payment.customer_id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Оплата уже сторнирована") from None
    return redirect(f"/manager/customers/{client_id}")
