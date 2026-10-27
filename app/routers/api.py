"""JSON API для фронтенда и интеграций."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services.pricing import OfferOut, PriceProviderError, get_pricing_service

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    await session.execute(text("select 1"))
    return {"status": "ok", "price_provider": get_pricing_service().provider.name}


@router.get("/search", response_model=list[OfferOut])
async def search(
    code: str = Query(..., min_length=2, max_length=100),
    refresh: bool = False,
    session: AsyncSession = Depends(get_session),
):
    try:
        return await get_pricing_service().search_by_code(session, code, force_refresh=refresh)
    except PriceProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
