from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.0f}".replace(",", " ") + " ₽"


_DISPLAY_TZ = ZoneInfo(get_settings().timezone)


def _format_date(value, fmt: str = "%d.%m") -> str:
    """Даты со смещением показываем в поясе сайта; «наивные» — как есть."""
    if not value:
        return ""
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(_DISPLAY_TZ)
    return value.strftime(fmt)


templates.env.filters["money"] = _format_money
templates.env.filters["date"] = _format_date
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
