"""Начальные данные: марки, категории, новости, акции, демо-клиент с гаражом и сметами.

Запуск: uv run python -m scripts.seed
Повторный запуск безопасен: уже заполненные таблицы пропускаются.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Brand, Category, Customer, Estimate, NewsItem, Order, Promo, Vehicle
from app.services.auth import hash_password

DEMO_EMAIL = "demo@das-auto-parts.ru"
DEMO_PASSWORD = "demo1234"  # только для локальной разработки

POPULAR = [
    "Audi", "Honda", "Kia", "Mitsubishi",
    "BMW", "Hyundai", "Land Rover", "Nissan",
    "Chevrolet", "Infiniti", "Lexus", "Opel",
    "Citroen", "Jaguar", "Mazda", "Peugeot",
    "Ford", "Jeep", "Mercedes", "Renault",
]
OTHER = [
    "Acura", "Alfa Romeo", "Aston Martin", "Bentley", "BYD", "Cadillac", "Changan", "Chery",
    "Chrysler", "Dacia", "Daewoo", "Daihatsu", "Datsun", "Dodge", "DS", "Exeed", "FAW", "Fiat",
    "Geely", "Genesis", "GMC", "Great Wall", "Haval", "Hummer", "Isuzu", "Iveco", "JAC", "Lada",
    "Lancia", "Lifan", "Lincoln", "Maserati", "Maybach", "MG", "Mini", "Omoda", "Porsche", "Ravon",
    "Rolls-Royce", "Rover", "Saab", "Scion", "Seat", "Skoda", "Smart", "SsangYong", "Subaru",
    "Suzuki", "Tank", "Tesla", "Toyota", "UAZ", "Volkswagen", "Volvo", "Voyah", "ZAZ", "Zeekr",
    "Brilliance", "Dongfeng", "Foton", "GAZ", "Hafei", "Hino", "Jetour",
]
CATEGORIES = [
    ("Автомасла и техжидкости", "oils", "oil", "steel"),
    ("Аккумуляторы и электрика", "electric", "battery", "orange"),
    ("Инструмент и приспособления", "tools", "tool", "steel"),
    ("Стеклоочистители", "wipers", "wiper", "green"),
    ("Автопринадлежности", "accessories", "feather", "steel"),
    ("Каталог TecDoc", "tecdoc", "book", "orange"),
]


def slugify(name: str) -> str:
    return name.lower().replace(" ", "-")


async def seed() -> None:
    async with SessionLocal() as session:
        if not await session.scalar(select(func.count(Brand.id))):
            for i, name in enumerate(POPULAR):
                session.add(Brand(name=name, slug=slugify(name), is_popular=True, sort=i))
            for name in OTHER:
                session.add(Brand(name=name, slug=slugify(name), is_popular=False, sort=100))
            print(f"brands: {len(POPULAR) + len(OTHER)}")

        if not await session.scalar(select(func.count(Category.id))):
            for i, (name, slug, icon, accent) in enumerate(CATEGORIES):
                session.add(Category(name=name, slug=slug, icon=icon, accent=accent, sort=i))
            print(f"categories: {len(CATEGORIES)}")

        if not await session.scalar(select(func.count(NewsItem.id))):
            year = datetime.now(UTC).year
            session.add_all([
                NewsItem(title="Открытие нового мини-склада в Мурманске", highlight="Мурманске",
                         published_at=datetime(year, 9, 11, tzinfo=UTC)),
                NewsItem(title="Опрос: рынок китайских автокомпонентов и удовлетворённость сервисом",
                         published_at=datetime(year, 9, 15, tzinfo=UTC)),
                NewsItem(title="Обновлён каталог TecDoc — добавлено 40 000 новых позиций",
                         published_at=datetime(year, 9, 18, tzinfo=UTC)),
            ])
            print("news: 3")

        if not await session.scalar(select(func.count(Promo.id))):
            session.add_all([
                Promo(kind="card", kicker="До 30 сентября",
                      title="Открытие автосервиса с рассрочкой на оборудование",
                      text="Партнёрская программа для СТО: рассрочка 0% на подъёмники и стенды при заказах от поставщика.",
                      cta_text="Смотреть условия", cta_url="/promo", sort=0),
                Promo(kind="slide", kicker="Эксклюзивно у поставщика · до 5 октября",
                      title="Балл за каждые 10 000 ₽ покупок LYNX для программы лояльности",
                      cta_text="Все акции", cta_url="/promo", sort=0),
                Promo(kind="slide", kicker="Сезонное предложение",
                      title="Скидка 7% на зимние щётки стеклоочистителя при заказе от 50 штук",
                      cta_text="Все акции", cta_url="/promo", sort=1),
                Promo(kind="slide", kicker="Для новых клиентов",
                      title="Бесплатная доставка первых трёх заказов по Санкт-Петербургу",
                      cta_text="Все акции", cta_url="/promo", sort=2),
                Promo(kind="slide", kicker="Масла и техжидкости",
                      title="Дополнительные 2% на программу лояльности за моторные масла Mobil",
                      cta_text="Все акции", cta_url="/promo", sort=3),
            ])
            print("promos: 5")

        if not await session.scalar(select(func.count(Customer.id))):
            customer = Customer(
                name="ООО «ЮжныйСервис»", initials="ЮЗ", email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD), inn="7811000000", contact_name="Юрий Захаров",
                phone="+7 (812) 000-00-00", manager_name="Дмитрий", manager_phone="+7 (911) 297-94-71",
            )
            session.add(customer)
            await session.flush()
            vehicles = [
                Vehicle(customer_id=customer.id, brand="Toyota", model="Camry", year=2018, vin="JTNBE40K903012345", plate="А123ВС178"),
                Vehicle(customer_id=customer.id, brand="Kia", model="Rio", year=2020, vin="Z94CB41BAKR123456", plate="В456ЕК198"),
                Vehicle(customer_id=customer.id, brand="BMW", model="X5 (G05)", year=2021, vin="WBACV610X0LP12345", plate="Е789МН178"),
                Vehicle(customer_id=customer.id, brand="Hyundai", model="Solaris", year=2019, vin="Z94CT41DBLR654321", plate="К321ОР198"),
                Vehicle(customer_id=customer.id, brand="Mercedes", model="E 200 (W213)", year=2017, vin="WDD2130421A123456", plate="М555ТТ178"),
            ]
            session.add_all(vehicles)
            await session.flush()
            now = datetime.now(UTC)
            session.add_all([
                Estimate(customer_id=customer.id, vehicle_id=vehicles[0].id, title="ТО-60: масло, фильтры, свечи",
                         status="open", total=18450, created_at=now - timedelta(days=2)),
                Estimate(customer_id=customer.id, vehicle_id=vehicles[2].id, title="Замена тормозных дисков и колодок",
                         status="open", total=64900, created_at=now - timedelta(days=1)),
                Estimate(customer_id=customer.id, vehicle_id=vehicles[1].id, title="Замена ремня ГРМ",
                         status="confirmed", total=12300, created_at=now - timedelta(days=9)),
                Order(customer_id=customer.id, number="DAP-10231", status="shipped", total=12300, created_at=now - timedelta(days=5)),
                Order(customer_id=customer.id, number="DAP-10198", status="done", total=41780, created_at=now - timedelta(days=20)),
            ])
            print("customer, vehicles: 5, estimates: 3, orders: 2")

        # Демо-клиенту из старого сида добавляем логин, если его ещё нет
        demo = await session.scalar(select(Customer).order_by(Customer.id).limit(1))
        if demo is not None and not demo.password_hash:
            demo.email = DEMO_EMAIL
            demo.password_hash = hash_password(DEMO_PASSWORD)
            demo.inn = demo.inn or "7811000000"
            demo.contact_name = demo.contact_name or "Юрий Захаров"
            print(f"demo login: {DEMO_EMAIL}")

        await session.commit()
    print("done")


if __name__ == "__main__":
    asyncio.run(seed())
