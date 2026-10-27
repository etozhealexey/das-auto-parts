"""scripts/autoeuro_check.py на поддельном API: скрипт должен печатать то, что реально отдаёт API."""

import functools

import pytest

import scripts.autoeuro_check as check
from app.config import Settings
from app.services.pricing.autoeuro import AutoEuroProvider
from tests.fake_autoeuro import DELIVERY_VORONEZH, FakeAutoEuro


@pytest.fixture
def patched_script(monkeypatch, fake_api: FakeAutoEuro):
    settings = Settings(
        _env_file=None,
        price_provider="autoeuro",
        autoeuro_api_key="test-key",
        autoeuro_delivery_key="",
        autoeuro_base_url="https://api.autoeuro.test/api/v2/json",
        price_markup_percent=15,
        delivery_extra_days=1,
    )
    monkeypatch.setattr(check, "get_settings", lambda: settings)
    monkeypatch.setattr(check, "AutoEuroProvider", functools.partial(AutoEuroProvider, transport=fake_api.transport))
    return fake_api


async def test_script_without_key_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(check, "get_settings", lambda: Settings(_env_file=None, autoeuro_api_key=""))
    assert await check.main("0446533471") == 1
    assert "AUTOEURO_API_KEY пуст" in capsys.readouterr().out


async def test_script_runs_and_prints_offers_with_markup(patched_script, capsys):
    rc = await check.main("0446533471")
    out = capsys.readouterr().out
    assert rc == 0
    assert "Вариантов получения: 3" in out
    assert "Бренды с артикулом 0446533471: ['TOYOTA', 'DAIHATSU']" in out
    assert "Предложений TOYOTA 0446533471: 4" in out
    assert "вариант получения: г. Воронеж (ул. Героев Сибиряков, 1Д)" in out
    assert "поставщик   2037.38 → клиент   2342.99 ₽" in out
    assert "срок 01.11 09:00" in out  # +1 день к сроку поставщика
    assert "аналог" in out
    # ключ API на экран не выводится
    assert "test-key" not in out
    assert len(patched_script.calls("search_items")) == 1


async def test_script_lists_delivery_names_from_api(patched_script, capsys):
    """API отдаёт поле delivery_name (а не name, как в документации) — оно должно попасть в вывод."""
    await check.main("0446533471")
    out = capsys.readouterr().out
    key, name = DELIVERY_VORONEZH["delivery_key"], DELIVERY_VORONEZH["delivery_name"]
    line = next(line for line in out.splitlines() if f"delivery_key={key}" in line)
    assert "None" not in line
    assert line == f"  delivery_key={key}  {name}  сдвиг от МСК: 0"


async def test_script_reports_api_error(patched_script, capsys):
    patched_script.errors["get_balance"] = {"code": 403, "message": "Неверный ключ"}
    rc = await check.main("0446533471")
    out = capsys.readouterr().out
    assert rc == 2
    assert "Ошибка API: autoeuro: Неверный ключ" in out
