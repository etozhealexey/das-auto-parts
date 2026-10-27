"""Корзина и оформление заказа."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_cart_token, get_current_customer, login_redirect
from app.models import Customer
from app.services import cart as cart_service
from app.services.cart import CartError, CheckoutForm
from app.services.pricing import get_pricing_service
from app.services.supplier_orders import send_order_by_id
from app.templating import templates

router = APIRouter()


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status_code)


class AddToCartIn(BaseModel):
    provider: str = Field(min_length=1, max_length=30)
    offer_key: str = Field(min_length=1, max_length=1000)
    qty: int = Field(default=1, ge=1, le=cart_service.MAX_QTY)


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "") or request.headers.get(
        "content-type", ""
    ).startswith("application/json")


@router.get("/api/v1/cart/count")
async def api_cart_count(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    token = request.session.get("cart_token")
    return {"count": await cart_service.cart_count(session, customer, token)}


@router.post("/cart/add")
async def cart_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    try:
        if _wants_json(request):
            payload = AddToCartIn.model_validate(await request.json())
        else:
            form = await request.form()
            payload = AddToCartIn.model_validate(
                {"provider": form.get("provider", ""), "offer_key": form.get("offer_key", ""), "qty": form.get("qty", 1)}
            )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Некорректные данные товара") from exc
    token = get_cart_token(request)
    try:
        await cart_service.add_to_cart(
            session, customer=customer, cart_token=token, provider=payload.provider,
            offer_key=payload.offer_key, qty=payload.qty, policy=get_pricing_service().policy,
        )
    except CartError as exc:
        if _wants_json(request):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return render(request, "stub.html", active=None, customer=customer, title="Корзина", text=str(exc), status_code=409)
    count = await cart_service.cart_count(session, customer, token)
    if _wants_json(request):
        return JSONResponse({"ok": True, "count": count})
    return RedirectResponse("/cart", status_code=303)


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    items = await cart_service.get_cart_items(session, customer, request.session.get("cart_token"))
    return render(
        request, "cart.html", active=None, customer=customer, items=items,
        total=cart_service.cart_total(items),
    )


@router.post("/cart/update")
async def cart_update(
    request: Request,
    item_id: int = Form(...),
    qty: int = Form(...),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    try:
        await cart_service.set_qty(session, customer, request.session.get("cart_token"), item_id, qty)
    except CartError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/remove")
async def cart_remove(
    request: Request,
    item_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    try:
        await cart_service.remove_item(session, customer, request.session.get("cart_token"), item_id)
    except CartError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse("/cart", status_code=303)


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    if customer is None:
        return login_redirect(request)
    items = await cart_service.get_cart_items(session, customer, None)
    if not items:
        return RedirectResponse("/cart", status_code=303)
    values = {
        "delivery_method": "pickup", "contact_name": customer.contact_name or "",
        "phone": customer.phone or "", "address": "", "comment": "",
    }
    return render(
        request, "checkout.html", active=None, customer=customer, items=items,
        total=cart_service.cart_total(items), values=values, errors={},
    )


@router.post("/checkout", response_class=HTMLResponse)
async def checkout_submit(
    request: Request,
    background: BackgroundTasks,
    delivery_method: str = Form("pickup"),
    contact_name: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    comment: str = Form(""),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    if customer is None:
        return login_redirect(request)
    form = CheckoutForm(
        delivery_method=delivery_method, contact_name=contact_name, phone=phone, address=address, comment=comment
    )
    errors = form.validate()
    items = await cart_service.get_cart_items(session, customer, None)
    if not items:
        return RedirectResponse("/cart", status_code=303)
    if errors:
        return render(
            request, "checkout.html", active=None, customer=customer, items=items,
            total=cart_service.cart_total(items), values=form.__dict__, errors=errors, status_code=400,
        )
    try:
        order = await cart_service.checkout(session, customer, form)
    except CartError:
        return RedirectResponse("/cart", status_code=303)
    # Заказ уходит поставщику после ответа клиенту; при сбое его дожмёт фоновый цикл
    background.add_task(send_order_by_id, order.id)
    return RedirectResponse(f"/orders/{order.id}?created=1", status_code=303)
