"""Вручную повторить отправку неотправленных заказов поставщику.

Запуск: uv run python -m scripts.resend_orders
"""

import asyncio

from app.db import SessionLocal
from app.services.supplier_orders import resend_pending


async def main() -> None:
    async with SessionLocal() as session:
        sent = await resend_pending(session)
    print(f"отправлено заказов: {sent}")


if __name__ == "__main__":
    asyncio.run(main())
