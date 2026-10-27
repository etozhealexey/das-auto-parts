"""Пароли, инициалы и проверка регистрационной формы."""

from app.services.auth import (
    hash_password,
    initials_from_name,
    normalize_email,
    validate_registration,
    verify_password,
)


def test_password_roundtrip_and_salt():
    h1 = hash_password("correct horse")
    h2 = hash_password("correct horse")
    assert h1 != h2  # разная соль
    assert h1.startswith("scrypt$")
    assert verify_password("correct horse", h1)
    assert verify_password("correct horse", h2)
    assert not verify_password("wrong", h1)


def test_verify_password_rejects_garbage():
    assert not verify_password("x", None)
    assert not verify_password("x", "")
    assert not verify_password("x", "md5$abc")
    assert not verify_password("x", "scrypt$16384$zz$zz")


def test_initials():
    assert initials_from_name("ООО «ЮжныйСервис»") == "ЮС"
    assert initials_from_name("Иван Петров") == "ИП"
    assert initials_from_name("ИП Сидоров") == "СИ"
    assert initials_from_name("autoparts") == "AU"
    assert initials_from_name("ООО") == "КЛ"


def test_normalize_email():
    assert normalize_email("  Demo@Das-Auto-Parts.RU ") == "demo@das-auto-parts.ru"


def test_validate_registration_errors():
    errors = validate_registration(
        company="A", inn="123", contact_name="", email="bad", phone="12", password="short", password2="x"
    )
    assert set(errors) == {"company", "inn", "contact_name", "email", "phone", "password"}
    ok = validate_registration(
        company="ООО Тест", inn="7811000000", contact_name="Иван", email="a@b.ru",
        phone="+7 (900) 123-45-67", password="longenough", password2="longenough",
    )
    assert ok == {}
    mismatch = validate_registration(
        company="ООО Тест", inn="", contact_name="Иван", email="a@b.ru",
        phone="+79001234567", password="longenough", password2="different",
    )
    assert mismatch == {"password2": "Пароли не совпадают"}
