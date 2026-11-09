"""Образцы для тестов прайс-листа: мини-CSV в стиле реального файла и база SQLite в памяти."""

import io
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.services.pricelist.importer import PriceRow
from app.services.pricing.base import normalize_code

WAREHOUSE = "Санкт-Петербург ЦС"
CSV_NAME = "PriceAE_0000000000_Sankt-Peterburg_CS_2026-10-27_14-42-24.csv"

HEADER = (
    '"Производитель","Марка","КаталожныйНомер","НомерПроизводителя","ОригинальныйНомер",'
    '"Применение","Цена","Единица","Наличие","МинУпаковка","Комментарий","Валюта","КодТНВЭД",'
    '"КодОКПД2","РегистрационныйНомерСертификата","СсылкаНаСертификат","GTIN"'
)
CERT = "ЕАЭС KG417/039.PL.02.07365"
CERT_URL = "https://swis.trade.kg/Doc/e6f18d0c-f054-46b2-afa0-d5e756abb735"


def line(
    brand: str,
    code: str,
    oem: str,
    name: str,
    price: str,
    unit: str = "шт.",
    qty: str = "1",
    min_pack: str = "1",
    *,
    tnved: str = "",
    okpd2: str = "",
    cert: str = "",
    cert_url: str = "",
    gtin: str = "",
    key: str | None = None,
) -> str:
    """Строка CSV в формате прайса: все поля в кавычках, ключ по умолчанию «Бренд^Артикул»."""
    fields = [
        brand, "", key or f"{brand}^{code}", code, oem, name, price, unit, qty, min_pack, "",
        "руб.", tnved, okpd2, cert, cert_url, gtin,
    ]  # fmt: skip
    return ",".join(f'"{f}"' for f in fields)


# 10 строк: 7 годных (одна — дубль ключа первой), 3 негодных
ROWS = [
    line(
        "KAYABA", "349079", "E6210JD02A", "Амортизатор зад.NISSAN QASHQAI J10E 06> (газ.)",
        "7064.47", qty="2", cert=CERT + "          ", cert_url=CERT_URL + "        ",
    ),
    line(
        "MANN-FILTER", "W 712/75", "", "Фильтр масляный OPEL ASTRA G/H, CHEVROLET LACETTI",
        "486.00", qty="37", tnved="8421230009", okpd2="28.29.13.110", gtin="4011558722401",
    ),
    line(
        "Hyundai/Kia", "26300-35503", "2630035503", "Фильтр масляный HYUNDAI/KIA (оригинал)",
        "412.50", qty="120",
    ),
    line(
        "ELRING", "530.571", "11127555222", "Прокладка ГБЦ BMW N47 (комплект)", "5120.00",
        "комп.", "3",
    ),
    line(
        "BOSCH", "0 986 424 797", "34116761280", "Колодки тормозные пер.BMW 5 (E60) 03>",
        "3187.00", qty="0",
    ),
    line(
        "NGK", "BKR6EKPB-11", "9091901240", "Свеча зажигания TOYOTA Camry 2.4 01>", "нет",
        qty="15", min_pack="4",
    ),
    line(
        "KAYABA", "349079", "E6210JD02A", "Амортизатор зад.NISSAN QASHQAI (дубль ключа)",
        "9999.00",
    ),
    line(
        "GM/Opel/Chevrolet", "13285808", "", "Втулка стабилизатора пер.OPEL INSIGNIA 08>",
        "350.00", "штука", "6", "",
    ),
    line("", "12345", "", "Деталь без бренда", "100.00"),
    line(
        "LIQUI MOLY", "7516", "", "Масло моторное 5W-30 Molygen New Generation 4л", "5890.00",
        qty="48", min_pack="4", key="Liqui Moly^7516",
    ),
]  # fmt: skip
CSV_TEXT = HEADER + "\n" + "\n".join(ROWS) + "\n"
ROWS_TOTAL = 10
ROWS_VALID = 7  # после разбора, включая дубль ключа
ROWS_UNIQUE = 6  # после замены в базе
BRANDS_UNIQUE = 6


def csv_bytes(bom: bool = False) -> bytes:
    data = CSV_TEXT.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def zip_bytes(csv_name: str = CSV_NAME) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(csv_name, csv_bytes())
    return buffer.getvalue()


def make_row(**overrides) -> PriceRow:
    base = {
        "item_key": "KAYABA^349079",
        "brand": "KAYABA",
        "brand_slug": "kayaba",
        "code": "349079",
        "oem_code": None,
        "name": "Амортизатор зад.NISSAN QASHQAI",
        "category": "suspension",
        "supplier_price": Decimal("7064.47"),
        "currency": "RUB",
        "unit": "шт.",
        "qty": 2,
        "min_pack": 1,
        "gtin": None,
        "tnved": None,
        "okpd2": None,
        "certificate": None,
        "certificate_url": None,
    }
    base.update(overrides)
    base.setdefault("code_norm", normalize_code(base["code"]))
    oem = base["oem_code"]
    base.setdefault("oem_code_norm", normalize_code(oem) if oem else None)
    return PriceRow(**base)


@asynccontextmanager
async def sqlite_sessionmaker() -> AsyncIterator[async_sessionmaker]:
    """SQLite в памяти со всеми таблицами; одно соединение на все сессии.

    Движок создаётся и закрывается в цикле событий теста: иначе поток aiosqlite
    переживает тест и падает на уже закрытом цикле.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
