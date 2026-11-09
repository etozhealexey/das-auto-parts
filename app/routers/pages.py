"""HTML-страницы."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.deps import get_current_customer, login_redirect
from app.models import Customer, Estimate, NewsItem, Order, Promo, Vehicle
from app.services.pricelist import catalog
from app.services.pricing import PriceProviderError, get_pricing_service
from app.services.pricing.grouping import group_offers
from app.templating import templates

router = APIRouter()


def render(request: Request, template: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    brands = await catalog.list_brands(session)
    popular_brands = sorted(brands, key=lambda b: (-b.count, b.name.casefold()))[:20]
    brands_total = len(brands)
    categories = (await catalog.list_categories(session))[:6]
    # Три последние новости, в блоке показываем по возрастанию даты, как в макете
    news = (await session.scalars(select(NewsItem).order_by(NewsItem.published_at.desc()).limit(3))).all()
    news = list(reversed(news))
    promo_card = await session.scalar(select(Promo).where(Promo.kind == "card").order_by(Promo.sort).limit(1))
    slides = (await session.scalars(select(Promo).where(Promo.kind == "slide").order_by(Promo.sort))).all()

    vehicles_count = estimates_count = 0
    if customer:
        vehicles_count = await session.scalar(
            select(func.count(Vehicle.id)).where(Vehicle.customer_id == customer.id)
        ) or 0
        estimates_count = await session.scalar(
            select(func.count(Estimate.id)).where(
                Estimate.customer_id == customer.id, Estimate.status == "open"
            )
        ) or 0

    return render(
        request,
        "index.html",
        active="home",
        customer=customer,
        popular_brands=popular_brands,
        brands_total=brands_total,
        categories=categories,
        news=news,
        promo_card=promo_card,
        slides=slides,
        vehicles_count=vehicles_count,
        estimates_count=estimates_count,
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query("", max_length=100),
    mode: str = Query("article"),
    refresh: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    q = q.strip()
    offers: list = []
    error: str | None = None
    if mode == "article" and q:
        try:
            offers = await get_pricing_service().search_by_code(session, q, force_refresh=refresh)
        except PriceProviderError as exc:
            error = str(exc)
    elif mode in ("vin", "model") and q:
        error = "Подбор по VIN и по марке/модели будет подключён на следующем этапе."
    return render(
        request,
        "search.html",
        active=None,
        customer=customer,
        q=q,
        mode=mode,
        result=group_offers(offers),
        error=error,
        provider=get_pricing_service().provider.name,
    )


@router.get("/orders", response_class=HTMLResponse)
async def orders(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    if customer is None:
        return login_redirect(request)
    items = []
    if customer:
        items = (
            await session.scalars(
                select(Order).where(Order.customer_id == customer.id).order_by(Order.created_at.desc())
            )
        ).all()
    return render(request, "orders.html", active="orders", customer=customer, orders=items)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    request: Request,
    order_id: int,
    created: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    if customer is None:
        return login_redirect(request)
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id, Order.customer_id == customer.id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return render(request, "order_detail.html", active="orders", customer=customer, order=order, created=created)


@router.get("/promo", response_class=HTMLResponse)
async def promo(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    promos = (await session.scalars(select(Promo).order_by(Promo.sort))).all()
    news = (await session.scalars(select(NewsItem).order_by(NewsItem.published_at.desc()))).all()
    return render(request, "promo.html", active="promo", customer=customer, promos=promos, news=news)


@router.get("/contacts", response_class=HTMLResponse)
async def contacts(request: Request, customer: Customer | None = Depends(get_current_customer)):
    return render(request, "contacts.html", active="contacts", customer=customer)
