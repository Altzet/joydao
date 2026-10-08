"""Тизер-разбор любой даты рождения — виральная механика «проверь знакомого».

Текст намеренно интригует: даёт сильные стороны и один цепляющий факт,
но «тёмную сторону», матрицу и совместимость оставляет за кнопкой —
это повод перейти в бота и повод переслать разбор тому, чья это дата.
"""
from __future__ import annotations

from datetime import date

from . import config, suisai
from .i18n import T

# Цепляющий факт-крючок для каждого Числа Сознания: правда с интригой, без приговора
HOOKS = {
    1: "Такому человеку нельзя приказывать — он всё равно сделает по-своему. Но есть один способ стать для него незаменимым…",
    2: "Он считывает ваше настроение раньше, чем вы сами его поняли. Опасно? Только если не знать, чего он не прощает…",
    3: "Ему верят с первого слова — прирождённый наставник. Но есть тема, в которой он сам вечный ученик…",
    4: "Он видит выходы там, где остальные видят стены. Правда, есть цена, которую он платит за свою непохожесть…",
    5: "С ним легко всем — но по-настоящему близко он подпускает единицы. Есть ключ, который открывает эту дверь…",
    6: "Рядом с ним хочется становиться лучше — энергия Венеры. Но у этой любви к гармонии есть обратная сторона…",
    7: "Его интуиция пугающе точна — он знает о людях то, чего они не говорили. Откуда? Ответ в его матрице…",
    8: "Он добивается всего сам и не просит помощи. Узнайте, что происходит, когда такой человек наконец доверяет…",
    9: "В нём столько огня, что хватит на двоих. Вопрос лишь в том, греет этот огонь или обжигает — это решает одна вещь…",
}


def build(birth: date, lang: str = "ru") -> dict:
    """Данные тизера для бота и Mini App (все тексты — на языке lang)."""
    chs = suisai.consciousness_number(birth)
    missing = suisai.missing_energies(birth)
    strong, present, _ = suisai.energy_groups(birth)
    nm = {e: T(n, lang) for e, n in suisai.ENERGY_NAMES.items()}
    short = T(suisai.CHS_SHORT[chs], lang)
    title, _, essence = short.replace("：", ":").partition(":")  # в китайском — «：»
    return {
        "date": birth.strftime("%d.%m.%Y"),
        "chs": chs,
        "title": title.strip(),
        "essence": essence.strip(),
        "hook": T(HOOKS[chs], lang),
        "missing_count": len(missing),
        # Баланс даты: каждая цифра — энергия; повтор усиливает, отсутствие — зона роста
        "strong": [f"{nm[e]} ×{c}" for e, c in strong],
        "present": [nm[e] for e in present],
        "missing": [nm[e] for e in missing],
        "offer": [T(x, lang) for x in ("миссию — как этот человек действует в жизни",
                                       "личный год — что происходит с ним прямо сейчас",
                                       "триггеры — что выводит этого человека из себя",
                                       "совместимость с вами — с объяснением, почему")],
    }


def ref_link(user_id: int) -> str:
    """Парная ссылка: открывший вводит дату и сразу видит совместимость с отправителем
    (старые ref_-ссылки бот понимает так же)."""
    return f"https://t.me/{config.BOT_USERNAME}?start=pair_{user_id}"


KINDS = {"love": "в любви", "business": "в бизнесе", "friend": "в дружбе"}

# Формулировки плюсов/минусов и вердиктов для каждого вида отношений.
# Ключи: chs+/chs-/chs0 (числа сознания), comp+/comp- (матрицы), mis+/mis-, year+/year-.
TEXTS = {
    "love": {
        "chs+": "Дружественные энергии сознания — вам легко понимать мотивы друг друга",
        "chs-": "Контраст характеров: понадобится терпение и уважение к различиям",
        "chs0": "Нейтральные числа сознания — отношения будут такими, какими вы их построите",
        "comp+": "Вы закрываете «пустые» энергии друг друга — рядом с таким человеком растут",
        "comp-": "Матрицы похожи: меньше взаимодополнения — зато глубокое узнавание себя в другом",
        "mis+": "Миссии резонируют — похожий способ действовать в жизни",
        "mis-": "Разные способы действовать: один планирует, другой летит — договаривайтесь о темпе",
        "year+": "Личные циклы сейчас синхронны — начинания будут общими",
        "year-": "Циклы в разных фазах: у каждого сейчас свой этап — поддерживайте друг друга в разном",
        "verdict": ["Редкое созвучие — такие пары чувствуют друг друга без слов",
                    "Сильная пара с потенциалом — основа уже есть, остальное строится",
                    "Живой союз: не подарок судьбы, а совместная работа — и это честнее",
                    "Союз-учитель: много роста для обоих, если оба готовы слышать"],
    },
    "business": {
        "chs+": "Понимаете мотивы друг друга — меньше трений во внутренних решениях",
        "chs-": "Разные характеры: заранее разделите зоны решений, чтобы не спорить за руль",
        "chs0": "Нейтральные характеры — работа строится на чётких договорённостях",
        "comp+": "Сильные стороны дополняют друг друга — вместе закрываете больше задач бизнеса",
        "comp-": "Похожие сильные стороны: оба тянете одно — пробелы закрывайте наймом",
        "mis+": "Похожий способ действовать — легко договориться о стратегии",
        "mis-": "Разный темп: один планирует, другой рвётся в бой — пропишите, кто решает что",
        "year+": "Циклы синхронны — хорошее время начинать общий проект",
        "year-": "Разные жизненные фазы — сверяйте ожидания по срокам и вовлечённости",
        "verdict": ["Сильный деловой тандем — у вас всё для общего дела",
                    "Хорошие партнёры: распределите роли — и вперёд",
                    "Рабочий союз при чётких договорённостях и разделе зон",
                    "Сложное партнёрство: возможно, лучше подрядчик, чем совладелец"],
    },
    "friend": {
        "chs+": "Понимаете друг друга с полуслова — такая дружба надолго",
        "chs-": "Разные характеры: спорить будете, зато скучно не будет",
        "chs0": "Спокойная, ровная дружба — держится на общих интересах",
        "comp+": "Учите друг друга тому, чего не хватает, — растёте рядом",
        "comp-": "Очень похожи — уютно, но иногда не хватает свежего взгляда",
        "mis+": "Похоже смотрите на жизнь — легко строить общие планы",
        "mis-": "Разный темп жизни — договаривайтесь, кто зовёт на приключения",
        "year+": "Сейчас вы в одной фазе — самое время для общих затей",
        "year-": "У вас разные этапы — поддерживайте друг друга, даже если видитесь реже",
        "verdict": ["Друзья по коду души — такие находят друг друга раз в жизни",
                    "Крепкая дружба с запасом прочности",
                    "Хорошие приятели — дружба растёт от общих дел",
                    "Дружба-вызов: много споров и много роста"],
    },
}

# Чем энергия полезна в общем деле — для разбора ролей в бизнес-партнёрстве
ROLES = {1: "лидерство и решения", 2: "переговоры и люди", 3: "аналитика и финансы",
         4: "обучение и продукт", 5: "продажи и напор", 6: "маркетинг и дизайн",
         7: "стратегия и видение", 8: "операционка и результат", 9: "сервис и забота о клиентах"}


def build_compat(my_birth: date, other_birth: date, kind: str = "love", lang: str = "ru") -> dict:
    """Совместимость с человеком: балл, вердикт и честные плюсы/минусы
    для выбранного вида отношений (любовь, бизнес, дружба)."""
    from . import compat
    kind = kind if kind in KINDS else "love"
    c = compat.compatibility(my_birth, other_birth, kind=kind)
    k, X = c["components"], TEXTS[kind]
    pros, cons = [], []

    if k["chs"] >= 85:
        pros.append(X["chs+"])
    elif k["chs"] <= 45:
        cons.append(X["chs-"])
    else:
        pros.append(X["chs0"])
    if k["complement"] >= 70:
        pros.append(X["comp+"])
    elif k["complement"] <= 40:
        cons.append(X["comp-"])
    if k["mission"] >= 85:
        pros.append(X["mis+"])
    elif k["mission"] <= 45:
        cons.append(X["mis-"])
    (pros if k["year"] >= 70 else cons).append(X["year+" if k["year"] >= 70 else "year-"])

    score = c["score"]
    verdict = X["verdict"][0 if score >= 80 else 1 if score >= 65 else 2 if score >= 50 else 3]
    out = {"score": score, "verdict": T(verdict, lang), "pros": [T(x, lang) for x in pros],
           "cons": [T(x, lang) for x in cons], "kind": kind,
           "kind_label": T(KINDS[kind], lang), "date": other_birth.strftime("%d.%m.%Y")}
    if kind == "business":  # кто что приносит в общее дело и чего нет ни у кого
        m1, m2 = suisai.matrix(my_birth), suisai.matrix(other_birth)
        r = lambda cond: [T(ROLES[e], lang) for e in range(1, 10) if cond(e)]  # noqa: E731
        out["roles"] = {
            "you": r(lambda e: m1[e] and not m2[e]),
            "partner": r(lambda e: m2[e] and not m1[e]),
            "both": r(lambda e: m1[e] and m2[e]),
            "gap": r(lambda e: not m1[e] and not m2[e]),
        }
    return out


def build_group(people: list[tuple[str, date]], kind: str = "love", lang: str = "ru") -> dict:
    """Совместимость группы (команда, семья, компания): попарные баллы, лучшая и самая
    сложная пара, «связующее звено» (выше всех в среднем) и, для бизнеса, покрытие ролей."""
    from itertools import combinations
    from . import compat
    kind = kind if kind in KINDS else "love"
    n = len(people)
    m = [[None] * n for _ in range(n)]
    for i, j in combinations(range(n), 2):
        m[i][j] = m[j][i] = compat.compatibility(people[i][1], people[j][1], kind=kind)["score"]
    pairs = sorted(((m[i][j], i, j) for i, j in combinations(range(n), 2)), reverse=True)
    avg = [round(sum(x for x in row if x is not None) / (n - 1)) for row in m]
    glue = max(range(n), key=lambda i: avg[i])
    out = {
        "kind": kind, "kind_label": T(KINDS[kind], lang),
        "people": [{"name": nm, "chs": suisai.consciousness_number(b), "avg": avg[i]}
                   for i, (nm, b) in enumerate(people)],
        "matrix": m,
        "best": {"a": pairs[0][1], "b": pairs[0][2], "score": pairs[0][0]},
        "hard": {"a": pairs[-1][1], "b": pairs[-1][2], "score": pairs[-1][0]},
        "glue": glue,
        "team": round(sum(p[0] for p in pairs) / len(pairs)),
    }
    if kind == "business":  # какие роли закрывает команда целиком, а каких нет ни у кого
        have = {e for _, b in people for e, c in suisai.matrix(b).items() if c}
        out["covered"] = [T(ROLES[e], lang) for e in range(1, 10) if e in have]
        out["gap"] = [T(ROLES[e], lang) for e in range(1, 10) if e not in have]
    return out


def render_compat_html(t: dict, with_name: str | None = None, lang: str = "ru") -> str:
    """with_name — для парной ссылки: показываем имя, а не чужую дату рождения."""
    import html
    pros = "\n".join(f"➕ {x}" for x in t["pros"])
    cons = "\n".join(f"➖ {x}" for x in t["cons"]) or "➖ " + T("Явных зон напряжения не видно — берегите то, что есть", lang)
    who = html.escape(with_name) if with_name else T("человеком {date}", lang, date=t["date"])
    return (
        T("💞 <b>Совместимость с {who}</b>", lang, who=who) + "\n\n"
        + T("<b>Ваше созвучие: {score}%</b>", lang, score=t["score"]) + f"\n<i>{t['verdict']}</i>\n\n"
        + T("<b>Что будет получаться легко</b>", lang) + f"\n{pros}\n\n"
        + T("<b>Над чем предстоит работать</b>", lang) + f"\n{cons}"
        + ("" if with_name else "\n\n" + T(
            "↗️ <i>Перешлите этот разбор самому кандидату — пусть проверит вашу пару "
            "со своей стороны. Кнопка ниже сработает и у него.</i>", lang))
    )


def compat_share_text(t: dict, my_name: str, lang: str = "ru") -> str:
    """Короткий текст для нативного шеринга (без HTML)."""
    top_pro = t["pros"][0] if t["pros"] else ""
    return (T("💞 Проверил(а) нашу совместимость {kind} по коду души: {score}%", lang,
              kind=t.get("kind_label", ""), score=t["score"])
            + f"\n«{t['verdict']}»\n➕ {top_pro}\n\n"
            + T("Есть и то, над чем работать — посмотри полный разбор и проверь сам(а):", lang))
