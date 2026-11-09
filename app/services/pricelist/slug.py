"""Slug для брендов из прайса: транслитерация кириллицы, только [a-z0-9-].

«Hyundai/Kia» → «hyundai-kia», «MANN-FILTER» → «mann-filter», «Дело Техники» → «delo-tekhniki».
"""

import re
import unicodedata

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Нижний регистр, кириллица → латиница, всё остальное → дефис, дефисы схлопнуты."""
    lowered = unicodedata.normalize("NFKD", text.strip().lower())
    # диакритика (ü, é) отбрасывается как combining-символы после NFKD: «Citroën» → «citroen»
    translit = "".join(_CYRILLIC.get(ch, ch) for ch in lowered if not unicodedata.combining(ch))
    return _NOT_ALNUM.sub("-", translit).strip("-")
