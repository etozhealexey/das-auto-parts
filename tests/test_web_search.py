"""HTTP-слой поиска без базы: /api/v1/search и страница /search с поддельным сервисом цен."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import app.routers.api as api_module
import app.routers.pages as pages_module
from app.db import get_session
from app.deps import get_current_customer
from app.main import app
from app.services.pricing.base import PriceProviderError
from app.services.pricing.policy import PricePolicy
from app.services.pricing.service import PricingService
from tests.helpers import dt, make_offer
from tests.test_service import BRANDS, FakeProvider, FakeSession

TOYOTA_OFFERS = [
    make_offer(cross_type=None, offer_key="T1", price=2037.38, amount=8, delivery_time=dt(31),
               order_before=dt(27, 15), supplier="Партнёр Авто-Евро", rejects=23.41, raw={"ttl": 1}),
    make_offer(brand="SANGSIN", code="SP2080", name="Колодки TOYOTA CAMRY 06=>", cross_type=3,
               offer_key="S1", price=1395.47, is_stock=True, is_dealer=True, warehouse="Воронеж",
               supplier="Официальный дилер через Авто-Евро", amount=1),
]


def make_client(monkeypatch, items: dict) -> TestClient:
    service = PricingService(
        provider=FakeProvider(BRANDS, items), cache_ttl=timedelta(minutes=30), policy=PricePolicy()
    )
    monkeypatch.setattr(api_module, "get_pricing_service", lambda: service)
    monkeypatch.setattr(pages_module, "get_pricing_service", lambda: service)

    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_current_customer] = lambda: None
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    yield make_client(monkeypatch, {"TOYOTA": TOYOTA_OFFERS, "DAIHATSU": []})
    app.dependency_overrides.clear()


@pytest.fixture
def failing_client(monkeypatch):
    error = PriceProviderError("autoeuro: Превышен лимит запросов", code=429)
    yield make_client(monkeypatch, {"TOYOTA": error, "DAIHATSU": error})
    app.dependency_overrides.clear()


def test_api_search_returns_client_prices_without_supplier_fields(client):
    response = client.get("/api/v1/search", params={"code": "04465-33471"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [o["offer_key"] for o in data] == ["T1", "S1"]

    original = data[0]
    assert original["price"] == 2342.99
    assert "supplier_price" not in original
    assert "raw" not in original
    assert original["delivery_time"] == "2026-11-01T09:00:00+03:00"  # +1 день
    assert original["order_before"] == "2026-10-27T15:00:00+03:00"  # не сдвигается
    assert original["supplier"] == "Партнёр Авто-Евро"
    assert original["cross_type"] is None
    assert original["currency"] == "RUB"

    analog = data[1]
    assert analog["price"] == 1604.79
    assert analog["cross_type"] == 3
    assert analog["is_dealer"] is True


def test_api_search_validates_code_length(client):
    assert client.get("/api/v1/search", params={"code": "1"}).status_code == 422
    assert client.get("/api/v1/search").status_code == 422


def test_api_search_provider_error_is_502(failing_client):
    response = failing_client.get("/api/v1/search", params={"code": "0446533471"})
    assert response.status_code == 502
    assert response.json()["detail"] == "autoeuro: Превышен лимит запросов"


def test_search_page_renders_originals_and_analogs_with_markup(client):
    response = client.get("/search", params={"q": "0446533471", "mode": "article"})
    assert response.status_code == 200
    html = response.text
    assert "Искомая деталь" in html
    assert "Аналоги и замены" in html
    # фильтр money разделяет тысячи узким неразрывным пробелом (U+202F)
    assert "2\u202f343 ₽" in html  # 2037.38 + 15 %
    assert "1\u202f605 ₽" in html  # 1395.47 + 15 %
    assert "2\u202f037" not in html  # цена поставщика не показывается
    assert "01.11" in html  # срок +1 день
    assert "заказ до 27.10 15:00" in html
    assert "Партнёр Авто-Евро" in html
    assert "SANGSIN" in html and "SP2080" in html
    assert "аналоги: 1 позиций, 1 предложений" in html
    assert "источник: демо-данные" in html  # провайдер в тесте не autoeuro


def test_search_page_shows_provider_error(failing_client):
    response = failing_client.get("/search", params={"q": "0446533471"})
    assert response.status_code == 200
    assert "autoeuro: Превышен лимит запросов" in response.text
    assert "Искомая деталь" not in response.text


def test_search_page_without_query(client):
    response = client.get("/search")
    assert response.status_code == 200
    assert "Введите артикул" in response.text


def test_search_page_vin_mode_is_stub(client):
    response = client.get("/search", params={"q": "XW8ZZZ61ZEG000000", "mode": "vin"})
    assert response.status_code == 200
    assert "будет подключён на следующем этапе" in response.text
