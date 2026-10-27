"""HTML-страницы."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.deps import get_current_customer, login_redirect
from app.models import Brand, Category, Customer, Estimate, NewsItem, Order, Promo, Vehicle
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
    popular_brands = (
        await session.scalars(
            select(Brand).where(Brand.is_popular).order_by(Brand.sort, Brand.name).limit(20)
        )
    ).all()
    brands_total = await session.scalar(select(func.count(Brand.id))) or 0
    categories = (await session.scalars(select(Category).order_by(Category.sort))).all()
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


@router.get("/catalog", response_class=HTMLResponse)
async def catalog(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    brands = (await session.scalars(select(Brand).order_by(Brand.name))).all()
    return render(request, "catalog.html", active="catalog", customer=customer, brands=brands)


@router.get("/garage", response_class=HTMLResponse)
async def garage(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: Customer | None = Depends(get_current_customer),
):
    if customer is None:
        return login_redirect(request)
    vehicles = []
    if customer:
        vehicles = (
            await session.scalars(
                select(Vehicle).where(Vehicle.customer_id == customer.id).order_by(Vehicle.id)
            )
        ).all()
    return render(request, "garage.html", active="garage", customer=customer, vehicles=vehicles)


@router.get("/estimates", response_class=HTMLResponse)
async def estimates(
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
                select(Estimate)
                .options(selectinload(Estimate.vehicle))
                .where(Estimate.customer_id == customer.id)
                .order_by(Estimate.created_at.desc())
            )
        ).all()
    return render(request, "estimates.html", active="estimates", customer=customer, estimates=items)


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


@router.get("/settlements", response_class=HTMLResponse)
async def settlements(request: Request, customer: Customer | None = Depends(get_current_customer)):
    return render(
        request,
        "stub.html",
        active="settlements",
        customer=customer,
        title="Взаиморасчёты",
        text="Баланс, акты сверки и счета появятся здесь после подключения учётной системы.",
    )


@router.get("/contacts", response_class=HTMLResponse)
async def contacts(request: Request, customer: Customer | None = Depends(get_current_customer)):
    return render(request, "contacts.html", active="contacts", customer=customer)
