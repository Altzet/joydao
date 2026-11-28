"""Перевод сообщений бота на язык игрока теми же словарями, что и сайт (static/i18n/<lang>.json).
Упрощённый двойник static/i18n.js: фраза целиком → со строчной → шаблон с {} → края без букв → части через «. », « · », «: »…
Язык игрока игра пишет в nunu-prefs.lang (static/nunu.html, boot). Чего нет в словаре — остаётся по-русски."""
import json
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "static" / "i18n"
CYR = re.compile("[А-Яа-яЁё]")
L = r"[^\W\d_]"  # буква


@lru_cache(maxsize=None)
def _load(lang: str):
    # ponytail: словарь читается раз за жизнь процесса — новые строки после перезапуска сервиса
    d = json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))
    pats = []
    for k, v in d.items():
        if "{}" not in k:
            continue
        parts = k.split("{}")
        rx = "(.*?)".join((f"(?<!{L})" if i and re.match(L, x) else "") + re.escape(x)
                          + (f"(?!{L})" if i < len(parts) - 1 and re.search(L + "$", x) else "") for i, x in enumerate(parts))
        pats.append((len(k) - 2 * (len(parts) - 1), re.compile(f"^{rx}$", re.S), v))
    pats.sort(key=lambda p: -p[0])
    return d, pats


def _one(k: str, d, pats, depth=0):
    if (v := d.get(k)) is not None:
        return v
    u = k[:1].upper() + k[1:]
    if u != k and (v := d.get(u)) is not None:
        return v[:1].lower() + v[1:]
    if depth > 8:
        return None
    for _, rx, to in pats:
        if m := rx.match(k):
            gs = iter(m.groups())

            def sub(_):
                g = next(gs).strip()
                t = _one(g, d, pats, depth + 1) if CYR.search(g) and len(g) < len(k) else None
                return t if t is not None else g
            return re.sub(r"\{\}", sub, to)
    if (n := re.match(rf"^([^\w«\"]+)({L}.*)$", k)) and (t := _one(n[2].strip(), d, pats, depth + 1)) is not None:
        return n[1] + t
    if (n := re.match(r"^(.*?[^\W\d_»\"])([^\w»\"]+)$", k)) and (t := _one(n[1].strip(), d, pats, depth + 1)) is not None:
        return t + n[2]
    b = re.split(r"((?<=[.!?…]) )", k)  # сначала по предложениям, потом внутри
    if len(b) < 3:
        b = re.split(r"(: | · | — |, )", k)
    if len(b) < 3:
        return None
    for i in range(0, len(b), 2):
        if b[i] and CYR.search(b[i]):
            if (v := _one(b[i], d, pats, depth + 1)) is None:
                return None
            b[i] = v
    return "".join(b)


def tr(text: str, lang: str) -> str:
    """Текст бота на языке lang (ru — как есть). По строкам; HTML-теги не трогаем."""
    if lang not in ("uk", "en") or not text:
        return text
    d, pats = _load(lang)

    def seg(s):
        k = re.sub(r"\s+", " ", s).strip()
        if not CYR.search(k):
            return s
        t = _one(k, d, pats)
        return s if t is None else s[:len(s) - len(s.lstrip())] + t + s[len(s.rstrip()):]
    return "\n".join("".join(seg(p) if i % 2 == 0 else p for i, p in enumerate(re.split(r"(<[^<>]*>)", line)))
                     for line in text.split("\n"))


def lang_of(data: dict | None) -> str:
    """Язык игрока из его состояния игры; не открывал игру после PR #9 — русский."""
    g = ((data or {}).get("nunu-prefs") or {}).get("lang")
    return g if g in ("ru", "uk", "en") else "ru"
