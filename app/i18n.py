"""Переводы: русский — исходный язык, остальные — словари app/i18n/<lang>.json.

Ключ словаря — сама русская строка (как в gettext): код остаётся читаемым,
а недостающий перевод безопасно откатывается на русский.
Длинные авторские тексты портрета переводятся через Anthropic API один раз
и навсегда сохраняются в базе (таблица tr_cache).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from . import config, db

# Русский (исходный) + украинский + 5 самых распространённых языков мира
SUPPORTED = ("ru", "uk", "en", "zh", "hi", "es", "ar")
NAMES = {"ru": "Русский", "uk": "Українська", "en": "English", "zh": "中文",
         "hi": "हिन्दी", "es": "Español", "ar": "العربية"}
_DIR = Path(__file__).parent / "i18n"
_dicts: dict[str, dict[str, str]] = {}
log = logging.getLogger("joydao.i18n")


def norm(code: str | None) -> str:
    """Код языка Telegram (en-US, pt-br, zh-hans…) → поддерживаемый. Неизвестный → английский."""
    code = (code or "").lower().replace("_", "-").split("-")[0]
    if not code:
        return "ru"
    return code if code in SUPPORTED else "en"


def table(lang: str) -> dict[str, str]:
    if lang not in _dicts:
        f = _DIR / f"{lang}.json"
        _dicts[lang] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _dicts[lang]


def T(text: str, lang: str = "ru", **kw) -> str:
    """Перевести строку; именованные {плейсхолдеры} подставляются после перевода."""
    s = text if lang == "ru" else (table(lang).get(text) or text)
    return s.format(**kw) if kw else s


def user_lang(user, fallback_code: str | None = None) -> str:
    """Язык пользователя: сохранённый в профиле, иначе — язык его Telegram."""
    if user is not None and "lang" in user.keys() and user["lang"]:
        return user["lang"]
    return norm(fallback_code)


# --- Длинные тексты: перевод через ИИ с вечным кэшем ---

def _cached(h: str, lang: str) -> str | None:
    with db.connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS tr_cache (h TEXT, lang TEXT, text TEXT, PRIMARY KEY (h, lang))")
        row = c.execute("SELECT text FROM tr_cache WHERE h=? AND lang=?", (h, lang)).fetchone()
        return row["text"] if row else None


def _store(h: str, lang: str, text: str) -> None:
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO tr_cache (h, lang, text) VALUES (?,?,?)", (h, lang, text))


async def long_text(text: str, lang: str) -> str:
    """Перевод длинного текста. Без ключа API или при ошибке — оригинал на русском."""
    if lang == "ru" or not text.strip():
        return text
    h = hashlib.sha1(text.encode()).hexdigest()
    if (hit := _cached(h, lang)) is not None:
        return hit
    if not config.ANTHROPIC_API_KEY:
        return text
    from .agent import _ask
    system = (f"You are a professional literary translator. Translate the user's text from Russian "
              f"into {NAMES[lang]} ({lang}). Keep the meaning, warm tone, line breaks, lists and emoji. "
              f"Numbers stay as digits. Output only the translation, nothing else.")
    try:
        out = await _ask(system, text, max_tokens=4000)
    except Exception:
        log.exception("Перевод не удался (%s)", lang)
        return text
    _store(h, lang, out)
    return out


async def warm(langs: tuple[str, ...] = SUPPORTED) -> int:
    """Заранее перевести все тексты портрета (запуск: python -m app.i18n)."""
    import asyncio
    from . import suisai
    texts = [t for key in suisai.TEXTS for t in suisai.TEXTS[key]]
    texts = [suisai._clean_text(t) for t in texts]
    n = 0
    for lang in langs:
        if lang == "ru":
            continue
        for i in range(0, len(texts), 5):  # по 5 запросов параллельно — бережём лимиты API
            await asyncio.gather(*(long_text(t, lang) for t in texts[i:i + 5]))
            n += len(texts[i:i + 5])
        log.info("Готово: %s", lang)
    return n


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    print("Переведено текстов:", asyncio.run(warm()))
