"""Звёзды: разбор даты знаменитости и совместимость с ней — за JOY.

Данные — app/data/celebs.json, собирается из Википедии скриптом tools/celebs_wiki.py.
Нет файла — раздел просто пустой.
"""
from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path

from . import teaser
from .i18n import T

FILE = Path(__file__).parent / "data" / "celebs.json"
CATS = {"biz": "💼 Бизнес", "music": "🎤 Музыка", "film": "🎬 Кино", "sport": "🏆 Спорт", "mind": "🧠 Гении"}


@cache  # ponytail: файл меняется только деплоем — перечитается при рестарте сервиса
def _all() -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(FILE.read_text(encoding="utf-8"))} if FILE.exists() else {}


def get(cid: str) -> dict | None:
    return _all().get(cid)


def _name(c: dict, lang: str) -> str:
    return c["name"].get(lang) or c["name"]["en"]


def listing(opened: set[str], lang: str) -> dict:
    """Список для витрины: имя и категория — бесплатно, всё остальное — после открытия."""
    return {"cats": {k: T(v, lang) for k, v in CATS.items()},
            "celebs": [{"id": c["id"], "cat": c["cat"], "name": _name(c, lang), "open": c["id"] in opened,
                         "photo": (c.get("photo") or {}).get("url")}
                       for c in _all().values()]}


def profile(c: dict, my_birth: date, lang: str) -> dict:
    """Разбор звезды + совместимость в трёх сферах. Дата публичная (Википедия) — её показываем."""
    birth = date.fromisoformat(c["birth"])
    kinds = [teaser.build_compat(my_birth, birth, k, lang) for k in ("love", "friend", "business")]
    name = _name(c, lang)
    share = (T("⭐ Я и {name}:", lang, name=name) + " "
             + " · ".join(f"{e} {k['score']}%" for e, k in zip(("💞", "🤝", "💼"), kinds))
             + "\n" + T("А с какой звездой созвучен ты? Проверь в JoyDao 👇", lang))
    return {"id": c["id"], "name": name, "cat": T(CATS[c["cat"]], lang),
            "wiki": c["wiki"].get(lang) or c["wiki"].get("en"),
            "photo": c.get("photo"),  # {url, author, license, page} — подпись обязательна по лицензии
            "card": teaser.build(birth, lang), "kinds": kinds, "share_text": share}
