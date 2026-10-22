"""Сопоставление GitHub-профилей с авторами OpenAlex по имени.

На GitHub имя может быть кириллицей ("Андрей Гетманов"), латиницей
в любом порядке ("Getmanov Andrey") или отсутствовать, поэтому
транслитерируем, сортируем токены и сравниваем нечётко. Совпадение имени -
только *кандидат* (`POSSIBLY_SAME_AS` с оценкой), а не факт.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Частые варианты латинизации одного и того же русского имени.
_VARIANTS = [("iy", "y"), ("ii", "i"), ("yi", "y"), ("kh", "h"), ("ks", "x"), ("j", "y"), ("ie", "e")]


def canonical(name: str | None) -> str:
    if not name:
        return ""
    name = name.lower()
    name = "".join(_TRANSLIT.get(ch, ch) for ch in name)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-z\s-]", " ", name).replace("-", " ")
    for a, b in _VARIANTS:
        name = name.replace(a, b)
    tokens = [t for t in name.split() if len(t) > 1]  # инициалы отбрасываем
    return " ".join(sorted(tokens))


def name_similarity(a: str | None, b: str | None) -> float:
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return 0.0
    if len(ca.split()) < 2 or len(cb.split()) < 2:
        return 0.0  # одно слово (часто ник), не сравниваем, слишком много ложных совпадений
    return fuzz.token_set_ratio(ca, cb) / 100
