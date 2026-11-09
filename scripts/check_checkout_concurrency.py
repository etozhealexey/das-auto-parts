"""Проверка PostgreSQL в отдельной временной схеме; поставщик всегда поддельный."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import Base
from app.models import CartItem, Customer, Order
from app.services.cart import CartError, CheckoutForm, checkout
from app.services.pricing.base import Offer
from app.services.pricing.policy import PricePolicy
from app.services.pricing.service import PricingService


async def main():
    url = get_settings().database_url
    if not url.startswith("postgresql"):
        raise SystemExit("This check requires PostgreSQL")
    schema = "dap_check_" + uuid4().hex
    admin = create_async_engine(url)
    test_engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        test_engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            for i in range(1, 4):
                session.add(Customer(id=i, name=f"Concurrency {i}", initials="ТТ"))
                session.add(
                    CartItem(
                        customer_id=i,
                        provider="demo",
                        offer_key=f"offer-{i}",
                        brand="TOYOTA",
                        code="TEST",
                        supplier_price=100,
                        price=115,
                        qty=2,
                    )
                )
            await session.commit()

        class Provider:
            name = "demo"

            async def search_items(self, brand, code, with_crosses=False):
                await asyncio.sleep(0.05)
                return [
                    Offer(brand="TOYOTA", code="TEST", price=100, amount=10, offer_key=f"offer-{i}")
                    for i in range(1, 4)
                ]

        pricing = PricingService(Provider(), timedelta(minutes=30), PricePolicy())
        form = CheckoutForm("pickup", "Test", "+79000000000")

        async def place(customer_id):
            async with factory() as session:
                customer = await session.get(Customer, customer_id)
                try:
                    return await checkout(session, customer, form, pricing)
                except CartError:
                    return None

        results = await asyncio.gather(place(1), place(1))
        assert sum(result is not None for result in results) == 1, (
            "Duplicate checkout created two orders"
        )
        results = await asyncio.gather(place(2), place(3))
        assert all(result is not None for result in results)
        async with factory() as session:
            orders = (await session.scalars(select(Order))).all()
            assert len(orders) == 3 and len({order.number for order in orders}) == 3
            assert all(order.total == Decimal("230.00") for order in orders)
            assert await session.scalar(select(func.count(CartItem.id))) == 0
        print(
            "PostgreSQL: duplicate checkout serialized; concurrent customers have unique order numbers; prices verified."
        )
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
