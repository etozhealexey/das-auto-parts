"""normalize_code: артикулы сравниваются без разделителей и регистра."""

import pytest

from app.services.pricing.base import normalize_code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04465-33471", "0446533471"),
        ("0446533471", "0446533471"),
        ("04465 33471", "0446533471"),
        ("04465.33471", "0446533471"),
        ("04465/33471", "0446533471"),
        ("pf-1521", "PF1521"),
        ("  sp2080 ", "SP2080"),
        ("W 68/3", "W683"),
        ("P83117", "P83117"),
        ("", ""),
        ("---", ""),
        ("   ", ""),
        ("а-123", "А123"),  # кириллица — тоже буквы
    ],
)
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


def test_normalize_code_is_idempotent():
    assert normalize_code(normalize_code("04465-33471")) == "0446533471"


def test_equivalent_spellings_match():
    assert normalize_code("04465-33471") == normalize_code("0446533471") == normalize_code("04465 33471")
