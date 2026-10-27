"""Общие зависимости: текущий клиент из cookie-сессии, токен корзины, редирект на вход."""

import uuid
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Customer

SESSION_CUSTOMER_KEY = "customer_id"
SESSION_CART_KEY = "cart_token"


async def get_current_customer(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Customer | None:
    customer_id = request.session.get(SESSION_CUSTOMER_KEY)
    if not customer_id:
        # Токен корзины выдаём сразу, чтобы параллельные «В корзину» до первой cookie
        # не создали две разные анонимные корзины
        get_cart_token(request)
        return None
    customer = await session.get(Customer, customer_id)
    if customer is None or not customer.is_active:
        request.session.pop(SESSION_CUSTOMER_KEY, None)
        return None
    return customer


def get_cart_token(request: Request) -> str:
    """Токен анонимной корзины; создаётся при первом обращении и живёт в сессии."""
    token = request.session.get(SESSION_CART_KEY)
    if not token:
        token = uuid.uuid4().hex
        request.session[SESSION_CART_KEY] = token
    return token


def login_customer(request: Request, customer: Customer) -> None:
    request.session[SESSION_CUSTOMER_KEY] = customer.id


def logout_customer(request: Request) -> None:
    request.session.pop(SESSION_CUSTOMER_KEY, None)


def login_redirect(request: Request) -> RedirectResponse:
    next_url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(next_url, safe='')}", status_code=303)


def safe_next(next_url: str | None, default: str = "/") -> str:
    """Разрешаем только внутренние пути, чтобы не редиректить на чужие сайты."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default
