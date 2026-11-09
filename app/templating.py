from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_money(value: float | None) -> str:
    if value is None:
        return "—"
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


_DISPLAY_TZ = ZoneInfo(get_settings().timezone)


def _format_date(value, fmt: str = "%d.%m") -> str:
    """Даты со смещением показываем в поясе сайта; «наивные» — как есть."""
    if not value:
        return ""
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(_DISPLAY_TZ)
    return value.strftime(fmt)


def _format_number(value: int | None) -> str:
    """Целое с разделителем разрядов, как в money: 64 214."""
    return f"{int(value or 0):,}".replace(",", " ")


def _plural(value: int | None, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 позиция, 3 позиции, 11 позиций."""
    n = abs(int(value or 0))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


templates.env.filters["money"] = _format_money
templates.env.filters["date"] = _format_date
templates.env.filters["number"] = _format_number
templates.env.filters["plural"] = _plural
templates.env.globals["settings"] = get_settings()

NAV_ITEMS = [
    ("home", "Главная", "/"),
    ("catalog", "Каталоги запчастей", "/catalog"),
    ("orders", "Мои заказы", "/orders"),
    ("garage", "Гараж", "/garage"),
    ("estimates", "Сметы и заказ-наряды", "/estimates"),
    ("settlements", "Взаиморасчёты", "/settlements"),
    ("contacts", "Контакты", "/contacts"),
]
templates.env.globals["nav_items"] = NAV_ITEMS
