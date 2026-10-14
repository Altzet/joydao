"""Алгоритм совместимости пары по Сюцай.

Итог 0–100 из четырёх компонентов (веса в data/compat_rules.json):
гармония Чисел Сознания, взаимодополнение матриц, резонанс миссий,
синхронность личных годов. Правила редактируются мастером без кода.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import suisai
from .i18n import T

with open(Path(__file__).parent / "data" / "compat_rules.json", encoding="utf-8") as f:
    RULES = json.load(f)

_FRIENDS = {int(k): set(v) for k, v in RULES["friends"].items()}
_ENEMIES = {int(k): set(v) for k, v in RULES["enemies"].items()}
_S = RULES["scores"]


def _relation_score(a: int, b: int) -> int:
    """Оценка отношений двух чисел по таблице дружбы планет (симметризованная)."""
    def one_way(x: int, y: int) -> int:
        if y in _FRIENDS.get(x, ()):
            return _S["friend"]
        if y in _ENEMIES.get(x, ()):
            return _S["enemy"]
        return _S["neutral"]
    return round((one_way(a, b) + one_way(b, a)) / 2)


def _complement_score(m1: dict[int, int], m2: dict[int, int]) -> int:
    """Насколько партнёры закрывают «недостающие компетенции» друг друга."""
    def covered(own: dict, other: dict) -> float:
        missing = [e for e, c in own.items() if c == 0]
        if not missing:
            return 1.0
        return sum(1 for e in missing if other[e] > 0) / len(missing)
    return round((covered(m1, m2) + covered(m2, m1)) / 2 * 100)


def _year_sync(y1: int, y2: int) -> int:
    ys = RULES["year_sync_scores"]
    if y1 == y2:
        return ys["same"]
    if min(abs(y1 - y2), 9 - abs(y1 - y2)) == 1:
        return ys["adjacent"]
    return ys["other"]


def _age(birth: date, today: date) -> float:
    return (today - birth).days / 365.25


def mission_phase(birth: date, today: date | None = None) -> float:
    """0 — живёт Числом Сознания (до 33), 1 — Миссия в полной силе; между — плавный переход."""
    a = RULES["age_shift"]
    t = (_age(birth, today or date.today()) - a["start"]) / (a["full"] - a["start"])
    return min(1.0, max(0.0, t))


def _age_weights(w: dict, phase: float) -> dict:
    """Перераспределяет общий вес «ЧС + миссия» по возрасту пары; остальные веса не трогает."""
    a = RULES["age_shift"]
    pool = w["chs_harmony"] + w["mission_resonance"]
    share = a["mission_share_young"] + (a["mission_share_mature"] - a["mission_share_young"]) * phase
    return {**w, "chs_harmony": pool * (1 - share), "mission_resonance": pool * share}


def compatibility(birth1: date, birth2: date, today: date | None = None, kind: str = "love",
                  lang: str = "ru") -> dict:
    """Полный расчёт совместимости пары. kind: love | business | friend — меняет веса
    компонентов (weights_by_kind в compat_rules.json; любовь — базовые weights)."""
    p1, p2 = suisai.portrait(birth1, today), suisai.portrait(birth2, today)
    w = RULES.get("weights_by_kind", {}).get(kind) or RULES["weights"]
    phase = (mission_phase(birth1, today) + mission_phase(birth2, today)) / 2
    w = _age_weights(w, phase)

    chs_score = _relation_score(p1["chs"], p2["chs"])
    compl_score = _complement_score(p1["matrix"], p2["matrix"])
    mission_score = _relation_score(p1["mission"], p2["mission"])
    year_score = _year_sync(p1["personal_year"], p2["personal_year"])

    total = round(
        chs_score * w["chs_harmony"] + compl_score * w["matrix_complement"]
        + mission_score * w["mission_resonance"] + year_score * w["year_sync"]
    )

    return {
        "score": total,
        "components": {
            "chs": chs_score, "complement": compl_score,
            "mission": mission_score, "year": year_score,
        },
        "mission_phase": round(phase, 2),
        "explanation": _explain(p1, p2, chs_score, compl_score, year_score, lang)
        + _explain_mission(p1, p2, mission_score, phase, lang),
    }


def _explain(p1: dict, p2: dict, chs_score: int, compl_score: int, year_score: int,
             lang: str = "ru") -> list[str]:
    """Человеческое объяснение «почему вы подходите» — тёплое, без приговоров."""
    planet = {k: T(v, lang) for k, v in RULES["planet_of"].items()}
    out = []

    a, b = p1["chs"], p2["chs"]
    if chs_score >= _S["friend"] - 5:
        out.append(T("Ваши Числа Сознания {a} ({pa}) и {b} ({pb}) — дружественные энергии: вам легко понимать мотивы друг друга.",
                     lang, a=a, b=b, pa=planet[str(a)], pb=planet[str(b)]))
    elif chs_score <= _S["enemy"] + 10:
        out.append(T("Числа Сознания {a} и {b} — контрастные энергии: такой союз учит принятию и даёт сильный рост, если оба готовы слышать друг друга.",
                     lang, a=a, b=b))
    else:
        out.append(T("Числа Сознания {a} и {b} нейтральны друг к другу: отношения будут такими, какими вы сами их построите.",
                     lang, a=a, b=b))

    gains1 = [e for e in p1["missing"] if p2["matrix"][e] > 0]
    if gains1 and compl_score >= 50:
        names = ", ".join(T(suisai.ENERGY_NAMES[e], lang) for e in gains1[:2])
        out.append(T("Партнёр силён там, где у вас пустые энергии ({names}) — рядом с ним вы нарабатываете недостающие компетенции.",
                     lang, names=names))

    if year_score >= 70:
        out.append(T("Ваши личные годы идут в такт — жизненные циклы сейчас синхронны, начинания будут общими.", lang))

    return out


def _explain_mission(p1: dict, p2: dict, mission_score: int, phase: float, lang: str = "ru") -> list[str]:
    """После 33 лет главное — куда стремится душа: говорим о миссиях, когда они уже ведут."""
    if phase < 0.5:
        return []
    a, b = p1["mission"], p2["mission"]
    if mission_score >= _S["friend"] - 5:
        return [T("Ваши Миссии {a} и {b} созвучны: души стремятся в одну сторону — а куда стремится душа, туда идёт и жизнь.", lang, a=a, b=b)]
    if mission_score <= _S["enemy"] + 10:
        return [T("Миссии {a} и {b} ведут в разные стороны: союз крепок, когда каждый уважает путь другого.", lang, a=a, b=b)]
    return [T("Миссии {a} и {b} нейтральны: общий путь вы выбираете сами.", lang, a=a, b=b)]
