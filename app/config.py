from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://dasauto:dasauto@localhost:5432/dasauto"
    app_env: Literal["local", "production"] = "local"
    session_https_only: bool = False
    allowed_hosts: list[str] = ["*"]
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

    # Прайс-лист склада autoeuro (ZIP с CSV) для каталога по брендам и категориям.
    # Ссылка выдаётся в кабинете autoeuro → Настройки → Прайс-лист; пусто — импорт выключен
    price_list_url: str = ""
    price_list_refresh_minutes: int = 30  # как часто проверять, не обновился ли файл
    price_list_warehouse: str = "Санкт-Петербург ЦС"  # отображаемое имя склада из прайса
    price_list_request_timeout: float = 120.0  # таймаут скачивания, секунд

    site_name: str = "Das-Auto-Parts"
    site_city: str = "Санкт-Петербург, склад Шушары"
    site_phone: str = "8-800-700-95-70"
    timezone: str = "Europe/Moscow"  # в каком поясе показывать даты и сроки


    @model_validator(mode="after")
    def production_safety(self):
        if self.app_env == "production":
            if self.debug or not self.session_https_only:
                raise ValueError("Production требует DEBUG=false и SESSION_HTTPS_ONLY=true")
            if len(self.secret_key) < 32 or self.secret_key == "change-me-in-production":
                raise ValueError("Production требует отдельный SECRET_KEY длиной от 32 символов")
            if not self.allowed_hosts or "*" in self.allowed_hosts:
                raise ValueError("Production требует явный ALLOWED_HOSTS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
