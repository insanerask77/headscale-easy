"""Tiny i18n layer (gettext style, standard library only).

Strings are written in English in the code and wrapped in _(). Each other
language is a catalog in locales/<lang>.json that maps the English text to its
translation. A missing entry falls back to English, so the UI never breaks.

The language for a request is picked in this order: the user's choice (cookie
set from Settings), the browser's Accept-Language, and DEFAULT_LANG.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

LANGUAGES = {"en": "English", "es": "Español"}
DEFAULT_LANG = os.environ.get("DEFAULT_LANG", "en") if os.environ.get("DEFAULT_LANG") in LANGUAGES else "en"

# locales/<lang>.json plus locales/<lang>.d/*.json (one file per feature, so
# features developed in parallel do not edit the same file)
_catalogs: dict[str, dict[str, str]] = {}
for _lang in LANGUAGES:
    _dir = Path(__file__).parent / "locales"
    _catalogs[_lang] = {}
    for _path in [_dir / f"{_lang}.json", *sorted((_dir / f"{_lang}.d").glob("*.json"))]:
        if _path.is_file():
            _catalogs[_lang].update(json.loads(_path.read_text(encoding="utf-8")))

# One request per thread (ThreadingHTTPServer): the current language lives in
# thread-local storage and is set at the start of every request.
_state = threading.local()


def set_lang(lang: str) -> None:
    _state.lang = lang if lang in LANGUAGES else DEFAULT_LANG


def get_lang() -> str:
    return getattr(_state, "lang", DEFAULT_LANG)


def pick_lang(cookie_value: str | None, accept_language: str | None) -> str:
    if cookie_value in LANGUAGES:
        return cookie_value
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT_LANG


def _(text: str, **kwargs) -> str:
    """Translate text into the current language and fill in {placeholders}."""
    translated = _catalogs.get(get_lang(), {}).get(text, text)
    return translated.format(**kwargs) if kwargs else translated


def ngettext(singular: str, plural: str, n: int, **kwargs) -> str:
    """Singular or plural form; both are catalog keys. {n} is always available."""
    return _(singular if n == 1 else plural, n=n, **kwargs)
