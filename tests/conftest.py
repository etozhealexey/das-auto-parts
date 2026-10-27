"""Общие настройки тестов. База данных и сеть не нужны.

Переменные окружения выставляются до первого импорта приложения, чтобы get_settings()
не зависел от локального .env (ключ API, адрес базы, провайдер цен).
"""

import os

os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@localhost:5432/test"
os.environ["PRICE_PROVIDER"] = "demo"
os.environ["AUTOEURO_API_KEY"] = ""
os.environ["AUTOEURO_DELIVERY_KEY"] = ""
os.environ["AUTOEURO_BASE_URL"] = "https://api.autoeuro.test/api/v2/json"
os.environ["PRICE_MARKUP_PERCENT"] = "15"
os.environ["DELIVERY_EXTRA_DAYS"] = "1"
os.environ["PRICE_CACHE_TTL_MINUTES"] = "30"

import pytest

from tests.fake_autoeuro import FakeAutoEuro


@pytest.fixture
def fake_api() -> FakeAutoEuro:
    """Поддельный сервер autoeuro с данными из реальных образцов ответов."""
    return FakeAutoEuro()
