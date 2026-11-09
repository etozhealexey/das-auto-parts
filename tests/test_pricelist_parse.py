"""Разбор CSV прайса autoeuro: нормализация полей, пропуск негодных строк, ZIP и BOM."""

import io
import zipfile
from decimal import Decimal

import pytest

from app.services.pricelist.categories import CATEGORY_BY_SLUG, assign_category
from app.services.pricelist.importer import (
    ParseStats,
    PriceListError,
    PriceRow,
    extract_csv,
    iter_rows,
)
from app.services.pricelist.slug import slugify
from tests.pricelist_samples import (
    CERT,
    CERT_URL,
    CSV_NAME,
    CSV_TEXT,
    HEADER,
    ROWS_TOTAL,
    ROWS_VALID,
    csv_bytes,
    line,
    zip_bytes,
)


@pytest.fixture
def parsed() -> tuple[list[PriceRow], ParseStats]:
    stats = ParseStats()
    rows = list(iter_rows(CSV_TEXT, stats=stats))
    return rows, stats


def by_key(rows: list[PriceRow], key: str) -> PriceRow:
    return next(r for r in rows if r.item_key == key)


def test_counts_valid_and_skipped(parsed):
    rows, stats = parsed
    assert len(rows) == ROWS_VALID
    assert stats.rows_total == ROWS_TOTAL
    assert stats.rows_skipped == ROWS_TOTAL - ROWS_VALID
    assert stats.reasons == {
        "нулевой остаток": 1,
        "некорректная цена": 1,
        "нет бренда, артикула или названия": 1,
    }
    assert "пропущено:" in stats.summary()
    # дубль ключа на этапе разбора не отбрасывается — это делает replace_items
    assert sum(r.item_key == "KAYABA^349079" for r in rows) == 2


def test_skipped_rows_are_not_in_result(parsed):
    rows, _ = parsed
    keys = {r.item_key for r in rows}
    assert "BOSCH^0 986 424 797" not in keys  # остаток 0
    assert "NGK^BKR6EKPB-11" not in keys  # цена «нет»
    assert "^12345" not in keys  # пустой бренд


def test_full_row_fields_and_trailing_spaces(parsed):
    rows, _ = parsed
    row = by_key(rows, "KAYABA^349079")
    assert row.brand == "KAYABA" and row.brand_slug == "kayaba"
    assert row.code == "349079" and row.code_norm == "349079"
    assert row.oem_code == "E6210JD02A" and row.oem_code_norm == "E6210JD02A"
    assert row.name.startswith("Амортизатор зад.NISSAN QASHQAI J10E")
    assert row.supplier_price == Decimal("7064.47")
    assert isinstance(row.supplier_price, Decimal)
    assert row.currency == "RUB" and row.unit == "шт."
    assert row.qty == 2 and row.min_pack == 1
    assert row.certificate == CERT  # хвост пробелов обрезан
    assert row.certificate_url == CERT_URL
    assert row.gtin is None and row.tnved is None and row.okpd2 is None


def test_code_normalization_and_optional_fields(parsed):
    rows, _ = parsed
    mann = by_key(rows, "MANN-FILTER^W 712/75")
    assert mann.code == "W 712/75" and mann.code_norm == "W71275"
    assert mann.oem_code is None and mann.oem_code_norm is None  # пустой OEM
    assert mann.gtin == "4011558722401"
    assert mann.tnved == "8421230009" and mann.okpd2 == "28.29.13.110"
    assert mann.brand_slug == "mann-filter"

    hk = by_key(rows, "Hyundai/Kia^26300-35503")
    assert hk.brand == "Hyundai/Kia" and hk.brand_slug == "hyundai-kia"
    assert hk.code_norm == "2630035503" and hk.oem_code_norm == "2630035503"
    assert hk.supplier_price == Decimal("412.50")


def test_units_and_min_pack_default(parsed):
    rows, _ = parsed
    assert by_key(rows, "ELRING^530.571").unit == "комп."
    gm = by_key(rows, "GM/Opel/Chevrolet^13285808")
    assert gm.unit == "штука"
    assert gm.min_pack == 1  # пустая МинУпаковка
    assert gm.brand_slug == "gm-opel-chevrolet"
    assert by_key(rows, "Liqui Moly^7516").min_pack == 4


def test_brand_comes_from_column_not_from_key(parsed):
    rows, _ = parsed
    row = by_key(rows, "Liqui Moly^7516")  # регистр бренда в ключе отличается от колонки
    assert row.brand == "LIQUI MOLY" and row.brand_slug == "liqui-moly"
    assert row.code == "7516" and row.category == assign_category(row.name)


def test_categories_come_from_assign_category(parsed):
    rows, _ = parsed
    for row in rows:
        assert row.category == assign_category(row.name)
        assert row.category in CATEGORY_BY_SLUG
    assert by_key(rows, "MANN-FILTER^W 712/75").category == "filters"
    assert by_key(rows, "Hyundai/Kia^26300-35503").category == "filters"


def test_long_values_are_cut_to_column_length():
    text = HEADER + "\n" + line("A" * 150, "1" * 90, "", "Фильтр " + "x" * 300, "10.00") + "\n"
    (row,) = iter_rows(text)
    assert len(row.brand) == 100 and len(row.code) == 60 and len(row.name) == 200
    assert len(row.item_key) == 80 and len(row.code_norm) <= 60
    assert len(row.brand_slug) <= 100


def test_header_without_key_column_is_rejected():
    with pytest.raises(PriceListError, match="КаталожныйНомер"):
        list(iter_rows('"a","b"\n"1","2"\n'))


def test_extract_csv_from_zip():
    name, text = extract_csv(zip_bytes(), "PriceAE_2026-10-27.zip")
    assert name == CSV_NAME
    assert text == CSV_TEXT


def test_extract_csv_from_zip_with_folder_and_no_csv():
    name, _ = extract_csv(zip_bytes("export/" + CSV_NAME), "p.zip")
    assert name == CSV_NAME
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "нет прайса")
    with pytest.raises(PriceListError, match="нет CSV"):
        extract_csv(buffer.getvalue(), "p.zip")


def test_extract_csv_plain_with_bom():
    name, text = extract_csv(csv_bytes(bom=True), "price.csv")
    assert name == "price.csv"
    assert text.startswith('"Производитель"')  # BOM отброшен
    assert len(list(iter_rows(text))) == ROWS_VALID


def test_extract_csv_rejects_non_utf8():
    with pytest.raises(PriceListError, match="UTF-8"):
        extract_csv("Производитель".encode("cp1251"), "price.csv")


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Hyundai/Kia", "hyundai-kia"),
        ("GM/Opel/Chevrolet", "gm-opel-chevrolet"),
        ("MANN-FILTER", "mann-filter"),
        ("LIQUI MOLY", "liqui-moly"),
        ("AE&T", "ae-t"),
        ("O.E.M.", "o-e-m"),
        ("  Дело Техники ", "delo-tekhniki"),
        ("Citroën", "citroen"),
        ("--Фильтр--", "filtr"),
    ],
)
def test_slugify(text, slug):
    assert slugify(text) == slug


@pytest.mark.parametrize("price", ["1e99999", "NaN", "Infinity", "0.001", "10000000000"])
def test_out_of_range_prices_are_skipped(price):
    text = HEADER + "\n" + line("BOSCH", "A1", "", "Фильтр", price)
    assert list(iter_rows(text)) == []


def test_multiple_csv_archive_is_ambiguous():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("spb.csv", csv_bytes())
        archive.writestr("moscow.csv", csv_bytes())
    with pytest.raises(PriceListError, match="несколько CSV"):
        extract_csv(buffer.getvalue(), "price.zip")
