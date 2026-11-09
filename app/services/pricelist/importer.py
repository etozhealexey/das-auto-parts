"""Импорт прайс-листа склада autoeuro: скачивание, разбор CSV, замена строк, расписание.

Прайс — ZIP с одним CSV (UTF-8, запятая, кавычки, заголовок). Цены в нём закупочные;
наценка применяется при показе (services.pricing.policy). Каждый импорт целиком заменяет
строки своего склада и оставляет запись PriceListImport со счётчиками и статусом.
Файл на сервере перезаписывается при каждой генерации, поэтому повторные проверки идут
условным запросом по ETag: если файл не менялся, ничего не делаем.
"""

import asyncio
import csv
import io
import logging
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import SessionLocal
from app.models import PriceListImport, PriceListItem
from app.services.pricelist.categories import assign_category
from app.services.pricelist.slug import slugify
from app.services.pricing.base import normalize_code

log = logging.getLogger(__name__)

PROVIDER = "autoeuro"
BATCH_SIZE = 2000
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_CSV_BYTES = 256 * 1024 * 1024
IMPORT_LOCK_ID = 2026102701
FIRST_CHECK_DELAY = 10  # секунд после старта приложения до первой проверки
MIN_REFRESH_SECONDS = 60  # нижняя граница интервала проверок, даже если в настройках меньше

# Колонки CSV → поля строки. «Марка» и «Комментарий» всегда пусты и не используются.
COL_BRAND = "Производитель"
COL_KEY = "КаталожныйНомер"
COL_CODE = "НомерПроизводителя"
COL_OEM = "ОригинальныйНомер"
COL_NAME = "Применение"
COL_PRICE = "Цена"
COL_UNIT = "Единица"
COL_QTY = "Наличие"
COL_MIN_PACK = "МинУпаковка"
COL_CURRENCY = "Валюта"
COL_TNVED = "КодТНВЭД"
COL_OKPD2 = "КодОКПД2"
COL_CERT = "РегистрационныйНомерСертификата"
COL_CERT_URL = "СсылкаНаСертификат"
COL_GTIN = "GTIN"

CURRENCIES = {"руб.": "RUB", "руб": "RUB", "rub": "RUB"}

# Длины колонок price_list_items: значения из файла обрезаются до них.
LEN_KEY, LEN_BRAND, LEN_CODE, LEN_NAME, LEN_SHORT, LEN_CERT, LEN_CERT_URL, LEN_CURRENCY = (
    80, 100, 60, 200, 20, 120, 200, 8,
)  # fmt: skip

_import_lock = asyncio.Lock()


class PriceListError(Exception):
    """Прайс не удалось скачать или разобрать."""


@dataclass(slots=True)
class PriceRow:
    """Строка прайса после разбора — поля PriceListItem без id, provider, warehouse, import_id."""

    item_key: str
    brand: str
    brand_slug: str
    code: str
    code_norm: str
    oem_code: str | None
    oem_code_norm: str | None
    name: str
    category: str
    supplier_price: Decimal
    currency: str
    unit: str
    qty: int
    min_pack: int
    gtin: str | None
    tnved: str | None
    okpd2: str | None
    certificate: str | None
    certificate_url: str | None


@dataclass(slots=True)
class ParseStats:
    """Счётчики разбора: сколько строк в файле и сколько пропущено (по причинам)."""

    rows_total: int = 0
    rows_skipped: int = 0
    reasons: Counter[str] = field(default_factory=Counter)

    def skip(self, reason: str) -> None:
        self.rows_skipped += 1
        self.reasons[reason] += 1

    def summary(self) -> str:
        if not self.reasons:
            return ""
        return "пропущено: " + ", ".join(f"{r} {n}" for r, n in self.reasons.most_common())


@dataclass(slots=True)
class FetchResult:
    status: str  # ok | not_modified
    content: bytes | None
    etag: str | None
    last_modified: str | None
    file_name: str


def _cut(value: str | None, limit: int) -> str | None:
    """Обрезать до длины колонки; пустая строка → None."""
    text = (value or "").strip()
    return text[:limit] if text else None


def _parse_price(value: str) -> Decimal | None:
    try:
        price = Decimal(value.strip().replace(",", "."))
        if not price.is_finite() or price <= 0 or price >= Decimal(10000000000):
            return None
        price = price.quantize(Decimal("0.01"))
        return price if 0 < price < Decimal(10000000000) else None
    except InvalidOperation:
        return None


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_row(raw: dict[str, str], stats: ParseStats) -> PriceRow | None:
    """Строка CSV → PriceRow. None, если строку надо пропустить (причина уходит в stats)."""
    brand = _cut(raw.get(COL_BRAND), LEN_BRAND)
    code = _cut(raw.get(COL_CODE), LEN_CODE)
    name = _cut(raw.get(COL_NAME), LEN_NAME)
    item_key = _cut(raw.get(COL_KEY), LEN_KEY)
    if not (brand and code and name and item_key):
        stats.skip("нет бренда, артикула или названия")
        return None
    price = _parse_price(raw.get(COL_PRICE) or "")
    if price is None:
        stats.skip("некорректная цена")
        return None
    qty = _parse_int(raw.get(COL_QTY) or "")
    if qty is None or qty > 2147483647:
        stats.skip("некорректный остаток")
        return None
    if qty <= 0:
        stats.skip("нулевой остаток")
        return None
    min_pack = _parse_int(raw.get(COL_MIN_PACK) or "") or 1
    if min_pack > 2147483647:
        stats.skip("некорректная упаковка")
        return None
    oem_code = _cut(raw.get(COL_OEM), LEN_CODE)
    currency_raw = (raw.get(COL_CURRENCY) or "").strip()
    currency = CURRENCIES.get(currency_raw.lower(), currency_raw.upper() or "RUB")[:LEN_CURRENCY]
    if currency != "RUB":
        raise PriceListError("Прайс содержит цены не в рублях; конвертация не настроена")
    return PriceRow(
        item_key=item_key,
        brand=brand,
        brand_slug=slugify(brand)[:LEN_BRAND],
        code=code,
        code_norm=normalize_code(code)[:LEN_CODE],
        oem_code=oem_code,
        oem_code_norm=normalize_code(oem_code)[:LEN_CODE] if oem_code else None,
        name=name,
        category=assign_category(name),
        supplier_price=price,
        currency=currency,
        unit=_cut(raw.get(COL_UNIT), LEN_SHORT) or "шт.",
        qty=qty,
        min_pack=max(min_pack, 1),
        gtin=_cut(raw.get(COL_GTIN), LEN_SHORT),
        tnved=_cut(raw.get(COL_TNVED), LEN_SHORT),
        okpd2=_cut(raw.get(COL_OKPD2), LEN_SHORT),
        certificate=_cut(raw.get(COL_CERT), LEN_CERT),
        certificate_url=_cut(raw.get(COL_CERT_URL), LEN_CERT_URL),
    )


def iter_rows(text: str, *, stats: ParseStats | None = None) -> Iterator[PriceRow]:
    """Разобрать CSV прайса. Негодные строки пропускаются и считаются в stats."""
    stats = stats if stats is not None else ParseStats()
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if not reader.fieldnames or COL_KEY not in reader.fieldnames:
        raise PriceListError(f"В CSV нет колонки «{COL_KEY}» — это не прайс autoeuro")
    required = {COL_KEY, COL_BRAND, COL_CODE, COL_NAME, COL_PRICE, COL_QTY, COL_CURRENCY}
    missing = required - set(reader.fieldnames)
    if missing:
        raise PriceListError(
            "В CSV отсутствуют обязательные колонки: " + ", ".join(sorted(missing))
        )
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise PriceListError("В CSV повторяются заголовки колонок")
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            raise PriceListError(f"Неверное число колонок в строке {reader.line_num}")
        stats.rows_total += 1
        row = parse_row(raw, stats)
        if row is not None:
            yield row


def extract_csv(data: bytes, file_name: str) -> tuple[str, str]:
    """Достать единственный CSV из ZIP или принять сами байты как CSV.

    Возвращает (имя csv, текст). BOM в начале файла отбрасывается. Имя файла в тексты
    ошибок не попадает: у скачанного прайса оно взято из адреса, а адрес — секрет.
    """
    if len(data) > MAX_CSV_BYTES:
        raise PriceListError("Прайс превышает допустимый размер")
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise PriceListError("В архиве прайса нет CSV")
            if len(names) != 1:
                raise PriceListError("В архиве несколько CSV: выберите прайс одного склада")
            if archive.getinfo(names[0]).file_size > MAX_CSV_BYTES:
                raise PriceListError("CSV превышает допустимый размер")
            csv_name, raw = Path(names[0]).name, archive.read(names[0])
    else:
        csv_name, raw = file_name, data
    try:
        return csv_name, raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PriceListError("CSV прайса не в кодировке UTF-8") from exc


async def fetch_price_list(
    url: str,
    *,
    etag: str | None = None,
    timeout: float = 120.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Скачать прайс. С etag — условный запрос: 304 → not_modified без содержимого.

    В логах и ошибках только хост: адрес прайса содержит токен аккаунта.
    """
    parts = urlsplit(url)
    host = parts.hostname or "?"
    headers = {"If-None-Match": etag} if etag else {}
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True, timeout=timeout, transport=transport
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            content = bytearray()
            if response.is_success:
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_DOWNLOAD_BYTES:
                        raise PriceListError("Скачиваемый прайс превышает допустимый размер")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # текст исключения httpx может содержать адрес с токеном — наружу только тип ошибки
        raise PriceListError(f"Не удалось скачать прайс с {host}: {type(exc).__name__}") from None
    log.info("Прайс %s: HTTP %s", host, response.status_code)
    if response.status_code == 304:
        return FetchResult("not_modified", None, etag, None, Path(parts.path).name or "pricelist")
    if not response.is_success:
        raise PriceListError(f"Прайс с {host}: HTTP {response.status_code}")
    # после редиректа имя файла берём из конечного адреса: там обычно настоящее имя архива
    file_name = Path(response.url.path).name or Path(parts.path).name or "pricelist"
    return FetchResult(
        "ok",
        bytes(content),
        response.headers.get("ETag"),
        response.headers.get("Last-Modified"),
        file_name,
    )


def _item_values(row: PriceRow, *, import_id: int | None, provider: str, warehouse: str) -> dict:
    return {
        "provider": provider,
        "warehouse": warehouse,
        "import_id": import_id,
        "item_key": row.item_key,
        "brand": row.brand,
        "brand_slug": row.brand_slug,
        "code": row.code,
        "code_norm": row.code_norm,
        "oem_code": row.oem_code,
        "oem_code_norm": row.oem_code_norm,
        "name": row.name,
        "category": row.category,
        "supplier_price": row.supplier_price,
        "currency": row.currency,
        "unit": row.unit,
        "qty": row.qty,
        "min_pack": row.min_pack,
        "gtin": row.gtin,
        "tnved": row.tnved,
        "okpd2": row.okpd2,
        "certificate": row.certificate,
        "certificate_url": row.certificate_url,
    }


async def _insert_batch(session: AsyncSession, batch: list[dict]) -> int:
    """Порция строк одним executemany по таблице (Core, без ORM-обвязки) — работает везде.

    Многострочный insert().values(порция) не подходит: 2000 строк × 22 колонки больше
    лимита параметров и asyncpg (32767), и SQLite (32766), а на меньших порциях он в разы
    медленнее из-за компиляции огромного SQL. Замер на реальном прайсе (64 тыс. строк,
    Postgres): executemany по таблице ~3 с, ORM bulk insert ~7 с, values() по 1000 ~24 с.
    """
    await session.execute(insert(PriceListItem.__table__), batch)
    return len(batch)


async def replace_items(
    session: AsyncSession,
    rows: Iterable[PriceRow],
    *,
    import_id: int | None,
    provider: str = PROVIDER,
    warehouse: str,
    commit: bool = True,
) -> tuple[int, int]:
    """Заменить все строки склада новыми в одной транзакции.

    Дубликаты item_key внутри файла: остаётся первая строка. Возвращает (вставлено, брендов).
    """
    seen: set[str] = set()
    brands: set[str] = set()
    batch: list[dict] = []
    inserted = 0
    try:
        await session.execute(
            delete(PriceListItem).where(
                PriceListItem.provider == provider, PriceListItem.warehouse == warehouse
            )
        )
        for row in rows:
            if row.item_key in seen:
                continue
            seen.add(row.item_key)
            brands.add(row.brand_slug)
            batch.append(
                _item_values(row, import_id=import_id, provider=provider, warehouse=warehouse)
            )
            if len(batch) >= BATCH_SIZE:
                inserted += await _insert_batch(session, batch)
                batch = []
        if batch:
            inserted += await _insert_batch(session, batch)
        if not inserted:
            raise PriceListError("В прайсе нет пригодных позиций; прежний каталог сохранён")
        if commit:
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return inserted, len(brands)


async def _last_etag(session: AsyncSession, *, source: str, warehouse: str) -> str | None:
    """ETag последнего успешного импорта с того же адреса — для условного запроса."""
    latest = await session.scalar(
        select(PriceListImport)
        .where(PriceListImport.status == "done", PriceListImport.warehouse == warehouse)
        .order_by(PriceListImport.id.desc())
        .limit(1)
    )
    return latest.etag if latest and latest.source == source else None


def _public_source(url: str) -> str:
    """Хост и хеш полного адреса: токен может находиться и в пути, и в query."""
    parts = urlsplit(url)
    return f"{parts.hostname or ''}/sha256:{sha256(url.encode()).hexdigest()}"


async def run_import(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    force: bool = False,
    source: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PriceListImport | None:
    """Выполнить импорт: из локального файла (source) или по PRICE_LIST_URL.

    None — импорт не выполнялся: выключен настройкой, файл не изменился (304) или уже идёт
    параллельный импорт. Иначе запись PriceListImport со статусом done или failed.
    Без force берётся ETag последнего успешного импорта и делается условный запрос.
    """
    if _import_lock.locked():
        log.warning("Импорт прайса уже выполняется — повторный запуск пропущен")
        return None
    factory = session_factory or SessionLocal
    # Отдельная транзакция держит блокировку, пока рабочая сессия сохраняет статусы.
    # Она освобождается и при отмене, и при аварийном завершении соединения.
    async with _import_lock, factory() as guard:
        if guard.get_bind().dialect.name == "postgresql":
            acquired = await guard.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK_ID}
            )
            if not acquired:
                log.info("Импорт прайса уже выполняется в другом процессе")
                return None
        async with factory() as session:
            return await _run_import(session, force=force, source=source, transport=transport)


async def _run_import(
    session: AsyncSession,
    *,
    force: bool,
    source: Path | None,
    transport: httpx.AsyncBaseTransport | None,
) -> PriceListImport | None:
    settings = get_settings()
    warehouse = settings.price_list_warehouse
    etag = last_modified = None
    if source is not None:
        public_source = file_name = source.name
        label = "локальный файл"
        try:
            if source.stat().st_size > MAX_CSV_BYTES:
                raise PriceListError("Прайс превышает допустимый размер")
            data = await asyncio.to_thread(source.read_bytes)
        except (OSError, PriceListError) as exc:
            message = str(exc) if isinstance(exc, PriceListError) else type(exc).__name__
            return await _save_failed(session, public_source, warehouse, message)
    else:
        url = settings.price_list_url
        if not url:
            log.info("Импорт прайса выключен: PRICE_LIST_URL пуст")
            return None
        public_source = _public_source(url)
        label = urlsplit(url).hostname or "?"  # в лог — только хост
        known_etag = None
        if not force:
            known_etag = await _last_etag(session, source=public_source, warehouse=warehouse)
        try:
            fetched = await fetch_price_list(
                url,
                etag=known_etag,
                timeout=settings.price_list_request_timeout,
                transport=transport,
            )
        except PriceListError as exc:
            log.warning("Импорт прайса не выполнен: %s", exc)
            return await _save_failed(session, public_source, warehouse, str(exc))
        # сервер может проигнорировать If-None-Match и отдать тот же файл с тем же ETag
        if fetched.status == "not_modified" or (known_etag and fetched.etag == known_etag):
            log.info("Прайс на %s не изменился, импорт не нужен", label)
            return None
        data, file_name = fetched.content or b"", fetched.file_name
        etag, last_modified = fetched.etag, fetched.last_modified

    record = PriceListImport(
        started_at=datetime.now(UTC),
        status="running",
        source=public_source[:300],
        etag=etag and etag[:120],
        last_modified=last_modified and last_modified[:80],
        # у скачанного прайса имя из адреса не пишем: его заменит имя CSV из архива
        file_name=file_name[:200] if source is not None else None,
        warehouse=warehouse,
    )
    session.add(record)
    await session.commit()
    import_id = record.id
    log.info("Импорт прайса %s (%s): запись №%s", label, warehouse, import_id)

    stats = ParseStats()
    csv_name: str | None = None
    try:
        csv_name, text = await asyncio.to_thread(extract_csv, data, file_name)
        rows = await asyncio.to_thread(lambda: list(iter_rows(text, stats=stats)))
        inserted, brands = await replace_items(
            session,
            rows,
            import_id=import_id,
            provider=PROVIDER,
            warehouse=warehouse,
            commit=False,
        )
        duplicates = stats.rows_total - stats.rows_skipped - inserted
        if duplicates:
            stats.reasons["дубликат ключа"] += duplicates
        record.status = "done"
        record.rows_total = stats.rows_total
        record.rows_imported = inserted
        record.rows_skipped = stats.rows_total - inserted  # негодные строки и дубли ключа
        record.brands_count = brands
        record.message = stats.summary() or None
        log.info(
            "Импорт прайса №%s: строк %s, импортировано %s, пропущено %s, брендов %s",
            import_id, stats.rows_total, inserted, record.rows_skipped, brands,
        )  # fmt: skip
        if csv_name:
            record.file_name = csv_name[:200]
        record.finished_at = datetime.now(UTC)
        # Каталог и успешный статус фиксируются вместе.
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — фиксируем сбой импорта, сохраняя прежний каталог
        await session.rollback()
        await session.refresh(record)
        record.status = "failed"
        detail = type(exc).__name__ if isinstance(exc, SQLAlchemyError) else str(exc)
        record.message = f"{type(exc).__name__}: {detail}"[:2000]
        record.finished_at = datetime.now(UTC)
        await session.commit()
        log.warning("Импорт прайса №%s завершился ошибкой: %s", import_id, record.message)
    await session.refresh(record)
    return record


async def _save_failed(
    session: AsyncSession, source: str, warehouse: str, message: str
) -> PriceListImport:
    now = datetime.now(UTC)
    record = PriceListImport(
        started_at=now,
        finished_at=now,
        status="failed",
        source=source[:300],
        warehouse=warehouse,
        message=message[:2000],
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def refresh_loop(stop: asyncio.Event) -> None:
    """Фоновый цикл в процессе приложения: импорт прайса по расписанию.

    Первая проверка через FIRST_CHECK_DELAY секунд после старта, затем раз в
    PRICE_LIST_REFRESH_MINUTES. Ошибки пишутся в лог и не останавливают цикл.
    """
    settings = get_settings()
    if not settings.price_list_url:
        log.info("Импорт прайса по расписанию выключен: PRICE_LIST_URL пуст")
        return
    interval = max(MIN_REFRESH_SECONDS, settings.price_list_refresh_minutes * 60)
    timeout: float = FIRST_CHECK_DELAY
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=timeout)
            break
        except TimeoutError:
            pass
        timeout = interval
        try:
            await run_import()
        except Exception:
            log.exception("Сбой цикла обновления прайса")
