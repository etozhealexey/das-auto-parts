"""Явное назначение роли уже зарегистрированному аккаунту, без публикации паролей."""

import argparse
import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Customer


async def run(email, revoke):
    async with SessionLocal() as session:
        customer = await session.scalar(
            select(Customer).where(func.lower(Customer.email) == email.strip().lower())
        )
        if customer is None:
            raise SystemExit("Аккаунт не найден. Сначала зарегистрируйтесь на сайте.")
        customer.is_manager = not revoke
        await session.commit()
        print("Роль менеджера снята." if revoke else "Роль менеджера назначена.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.email, args.revoke))
