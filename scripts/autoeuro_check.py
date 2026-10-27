"""Проверка доступа к API autoeuro.ru по ключу из .env.

Запуск: uv run python -m scripts.autoeuro_check [артикул]
Делает 5 запросов: баланс, варианты получения, плательщики, бренды по артикулу, предложения по первому бренду.
Заказы не создаёт.
Ключ на экран не выводится.
"""

import asyncio
import sys

from app.config import get_settings
from app.services.pricing.autoeuro import AutoEuroProvider
from app.services.pricing.base import PriceProviderError
from app.services.pricing.service import build_policy


async def main(code: str) -> int:
    settings = get_settings()
    if not settings.autoeuro_api_key:
        print("AUTOEURO_API_KEY пуст — впишите ключ в .env")
        return 1
    provider = AutoEuroProvider(
        api_key=settings.autoeuro_api_key,
        delivery_key=settings.autoeuro_delivery_key,
        base_url=settings.autoeuro_base_url,
    )
    policy = build_policy(settings)
    try:
        balance = await provider.get_balance()
        print("Баланс:", {k: balance.get(k) for k in ("balance", "credit", "limit", "active")})

        deliveries = await provider.get_deliveries()
        print(f"Вариантов получения: {len(deliveries)}")
        for d in deliveries:
            name = d.get("delivery_name") or d.get("name") or "—"
            print(f"  delivery_key={d.get('delivery_key')}  {name}  сдвиг от МСК: {d.get('time_shift_msk')}")

        payers = await provider.get_payers()
        print(f"Плательщиков: {len(payers)}")
        for pr in payers:
            print(f"  payer_key={pr.get('payer_key')}  {pr.get('payer_name')}")

        brands = await provider.search_brands(code)
        print(f"Бренды с артикулом {code}: {[b.brand for b in brands] or 'не найдено'}")
        if not brands:
            return 0

        offers = await provider.search_items(brands[0].brand, brands[0].code)
        print(f"Предложений {brands[0].brand} {brands[0].code}: {len(offers)} (вариант получения: {provider.delivery_name})")
        for o in offers[:8]:
            client = policy.apply(o)
            when = client.delivery_time.strftime("%d.%m %H:%M") if client.delivery_time else "—"
            print(
                f"  {o.supplier:<32} {o.amount:>4} {o.unit or 'шт':<4} "
                f"поставщик {o.price:>9.2f} → клиент {client.price:>9.2f} ₽  срок {when}"
                f"{'  аналог' if o.cross_type is not None else ''}"
            )
    except PriceProviderError as exc:
        print("Ошибка API:", exc)
        return 2
    finally:
        await provider.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "0446533471")))
