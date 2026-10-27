"""Регистрация, вход и выход."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import (
    get_cart_token,
    get_current_customer,
    login_customer,
    logout_customer,
    safe_next,
)
from app.models import Customer
from app.services import auth as auth_service
from app.services.cart import attach_manager_defaults, merge_cart_on_login
from app.templating import templates

router = APIRouter()


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status_code)


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, next: str = "/", customer: Customer | None = Depends(get_current_customer)):
    if customer:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "auth/register.html", active=None, customer=None, errors={}, values={}, next=safe_next(next))


@router.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    company: str = Form(""),
    inn: str = Form(""),
    contact_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    next: str = Form("/"),
    session: AsyncSession = Depends(get_session),
):
    values = {"company": company, "inn": inn, "contact_name": contact_name, "email": email, "phone": phone}
    errors = auth_service.validate_registration(
        company=company, inn=inn, contact_name=contact_name, email=email, phone=phone,
        password=password, password2=password2,
    )
    normalized_email = auth_service.normalize_email(email)
    if not errors and await session.scalar(select(Customer).where(Customer.email == normalized_email)):
        errors["email"] = "Клиент с таким e-mail уже зарегистрирован"
    if errors:
        return render(
            request, "auth/register.html", active=None, customer=None, errors=errors, values=values,
            next=safe_next(next), status_code=400,
        )

    customer = Customer(
        name=company.strip(),
        initials=auth_service.initials_from_name(company),
        email=normalized_email,
        password_hash=auth_service.hash_password(password),
        inn=inn.strip() or None,
        contact_name=contact_name.strip(),
        phone=phone.strip(),
    )
    session.add(customer)
    await session.commit()
    await attach_manager_defaults(session, customer)
    login_customer(request, customer)
    await merge_cart_on_login(session, customer, request.session.get("cart_token"))
    return RedirectResponse(safe_next(next), status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/", customer: Customer | None = Depends(get_current_customer)):
    if customer:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "auth/login.html", active=None, customer=None, error=None, email="", next=safe_next(next))


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    session: AsyncSession = Depends(get_session),
):
    normalized_email = auth_service.normalize_email(email)
    customer = await session.scalar(select(Customer).where(Customer.email == normalized_email))
    if customer is None or not customer.is_active or not auth_service.verify_password(password, customer.password_hash):
        return render(
            request, "auth/login.html", active=None, customer=None,
            error="Неверный e-mail или пароль", email=email, next=safe_next(next), status_code=401,
        )
    login_customer(request, customer)
    await merge_cart_on_login(session, customer, request.session.get("cart_token"))
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    logout_customer(request)
    return RedirectResponse("/", status_code=303)


__all__ = ["get_cart_token", "router"]
