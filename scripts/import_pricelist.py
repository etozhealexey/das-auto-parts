"""Импорт прайс-листа autoeuro вручную.

Запуск: uv run python -m scripts.import_pricelist [--file PATH] [--force]
  --file   локальный ZIP или CSV вместо скачивания по PRICE_LIST_URL
  --force  скачать и импортировать, даже если файл на сервере не менялся с прошлого раза

Адрес прайса содержит токен аккаунта, поэтому он не печатается: в итоге только склад,
имя файла и счётчики, в логе — только хост.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.services.pricelist.importer import run_import


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Импорт прайс-листа autoeuro в каталог")
    parser.add_argument("--file", type=Path, help="локальный ZIP или CSV вместо скачивания")
    parser.add_argument("--force", action="store_true", help="не делать условный запрос по ETag")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx на уровне INFO пишет каждый запрос с полным адресом — а в нём токен
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.file is not None and not args.file.is_file():
        print(f"файл не найден: {args.file}")
        return 2
    record = await run_import(force=args.force, source=args.file)
    if record is None:
        print("импорт не выполнялся: выключен, файл не изменился или уже идёт (см. лог выше)")
        return 1
    duration = ""
    if record.started_at and record.finished_at:
        seconds = (record.finished_at - record.started_at).total_seconds()
        finished = record.finished_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        duration = f", завершён {finished}, длительность {seconds:.1f} с"
    print(f"импорт №{record.id}, статус: {record.status}{duration}")
    print(f"склад: {record.warehouse}, файл: {record.file_name or '-'}")
    print(
        f"строк: {record.rows_total}, импортировано: {record.rows_imported}, "
        f"пропущено: {record.rows_skipped}, брендов: {record.brands_count}"
    )
    if record.message:
        print(record.message)
    return 0 if record.status == "done" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
