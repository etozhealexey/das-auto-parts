from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://dasauto:dasauto@localhost:5432/dasauto"
    debug: bool = False
    secret_key: str = "change-me-in-production"  # подпись cookie сессии
    session_max_age_days: int = 30

    price_provider: Literal["demo", "autoeuro"] = "demo"
    autoeuro_api_key: str = ""
    autoeuro_delivery_key: str = ""
    autoeuro_base_url: str = "https://api.autoeuro.ru/api/v2/json"
    price_cache_ttl_minutes: int = 30
    autoeuro_payer_key: str = ""  # плательщик из /get_payers; пусто — первый в списке
    # Отправлять ли заказы клиентов поставщику. На локальной машине держать выключенным:
    # включённая отправка создаёт настоящие заказы и списывает деньги с баланса в autoeuro
    supplier_orders_enabled: bool = False
    supplier_orders_retry_minutes: int = 5
    supplier_orders_max_attempts: int = 12
    price_markup_percent: float = 15.0  # наценка к цене поставщика, %
    delivery_extra_days: int = 1  # сдвиг сроков поставки относительно поставщика, дней

    site_name: str = "Das-Auto-Parts"
    site_city: str = "Санкт-Петербург, склад Шушары"
    site_phone: str = "8-800-700-95-70"
    timezone: str = "Europe/Moscow"  # в каком поясе показывать даты и сроки


@lru_cache
def get_settings() -> Settings:
    return Settings()
