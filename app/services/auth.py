"""Пароли и проверка регистрационных данных. Хэш — scrypt из стандартной библиотеки."""

import hashlib
import hmac
import os
import re

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LEGAL_FORMS = {"ООО", "ИП", "АО", "ЗАО", "ПАО", "ОАО", "НКО", "LLC", "LTD"}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${salt.hex()}${digest.hex()}"


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        algo, n, salt_hex, digest_hex = hashed.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=SCRYPT_R, p=SCRYPT_P, dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def initials_from_name(name: str) -> str:
    """«ООО «ЮжныйСервис»» → ЮС, «Иван Петров» → ИП, иначе первые две буквы."""
    words = [w.strip("«»\"'()") for w in name.replace("«", " ").replace("»", " ").split()]
    words = [w for w in words if w and w.upper() not in _LEGAL_FORMS]
    if not words:
        return "КЛ"
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    word = words[0]
    caps = [ch for ch in word if ch.isupper()]
    if len(caps) >= 2:
        return (caps[0] + caps[1]).upper()
    return word[:2].upper()


def validate_registration(
    *, company: str, inn: str, contact_name: str, email: str, phone: str, password: str, password2: str
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(company.strip()) < 2:
        errors["company"] = "Укажите название компании"
    inn_digits = inn.strip()
    if inn_digits and not (inn_digits.isdigit() and len(inn_digits) in (10, 12)):
        errors["inn"] = "ИНН состоит из 10 или 12 цифр"
    if len(contact_name.strip()) < 2:
        errors["contact_name"] = "Укажите контактное лицо"
    if not _EMAIL_RE.match(email.strip()):
        errors["email"] = "Некорректный e-mail"
    if len(re.sub(r"\D", "", phone)) < 10:
        errors["phone"] = "Укажите телефон"
    if len(password) < 8:
        errors["password"] = "Пароль не короче 8 символов"
    elif password != password2:
        errors["password2"] = "Пароли не совпадают"
    return errors
