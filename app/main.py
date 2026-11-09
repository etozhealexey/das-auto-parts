import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.routers import account, api, auth, cart, catalog, pages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # не писать каждый запрос к API в лог


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.services.pricelist.importer import refresh_loop
    from app.services.supplier_orders import retry_loop

    stop = asyncio.Event()
    retry_task = asyncio.create_task(retry_loop(stop))
    price_list_task = asyncio.create_task(refresh_loop(stop))
    try:
        yield
    finally:
        stop.set()
        retry_task.cancel()
        price_list_task.cancel()
        await asyncio.gather(retry_task, price_list_task, return_exceptions=True)
        from app.services.pricing.service import _service

        if _service is not None and hasattr(_service.provider, "aclose"):
            await _service.provider.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.site_name, debug=settings.debug, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=settings.session_max_age_days * 86400,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(account.router)
    app.include_router(pages.router)
    app.include_router(catalog.router)
    app.include_router(auth.router)
    app.include_router(cart.router)
    app.include_router(api.router)
    return app


app = create_app()
