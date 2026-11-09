"""Замена строк прайса в базе и run_import: локальный файл, HTTP с ETag, ошибки, блокировка."""

import asyncio
import functools
import logging
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import PriceListImport, PriceListItem
from app.services.pricelist import importer
from app.services.pricelist.importer import replace_items, run_import
from tests.pricelist_samples import (
    BRANDS_UNIQUE,
    CSV_NAME,
    ROWS_TOTAL,
    ROWS_UNIQUE,
    WAREHOUSE,
    make_row,
    sqlite_sessionmaker,
    zip_bytes,
)

URL = "https://pricelist.test/export/price.zip?token=SECRET-ACCOUNT-TOKEN"
ETAG = '"v1-abc"'


@pytest.fixture
async def db_sessionmaker():
    async with sqlite_sessionmaker() as factory:
        yield factory


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "price_list_url", URL)
    monkeypatch.setattr(s, "price_list_warehouse", WAREHOUSE)
    monkeypatch.setattr(s, "price_list_request_timeout", 5.0)
    return s


@pytest.fixture
def zip_path(tmp_path: Path) -> Path:
    path = tmp_path / "PriceAE_2026-10-27.zip"
    path.write_bytes(zip_bytes())
    return path


async def count_items(factory, **where) -> int:
    async with factory() as session:
        stmt = select(func.count()).select_from(PriceListItem)
        for key, value in where.items():
            stmt = stmt.where(getattr(PriceListItem, key) == value)
        return await session.scalar(stmt)


async def item_keys(factory) -> set[str]:
    async with factory() as session:
        return set((await session.scalars(select(PriceListItem.item_key))).all())


async def imports(factory) -> list[PriceListImport]:
    async with factory() as session:
        return list(
            (await session.scalars(select(PriceListImport).order_by(PriceListImport.id))).all()
        )


class PriceServer:
    """Сервер прайса для httpx.MockTransport: отдаёт ZIP, умеет 304 по ETag и сбой."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="error")
        if request.headers.get("If-None-Match") == ETAG:
            return httpx.Response(304, headers={"ETag": ETAG})
        return httpx.Response(
            200,
            content=zip_bytes(),
            headers={"ETag": ETAG, "Last-Modified": "Mon, 27 Oct 2026 11:42:24 GMT"},
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


# --- replace_items ------------------------------------------------------------------------


async def test_replace_items_is_idempotent_and_removes_stale(db_sessionmaker):
    a = make_row(item_key="A^1", code="1", brand="A", brand_slug="a")
    b = make_row(item_key="B^2", code="2", brand="B", brand_slug="b")
    c = make_row(item_key="C^3", code="3", brand="C", brand_slug="c")
    async with db_sessionmaker() as session:
        assert await replace_items(session, [a, b], import_id=None, warehouse=WAREHOUSE) == (2, 2)
    assert await item_keys(db_sessionmaker) == {"A^1", "B^2"}
    async with db_sessionmaker() as session:
        assert await replace_items(session, [a, b], import_id=None, warehouse=WAREHOUSE) == (2, 2)
    assert await count_items(db_sessionmaker) == 2  # повтор не плодит дубли
    async with db_sessionmaker() as session:
        assert await replace_items(session, [b, c], import_id=None, warehouse=WAREHOUSE) == (2, 2)
    assert await item_keys(db_sessionmaker) == {"B^2", "C^3"}  # A удалена как устаревшая


async def test_replace_items_keeps_first_duplicate_and_other_warehouse(db_sessionmaker):
    first = make_row(supplier_price=Decimal("100.00"))
    second = make_row(supplier_price=Decimal("999.00"), name="дубль")
    other = make_row(item_key="X^9", code="9", brand="X", brand_slug="x")
    async with db_sessionmaker() as session:
        assert await replace_items(session, [other], import_id=None, warehouse="Москва") == (1, 1)
        assert await replace_items(
            session, [first, second], import_id=None, warehouse=WAREHOUSE
        ) == (1, 1)
    assert await count_items(db_sessionmaker) == 2
    assert await count_items(db_sessionmaker, warehouse="Москва") == 1
    async with db_sessionmaker() as session:
        item = await session.scalar(
            select(PriceListItem).where(PriceListItem.item_key == "KAYABA^349079")
        )
    assert item.supplier_price == Decimal("100.00") and item.name != "дубль"
    assert item.provider == "autoeuro" and item.warehouse == WAREHOUSE
    assert item.updated_at is not None


# --- run_import из файла --------------------------------------------------------------------


async def test_run_import_from_local_zip(db_sessionmaker, settings, zip_path):
    record = await run_import(db_sessionmaker, source=zip_path)
    assert record is not None and record.status == "done"
    assert record.source == "PriceAE_2026-10-27.zip" and record.file_name == CSV_NAME
    assert record.warehouse == WAREHOUSE
    assert (record.rows_total, record.rows_imported, record.rows_skipped, record.brands_count) == (
        ROWS_TOTAL, ROWS_UNIQUE, ROWS_TOTAL - ROWS_UNIQUE, BRANDS_UNIQUE,
    )  # fmt: skip
    assert record.etag is None and record.finished_at is not None and record.started_at
    assert "пропущено:" in record.message and "дубликат ключа 1" in record.message
    assert await count_items(db_sessionmaker, import_id=record.id) == ROWS_UNIQUE


async def test_run_import_from_local_csv_and_repeat(db_sessionmaker, settings, tmp_path, zip_path):
    csv_path = tmp_path / CSV_NAME
    from tests.pricelist_samples import csv_bytes

    csv_path.write_bytes(csv_bytes(bom=True))
    first = await run_import(db_sessionmaker, source=csv_path)
    second = await run_import(db_sessionmaker, source=zip_path)
    assert first.status == second.status == "done"
    assert await count_items(db_sessionmaker) == ROWS_UNIQUE  # строки заменены, не добавлены
    assert await count_items(db_sessionmaker, import_id=second.id) == ROWS_UNIQUE
    assert len(await imports(db_sessionmaker)) == 2


async def test_run_import_marks_failed_on_bad_file_and_keeps_old_rows(
    db_sessionmaker, settings, zip_path, tmp_path
):
    await run_import(db_sessionmaker, source=zip_path)
    bad = tmp_path / "bad.csv"
    bad.write_text('"a","b"\n"1","2"\n', encoding="utf-8")
    record = await run_import(db_sessionmaker, source=bad)
    assert record.status == "failed" and "КаталожныйНомер" in record.message
    assert record.finished_at is not None
    assert await count_items(db_sessionmaker) == ROWS_UNIQUE


async def test_run_import_marks_failed_on_db_error_and_keeps_old_rows(
    db_sessionmaker, settings, zip_path, monkeypatch
):
    await run_import(db_sessionmaker, source=zip_path)

    async def broken(session, batch):
        raise RuntimeError("диск переполнен")

    monkeypatch.setattr(importer, "_insert_batch", broken)
    record = await run_import(db_sessionmaker, source=zip_path)
    assert record.status == "failed" and "диск переполнен" in record.message
    assert await count_items(db_sessionmaker) == ROWS_UNIQUE  # DELETE откатился


# --- run_import по URL ------------------------------------------------------------------------


async def test_run_import_disabled_without_url(db_sessionmaker, settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, "price_list_url", "")
    with caplog.at_level(logging.INFO, logger=importer.__name__):
        assert await run_import(db_sessionmaker) is None
    assert "выключен" in caplog.text
    assert await imports(db_sessionmaker) == []


async def test_run_import_http_etag_flow(db_sessionmaker, settings, caplog):
    server = PriceServer()
    with caplog.at_level(logging.INFO, logger=importer.__name__):
        record = await run_import(db_sessionmaker, transport=server.transport)
        assert record.status == "done" and record.rows_imported == ROWS_UNIQUE
        assert record.etag == ETAG and record.last_modified == "Mon, 27 Oct 2026 11:42:24 GMT"
        assert record.source == importer._public_source(URL)  # без query с токеном
        assert record.file_name == CSV_NAME
        assert "If-None-Match" not in server.requests[0].headers

        # файл не менялся: условный запрос → 304 → None, новой записи нет
        assert await run_import(db_sessionmaker, transport=server.transport) is None
        assert server.requests[1].headers["If-None-Match"] == ETAG
        assert len(await imports(db_sessionmaker)) == 1
        assert "не изменился" in caplog.text

        # force — без условного запроса, новая запись
        forced = await run_import(db_sessionmaker, force=True, transport=server.transport)
        assert forced.status == "done" and forced.id != record.id
        assert "If-None-Match" not in server.requests[2].headers
    assert await count_items(db_sessionmaker) == ROWS_UNIQUE
    # токен из адреса не попадает ни в записи, ни в лог
    assert "SECRET" not in caplog.text
    assert all("SECRET" not in (r.source or "") for r in await imports(db_sessionmaker))


async def test_run_import_logs_only_host_when_token_is_in_path(
    db_sessionmaker, settings, monkeypatch, caplog
):
    # у autoeuro номер аккаунта — в имени файла в адресе; в лог он не должен попадать
    monkeypatch.setattr(settings, "price_list_url", "https://prices.test/PriceAE_1234567890.zip")
    server = PriceServer()
    with caplog.at_level(logging.DEBUG, logger=importer.__name__):
        record = await run_import(db_sessionmaker, transport=server.transport)
        assert await run_import(db_sessionmaker, transport=server.transport) is None  # 304
    assert record.status == "done" and record.file_name == CSV_NAME  # имя CSV из архива
    assert record.source.startswith("prices.test/sha256:")
    assert "1234567890" not in record.source
    assert "prices.test" in caplog.text and "1234567890" not in caplog.text


async def test_run_import_same_etag_without_304_is_skipped(db_sessionmaker, settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        # сервер игнорирует If-None-Match и всегда отдаёт файл целиком
        return httpx.Response(200, content=zip_bytes(), headers={"ETag": ETAG})

    transport = httpx.MockTransport(handler)
    assert (await run_import(db_sessionmaker, transport=transport)).status == "done"
    assert await run_import(db_sessionmaker, transport=transport) is None
    assert len(await imports(db_sessionmaker)) == 1


async def test_run_import_http_error_is_failed_record(db_sessionmaker, settings, caplog):
    server = PriceServer()
    await run_import(db_sessionmaker, transport=server.transport)
    server.status = 500
    with caplog.at_level(logging.WARNING, logger=importer.__name__):
        record = await run_import(db_sessionmaker, transport=server.transport)
    assert record.status == "failed" and "HTTP 500" in record.message
    assert record.source == importer._public_source(URL)
    assert record.finished_at is not None
    assert await count_items(db_sessionmaker) == ROWS_UNIQUE  # старые строки на месте
    assert "SECRET" not in caplog.text and "SECRET" not in record.message
    assert [r.status for r in await imports(db_sessionmaker)] == ["done", "failed"]


async def test_fetch_price_list_follows_redirect_and_takes_file_name():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/get":
            return httpx.Response(302, headers={"Location": "https://cdn.test/f/PriceAE_1.zip"})
        return httpx.Response(200, content=b"zip", headers={"ETag": ETAG})

    result = await importer.fetch_price_list(
        "https://pricelist.test/get?token=SECRET", timeout=5, transport=httpx.MockTransport(handler)
    )
    assert (result.status, result.content, result.etag) == ("ok", b"zip", ETAG)
    assert result.file_name == "PriceAE_1.zip"


async def test_fetch_price_list_not_modified_and_error():
    server = PriceServer()
    result = await importer.fetch_price_list(URL, etag=ETAG, timeout=5, transport=server.transport)
    assert result.status == "not_modified" and result.content is None
    assert result.etag == ETAG and result.file_name == "price.zip"
    server.status = 403
    with pytest.raises(importer.PriceListError, match="HTTP 403") as exc_info:
        await importer.fetch_price_list(URL, timeout=5, transport=server.transport)
    assert "SECRET" not in str(exc_info.value)


async def test_run_import_network_error_hides_url(db_sessionmaker, settings):
    async def handler(request):
        raise httpx.ConnectError("boom", request=request)

    record = await run_import(db_sessionmaker, transport=httpx.MockTransport(handler))
    assert record.status == "failed"
    assert "ConnectError" in record.message and "pricelist.test" in record.message
    assert "SECRET" not in record.message


# --- параллельный запуск и расписание ---------------------------------------------------------


async def test_parallel_run_is_skipped(db_sessionmaker, settings, zip_path, caplog):
    with caplog.at_level(logging.WARNING, logger=importer.__name__):
        async with importer._import_lock:
            assert await run_import(db_sessionmaker, source=zip_path) is None
    assert "уже выполняется" in caplog.text
    assert await imports(db_sessionmaker) == []
    record = await run_import(db_sessionmaker, source=zip_path)  # после освобождения — работает
    assert record.status == "done"


async def test_parallel_run_in_flight(db_sessionmaker, settings):
    started, gate = asyncio.Event(), asyncio.Event()

    async def slow(request):
        started.set()
        await gate.wait()
        return httpx.Response(200, content=zip_bytes())

    transport = httpx.MockTransport(slow)
    task = asyncio.create_task(run_import(db_sessionmaker, transport=transport))
    await asyncio.wait_for(started.wait(), timeout=2)  # первый импорт уже качает файл
    assert await run_import(db_sessionmaker, transport=transport) is None
    gate.set()
    record = await task
    assert record.status == "done"
    assert len(await imports(db_sessionmaker)) == 1


async def test_refresh_loop_returns_when_disabled(settings, monkeypatch):
    monkeypatch.setattr(settings, "price_list_url", "")
    await asyncio.wait_for(importer.refresh_loop(asyncio.Event()), timeout=1)


async def test_refresh_loop_runs_import_after_delay(settings, monkeypatch):
    calls: list[int] = []
    stop = asyncio.Event()

    async def fake_run_import():
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("ошибка не должна убить цикл")

    # интервал в минутах сжимается до миллисекунд
    monkeypatch.setattr(settings, "price_list_refresh_minutes", 0)
    monkeypatch.setattr(importer, "MIN_REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(importer, "FIRST_CHECK_DELAY", 0.01)
    monkeypatch.setattr(importer, "run_import", fake_run_import)
    await asyncio.wait_for(importer.refresh_loop(stop), timeout=2)
    assert calls == [1, 1]


async def test_refresh_loop_stops_before_first_check(settings, monkeypatch):
    calls: list[int] = []

    async def fake_run_import():
        calls.append(1)

    monkeypatch.setattr(importer, "run_import", fake_run_import)
    stop = asyncio.Event()
    task = asyncio.create_task(importer.refresh_loop(stop))
    await asyncio.sleep(0)
    stop.set()  # остановка приложения раньше первой проверки (через 10 с)
    await asyncio.wait_for(task, timeout=1)
    assert calls == []


# --- скрипт ручного импорта -----------------------------------------------------------------


async def test_cli_imports_local_file(db_sessionmaker, settings, zip_path, monkeypatch, capsys):
    from scripts import import_pricelist

    monkeypatch.setattr(
        import_pricelist, "run_import", functools.partial(run_import, db_sessionmaker)
    )
    assert await import_pricelist.main(["--file", str(zip_path)]) == 0
    out = capsys.readouterr().out
    assert "статус: done" in out and f"склад: {WAREHOUSE}" in out
    assert f"строк: {ROWS_TOTAL}, импортировано: {ROWS_UNIQUE}" in out
    assert "SECRET" not in out and "pricelist.test" not in out


async def test_cli_missing_file(tmp_path, capsys):
    from scripts import import_pricelist

    assert await import_pricelist.main(["--file", str(tmp_path / "nope.zip")]) == 2
    assert "не найден" in capsys.readouterr().out


@pytest.mark.parametrize(
    "kind", ["empty", "invalid", "missing_columns", "truncated", "foreign_currency"]
)
async def test_rejects_incomplete_snapshot_without_erasing_catalog(
    db_sessionmaker, settings, zip_path, tmp_path, kind
):
    from tests.pricelist_samples import HEADER, line

    first = await run_import(db_sessionmaker, source=zip_path)
    good = line("BOSCH", "A1", "", "Фильтр", "100")
    contents = {
        "empty": HEADER + "\n",
        "invalid": HEADER + "\n" + good.replace('"100"', '"нет"'),
        "missing_columns": '"КаталожныйНомер"\n"A1"',
        "truncated": HEADER + "\n" + good + '\n"BOSCH","A2"',
        "foreign_currency": HEADER + "\n" + good.replace('"руб."', '"USD"'),
    }
    path = tmp_path / "broken.csv"
    path.write_text(contents[kind], encoding="utf-8")
    failed = await run_import(db_sessionmaker, source=path)
    assert failed.status == "failed"
    assert await count_items(db_sessionmaker, import_id=first.id) == ROWS_UNIQUE


async def test_commit_failure_keeps_previous_snapshot(
    db_sessionmaker, settings, zip_path, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    first = await run_import(db_sessionmaker, source=zip_path)
    original = AsyncSession.commit

    async def commit(session):
        if any(isinstance(obj, PriceListImport) and obj.status == "done" for obj in session.dirty):
            raise RuntimeError("сбой финального коммита")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    failed = await run_import(db_sessionmaker, source=zip_path)
    assert failed.status == "failed"
    assert await count_items(db_sessionmaker, import_id=first.id) == ROWS_UNIQUE


async def test_download_size_limit(settings, monkeypatch):
    monkeypatch.setattr(importer, "MAX_DOWNLOAD_BYTES", 10)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 11))
    with pytest.raises(importer.PriceListError, match="размер"):
        await importer.fetch_price_list(URL, transport=transport)


def test_source_identity_includes_private_query_without_disclosing_it():
    other = URL.replace("SECRET-ACCOUNT-TOKEN", "ANOTHER-SECRET")
    assert importer._public_source(URL) != importer._public_source(other)
    assert "SECRET" not in importer._public_source(URL)


async def test_remote_snapshot_restored_after_manual_import(db_sessionmaker, settings, zip_path):
    server = PriceServer()
    await run_import(db_sessionmaker, transport=server.transport)
    manual = await run_import(db_sessionmaker, source=zip_path)
    restored = await run_import(db_sessionmaker, transport=server.transport)
    assert restored.status == "done" and restored.id != manual.id
    assert "If-None-Match" not in server.requests[-1].headers
    assert await count_items(db_sessionmaker, import_id=restored.id) == ROWS_UNIQUE
