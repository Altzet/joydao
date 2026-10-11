"""FastAPI: API для Mini App + раздача статики.

Авторизация: каждый запрос несёт заголовок X-Init-Data (Telegram WebApp initData),
подпись проверяется HMAC-SHA256 по алгоритму Telegram.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import compat, config, db, joy, suisai
from .i18n import NAMES, SUPPORTED, T, long_text, table, user_lang

app = FastAPI(title="Suisai Dating API")
STATIC = Path(__file__).parent.parent / "static"

_bot = None  # устанавливается из main.py для нотификаций и фото


def set_bot(bot):
    global _bot
    _bot = bot


import logging

_log = logging.getLogger("joydao.api")


def _validate_init_data(init_data: str) -> int:
    """Проверка подписи initData (реализация aiogram); возвращает tg_id."""
    try:
        from aiogram.utils.web_app import safe_parse_webapp_init_data
        data = safe_parse_webapp_init_data(config.BOT_TOKEN, init_data)
        return data.user.id
    except Exception as e:
        _log.warning("401 initData: %s | начало строки: %.80s", e, init_data)
        raise HTTPException(401, "Invalid initData")


def _auth(x_init_data: str | None) -> int:
    if not x_init_data:
        _log.warning("401: пустой X-Init-Data (страница открыта не как Mini App)")
        raise HTTPException(401, "No initData")
    return _validate_init_data(x_init_data)


def _user_card(row, viewer_birth: date | None = None, kind: str = "love", lang: str = "ru") -> dict:
    birth = date.fromisoformat(row["birth_date"])
    card = {
        "id": row["tg_id"], "name": row["name"], "city": row["city"], "bio": row["bio"],
        "chs": suisai.consciousness_number(birth),
        "chs_short": T(suisai.CHS_SHORT[suisai.consciousness_number(birth)], lang),
        "has_photo": bool(row["photo_id"]), "goals": db.goals_of(row),
    }
    if viewer_birth:
        c = compat.compatibility(viewer_birth, birth, kind=kind, lang=lang)
        card["compat"] = c["score"]
        card["explanation"] = c["explanation"]
        card["components"] = c["components"]
    return card


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/me")
async def me(x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Заполните анкету в боте: /start")
    from . import mailing, teaser
    lang = user_lang(user)
    birth = date.fromisoformat(user["birth_date"])
    p = suisai.portrait(birth)
    today = date.today()
    return {"id": uid, "profile": _user_card(user, lang=lang), "portrait": p,
            "lang": lang, "languages": NAMES,
            "missing_names": [T(suisai.ENERGY_NAMES[e], lang) for e in p["missing"]],
            # экран «Сегодня»: личная энергия дня (тот же текст, что в рассылке) + баланс даты
            "today": {**mailing.day_parts(birth, today, lang), "date": today.strftime("%d.%m.%Y"),
                      "matrix": suisai.matrix(today)},
            "is_dating": db.is_dating(user), "bot": config.BOT_USERNAME,
            "community_chat": config.COMMUNITY_CHAT_URL,
            "settings": {"name": user["name"], "birth": birth.isoformat(), "gender": user["gender"],
                         "seek": user["seek"], "city": user["city"] or "", "bio": user["bio"] or "",
                         "daily": bool(user["daily_opt"]), "visible": bool(user["active"]),
                         "has_photo": bool(user["photo_id"]), "goals": db.goals_of(user)},
            "ref_link": teaser.ref_link(uid)}


class ProfileIn(BaseModel):
    """Частичное обновление профиля: приходят только изменённые поля."""
    name: str | None = Field(None, min_length=1, max_length=50)
    birth: str | None = None
    gender: str | None = Field(None, pattern="^(m|f)$")
    seek: str | None = Field(None, pattern="^(m|f|any)$")
    city: str | None = Field(None, max_length=60)
    bio: str | None = Field(None, max_length=500)
    daily: bool | None = None
    visible: bool | None = None
    goals: list[str] | None = None
    lang: str | None = None


@app.post("/api/profile")
async def save_profile(body: ProfileIn, x_init_data: str | None = Header(default=None)):
    from . import mailing
    from .bot import _is_adult
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    fields: dict = {}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "Пустое имя")
        fields["name"] = body.name.strip()
    if body.birth is not None:
        birth = _parse_birth(body.birth)
        if not _is_adult(birth):
            raise HTTPException(400, "Сервис 18+")
        fields["birth_date"] = birth.isoformat()
    for k in ("city", "bio"):
        if getattr(body, k) is not None:
            fields[k] = getattr(body, k).strip()
    dating = db.is_dating(user)
    for k in ("gender", "seek"):  # пол и «кого ищу» — только у анкеты знакомств
        if getattr(body, k) is not None:
            if not dating:
                raise HTTPException(409, "Нужна анкета знакомств")
            fields[k] = getattr(body, k)
    if body.lang is not None:
        if body.lang not in SUPPORTED:
            raise HTTPException(400, "Неизвестный язык")
        fields["lang"] = body.lang
    if body.goals is not None:
        goals = [g for g in db.GOALS if g in body.goals]  # порядок и только известные цели
        if not goals:
            raise HTTPException(400, "Выберите хотя бы одну цель")
        if not dating:
            raise HTTPException(409, "Нужна анкета в сообществе")
        if "love" in goals and not (fields.get("seek") or user["seek"]):
            raise HTTPException(400, "Для знакомств укажите, кого ищете")
        fields["goals"] = ",".join(goals)
    if body.visible is not None:
        if body.visible and not dating:
            raise HTTPException(409, "Нужна анкета знакомств")
        fields["active"] = int(body.visible)
    if fields:
        db.update_user(uid, **fields)
    if body.daily is not None:
        mailing.set_opt(uid, body.daily)
    return {"ok": True}


class GroupIn(BaseModel):
    people: list[dict] = Field(..., min_length=1, max_length=8)  # [{"name": "...", "date": "ДД.ММ.ГГГГ"}]
    kind: str = Field("love", pattern="^(love|business|friend)$")


@app.post("/api/group")
async def group_check(body: GroupIn, x_init_data: str | None = Header(default=None)):
    """Совместимость группы: я + до 8 человек (команда, семья, компания друзей)."""
    from . import teaser
    uid = _auth(x_init_data)
    me_row = db.get_user(uid)
    if not me_row:
        raise HTTPException(404, "Нет анкеты")
    lang = user_lang(me_row)
    people = [(T("Я", lang), date.fromisoformat(me_row["birth_date"]))]
    for p in body.people:
        name = str(p.get("name", "")).strip()[:20] or T("Человек {n}", lang, n=len(people))
        people.append((name, _parse_birth(str(p.get("date", "")))))
    return teaser.build_group(people, body.kind, lang)


@app.post("/api/profile/delete")
async def delete_profile(x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    db.delete_user(uid)
    return {"ok": True}


@app.get("/api/i18n/{lang}")
async def i18n_table(lang: str):
    """Словарь переводов для интерфейса приложения (ключ — русская строка)."""
    if lang not in SUPPORTED:
        raise HTTPException(404)
    return table(lang)


@app.get("/api/texts")
async def portrait_texts(x_init_data: str | None = Header(default=None)):
    """Глубокие тексты портрета на языке пользователя: ИИ-перевод один раз, дальше — из кэша."""
    import asyncio
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404)
    texts = suisai.portrait(date.fromisoformat(user["birth_date"]))["texts"]
    lang = user_lang(user)
    keys = list(texts)
    done = await asyncio.gather(*(long_text(texts[k], lang) for k in keys))
    return dict(zip(keys, done))


@app.get("/api/candidates")
async def candidates(goal: str = "love", x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    me_row = db.get_user(uid)
    if not me_row:
        raise HTTPException(404, "Нет анкеты")
    if not db.is_dating(me_row):
        raise HTTPException(409, "Нужна анкета знакомств")
    if goal not in db.goals_of(me_row):
        raise HTTPException(409, "Эта цель не выбрана в профиле")
    my_birth = date.fromisoformat(me_row["birth_date"])
    sparks, boosted = joy.sparked_me(uid), joy.boosted()
    # Прислал «Искру» — первым в ленте; анкета под бустом — выше остальных
    lift = lambda tg_id: (1000 if tg_id in sparks else 0) + (joy.BOOST_RANK if tg_id in boosted else 0)  # noqa: E731
    rows = db.daily_candidates(
        uid, config.FEED_LIMIT, goal=goal,
        rank=lambda r: compat.compatibility(my_birth, date.fromisoformat(r["birth_date"]), kind=goal)["score"]
        + lift(r["tg_id"]),
    )
    cards = [{**_user_card(r, my_birth, goal, user_lang(me_row)), "spark": r["tg_id"] in sparks} for r in rows]
    cards.sort(key=lambda c: -(c["compat"] + lift(c["id"])))
    from . import teaser
    return {"candidates": cards,
            "ref_link": teaser.ref_link(uid)}


@app.post("/api/like/{to_id}")
async def like(to_id: int, is_like: bool = True,
               x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    if uid == to_id or not db.get_user(to_id) or not db.was_shown(uid, to_id):
        raise HTTPException(400, "Некорректный кандидат")  # лайкать можно только показанных
    matched = db.set_like(uid, to_id, is_like)
    if is_like:
        await _after_like(uid, to_id, matched)
    return {"match": matched}


async def _after_like(uid: int, to_id: int, matched: bool) -> None:
    """События воронки + уведомление обоим о взаимности (общая часть лайка и «Искры»)."""
    db.log_event(uid, "like", to_id)
    if matched:
        db.log_event(uid, "match", to_id)
    if matched and _bot:
        from .bot import notify_match
        me_row, other = db.get_user(uid), db.get_user(to_id)
        c = compat.compatibility(date.fromisoformat(me_row["birth_date"]),
                                 date.fromisoformat(other["birth_date"]))
        await notify_match(_bot, uid, to_id, c["score"])


@app.post("/api/undo")
async def undo(x_init_data: str | None = Header(default=None)):
    """Отменить последнее действие в ленте (случайный лайк/пропуск)."""
    uid = _auth(x_init_data)
    me_row = db.get_user(uid)
    if not me_row:
        raise HTTPException(404, "Нет анкеты")
    restored = db.undo_last_like(uid)
    if not restored:
        return {"restored": False}
    other = db.get_user(restored)
    card = _user_card(other, date.fromisoformat(me_row["birth_date"])) if other else None
    return {"restored": True, "candidate": card}


@app.get("/api/matches")
async def matches(x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    me_row = db.get_user(uid)
    if not me_row:
        raise HTTPException(404, "Нет анкеты")
    my_birth = date.fromisoformat(me_row["birth_date"])
    # username отдаём только взаимным симпатиям — для кнопки «Написать»
    return {"matches": [{**_user_card(r, my_birth, lang=user_lang(me_row)), "username": r["username"] or "",
                         "report": joy.has_report(uid, r["tg_id"]),
                         "quiz": joy.pair_quiz_status(uid, r["tg_id"])}
                        for r in db.get_matches(uid)]}


# --- JOY energy (фаза 1: начисления; см. app/joy.py) ---

@app.get("/api/joy")
async def joy_state(x_init_data: str | None = Header(default=None)):
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    return joy.state(uid, user_lang(user))


class JoyTestIn(BaseModel):
    answers: list[int] = Field(..., min_length=len(joy.PRINCIPLES), max_length=len(joy.PRINCIPLES))


@app.post("/api/joy/test")
async def joy_test(body: JoyTestIn, x_init_data: str | None = Header(default=None)):
    """Тест на понимание принципов сообщества: сдан — человек становится участником клуба."""
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    passed, right = joy.pass_test(uid, body.answers)
    if passed:
        db.log_event(uid, "joy_member")
    lang = user_lang(user)
    return {"passed": passed, "right": right, "need": joy.PASS, "total": len(joy.PRINCIPLES),
            "review": joy.review(body.answers, lang), **joy.state(uid, lang)}


class JoyQuizIn(BaseModel):
    choice: int = Field(..., ge=0, le=2)


@app.post("/api/joy/quiz")
async def joy_quiz(body: JoyQuizIn, x_init_data: str | None = Header(default=None)):
    """Ответ на «Число дня»: один в день, только для участников клуба."""
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    if not joy.is_member(user):
        raise HTTPException(409, "Сначала тест принципов")
    res = joy.answer_quiz(uid, body.choice)
    if res is None:
        raise HTTPException(409, "Уже отвечал сегодня")
    db.log_event(uid, "joy_quiz", int(res["correct"]))
    return {**res, "reward": res["joy"] / joy.UNIT, **joy.state(uid, user_lang(user))}


def _joy_user(x_init_data: str | None):
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    return uid, user


@app.post("/api/joy/spark/{to_id}")
async def joy_spark(to_id: int, x_init_data: str | None = Header(default=None)):
    """«Искра» — лайк за 0,1 JOY: получатель сразу узнаёт, а ты первый в его ленте."""
    uid, user = _joy_user(x_init_data)
    if uid == to_id or not db.get_user(to_id) or not db.was_shown(uid, to_id):
        raise HTTPException(400, "Некорректный кандидат")
    try:
        fresh = joy.spend(uid, joy.SPARK, f"spend:spark:{to_id}")
    except joy.NotEnough:
        raise HTTPException(402, "Не хватает JOY")
    matched = db.set_like(uid, to_id, True)
    await _after_like(uid, to_id, matched)
    db.log_event(uid, "joy_spark", to_id)
    if fresh and not matched and _bot:
        from .bot import _app_kb
        other = db.get_user(to_id)
        lang = user_lang(other)
        try:
            await _bot.send_message(
                to_id, T("✨ Тебе прислали Искру! Кто-то из JoyDao очень хочет познакомиться — "
                         "он(а) уже первым(ой) в твоей ленте «Люди».", lang),
                reply_markup=_app_kb(T("👀 Посмотреть", lang), "feed", lang))
        except Exception:
            pass  # личка боту закрыта — увидит в ленте
    return {"match": matched, **joy.state(uid, user_lang(user))}


@app.post("/api/joy/boost")
async def joy_boost(x_init_data: str | None = Header(default=None)):
    """Буст анкеты на 24 часа за 0,3 JOY."""
    uid, user = _joy_user(x_init_data)
    if not db.is_dating(user) or not user["active"]:
        raise HTTPException(409, "Нужна видимая анкета")
    try:
        joy.boost(uid)
    except joy.NotEnough:
        raise HTTPException(402, "Не хватает JOY")
    db.log_event(uid, "joy_boost")
    return joy.state(uid, user_lang(user))


@app.post("/api/joy/report/{other_id}")
async def joy_report(other_id: int, x_init_data: str | None = Header(default=None)):
    """Глубокий разбор пары за 0,5 JOY (один раз на человека, дальше — бесплатно)."""
    uid, user = _joy_user(x_init_data)
    allowed = db.was_shown(uid, other_id) or any(r["tg_id"] == other_id for r in db.get_matches(uid))
    if uid == other_id or not db.get_user(other_id) or not allowed:
        raise HTTPException(400, "Некорректный человек")
    try:
        if joy.spend(uid, joy.REPORT, f"spend:report:{other_id}"):
            db.log_event(uid, "joy_report", other_id)
    except joy.NotEnough:
        raise HTTPException(402, "Не хватает JOY")
    lang = user_lang(user)
    return {**await joy.deep_report(uid, other_id, lang), "joy": joy.state(uid, lang)}


def _matched(uid: int, other_id: int) -> bool:
    return any(r["tg_id"] == other_id for r in db.get_matches(uid))


@app.get("/api/pairquiz/{other_id}")
async def pair_quiz(other_id: int, x_init_data: str | None = Header(default=None)):
    """Квиз пары: вопросы, статус и — когда ответили оба — сравнение."""
    uid, user = _joy_user(x_init_data)
    if not _matched(uid, other_id):
        raise HTTPException(403, "Только для взаимной симпатии")
    lang = user_lang(user)
    return {"name": db.get_user(other_id)["name"], "questions": joy.pair_questions(uid, other_id, lang),
            "status": joy.pair_quiz_status(uid, other_id), "reward": joy.PAIR_QUIZ / joy.UNIT,
            "result": joy.pair_quiz_result(uid, other_id, lang)}


class PairQuizIn(BaseModel):
    mine: list[int] = Field(..., min_length=joy.PAIR_QUIZ_LEN, max_length=joy.PAIR_QUIZ_LEN)
    guess: list[int] = Field(..., min_length=joy.PAIR_QUIZ_LEN, max_length=joy.PAIR_QUIZ_LEN)


@app.post("/api/pairquiz/{other_id}")
async def pair_quiz_answer(other_id: int, body: PairQuizIn, x_init_data: str | None = Header(default=None)):
    uid, user = _joy_user(x_init_data)
    if not _matched(uid, other_id):
        raise HTTPException(403, "Только для взаимной симпатии")
    res = joy.answer_pair_quiz(uid, other_id, body.mine, body.guess)
    if not res.get("again"):
        db.log_event(uid, "pair_quiz", other_id)
        await _notify_pair_quiz(uid, other_id, res)
    lang = user_lang(user)
    return {"status": joy.pair_quiz_status(uid, other_id), "result": joy.pair_quiz_result(uid, other_id, lang),
            "reward": joy.PAIR_QUIZ / joy.UNIT if uid in res["awarded"] else 0, "joy": joy.state(uid, lang)}


async def _notify_pair_quiz(uid: int, other_id: int, res: dict) -> None:
    """Партнёру: «твоя очередь» или «результат готов»."""
    if not _bot:
        return
    from .bot import _app_kb
    me, other = db.get_user(uid), db.get_user(other_id)
    lang = user_lang(other)
    if res["done"]:
        r = joy.pair_quiz_result(other_id, uid, lang)
        text = T("🎲 Квиз пары с {name} готов: вы совпали в {same} из {total}! Загляни — там повод для разговора.",
                 lang, name=me["name"], same=r["same"], total=r["total"])
        if other_id in res["awarded"]:
            text += "\n" + T("⚡ +{n} JOY", lang, n=joy.PAIR_QUIZ / joy.UNIT)
    else:
        text = T("🎲 {name} прошёл(ла) квиз пары и угадывал(а) твои ответы. Твоя очередь — интересно, совпадёте ли вы?",
                 lang, name=me["name"])
    try:
        await _bot.send_message(other_id, text, reply_markup=_app_kb(T("🎲 Открыть квиз", lang), "matches", lang))
    except Exception:
        pass


@app.get("/api/tour")
async def tour(x_init_data: str | None = Header(default=None)):
    uid, user = _joy_user(x_init_data)
    return {**joy.tour_state(uid, user_lang(user)), "member": joy.is_member(user)}


@app.post("/api/tour/join")
async def tour_join(x_init_data: str | None = Header(default=None)):
    """Вход в турнир недели за 0,5 JOY. Сразу после входа идёт отсчёт 10 минут."""
    uid, user = _joy_user(x_init_data)
    if not joy.is_member(user):
        raise HTTPException(409, "Сначала тест принципов")
    try:
        if joy.tour_join(uid):
            db.log_event(uid, "tour_join")
    except joy.NotEnough:
        raise HTTPException(402, "Не хватает JOY")
    except ValueError:
        raise HTTPException(409, "Турнир этой недели закрыт")
    lang = user_lang(user)
    return {**joy.tour_state(uid, lang), "joy": joy.state(uid, lang)}


class TourIn(BaseModel):
    answers: list[int] = Field(..., min_length=joy.TOUR_LEN, max_length=joy.TOUR_LEN)


@app.post("/api/tour/answer")
async def tour_answer(body: TourIn, x_init_data: str | None = Header(default=None)):
    uid, user = _joy_user(x_init_data)
    try:
        res = joy.tour_answer(uid, body.answers)
    except LookupError:
        raise HTTPException(409, "Сначала войди в турнир")
    return {**res, **joy.tour_state(uid, user_lang(user))}


@app.get("/api/joy/rating")
async def joy_rating(period: str = "week", x_init_data: str | None = Header(default=None)):
    """Рейтинг клуба по заработанному JOY: неделя или всё время."""
    uid, _ = _joy_user(x_init_data)
    return joy.rating(uid, "all" if period == "all" else "week")


@app.get("/api/thanks-wall")
async def wall():
    return {"thanks": [dict(r) for r in db.thanks_wall()]}


def _parse_birth(d: str) -> date:
    """Дата в ISO (1974-02-01) или русском формате (01.02.1974)."""
    d = d.strip()
    for parse in (date.fromisoformat,
                  lambda s: datetime.strptime(s, "%d.%m.%Y").date()):
        try:
            birth = parse(d)
            if date(1920, 1, 1) <= birth <= date.today():
                return birth
        except ValueError:
            continue
    raise HTTPException(400, "Некорректная дата")


@app.get("/api/check")
async def check_date(d: str, x_init_data: str | None = Header(default=None)):
    """Тизер-разбор произвольной даты (для вкладки «Проверка» в Mini App)."""
    from . import teaser
    uid = _auth(x_init_data)
    lang = user_lang(db.get_user(uid))
    birth = _parse_birth(d)
    t = teaser.build(birth, lang)
    t["ref_link"] = teaser.ref_link(uid)
    t["share_text"] = (
        T("🔍 Разбор даты {date}", lang, date=t["date"]) + "\n\n"
        + T("Число Сознания {chs} — {title}.", lang, chs=t["chs"], title=t["title"])
        + f" {t['essence'][:1].upper() + t['essence'][1:]}.\n\n⚡ {t['hook']}\n\n"
        + (T("💪 В избытке: {list}", lang, list=", ".join(t["strong"])) + "\n" if t["strong"] else "")
        + (T("🌱 Наработать: {list}", lang, list=", ".join(t["missing"])) + "\n\n" if t["missing"] else "\n")
        + T("Узнай баланс своей даты бесплатно:", lang)
    )
    return t


@app.get("/api/compat-check")
async def compat_check(d: str, kind: str = "love", x_init_data: str | None = Header(default=None)):
    """Совместимость пользователя с произвольной датой (вкладка «Проверка»)."""
    from . import teaser
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user:
        raise HTTPException(404, "Нет анкеты")
    other = _parse_birth(d)
    lang = user_lang(user)
    t = teaser.build_compat(date.fromisoformat(user["birth_date"]), other, kind, lang)
    t["ref_link"] = teaser.ref_link(uid)
    t["share_text"] = teaser.compat_share_text(t, user["name"], lang)
    return t


@app.get("/api/calc")
async def calc_text(text: str, x_init_data: str | None = Header(default=None)):
    """Калькулятор: имя (латиницей), город, номер карты/телефона/авто."""
    import re
    uid = _auth(x_init_data)
    raw = text.strip()[:64]
    if re.search(r"[а-яёА-ЯЁ]", raw):
        raise HTTPException(400, "Только латиница")
    n = suisai.name_number(raw)
    if n == 0:
        raise HTTPException(400, "Нет латинских букв или цифр")
    lang = user_lang(db.get_user(uid))
    return {"text": raw, "number": n, "meaning": T(suisai.TEXT_CALC_MEANINGS[n], lang),
            "scale": {k: T(v, lang) for k, v in suisai.TEXT_CALC_MEANINGS.items()}}


@app.get("/card.png")
async def card_png(kind: str = "day", num: int = 1, a: int = 1, b: int = 1, d: str = ""):
    """Публичная карточка поста (для превью в канале). Параметры безопасно ограничены."""
    from . import card as card_mod
    num, a, b = (max(1, min(9, x)) for x in (num, a, b))
    if kind == "chs":
        topic = {"kind": "chs", "num": num}
    elif kind == "pair":
        topic = {"kind": "pair", "a": a, "b": b}
    else:
        topic = {"kind": "day", "num": num, "date": d[:10]}
    return Response(content=card_mod.render(topic), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/photo")
async def upload_photo(request: Request, x_init_data: str | None = Header(default=None)):
    """Смена фото прямо из приложения: тело запроса — сам файл картинки.
    Пережимаем в JPEG (заодно отсекаем не-картинки и EXIF с геометкой) и шлём через бота
    в чат пользователя — так получаем Telegram file_id, как при загрузке в боте."""
    uid = _auth(x_init_data)
    user = db.get_user(uid)
    if not user or not _bot:
        raise HTTPException(404)
    raw = await request.body()
    if not raw or len(raw) > 15_000_000:
        raise HTTPException(413, "too big")
    import io
    from PIL import Image, ImageOps
    from aiogram.types import BufferedInputFile
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:
        raise HTTPException(400, "not an image")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=88)
    lang = user_lang(user)
    msg = await _bot.send_photo(uid, BufferedInputFile(buf.getvalue(), "photo.jpg"),
                                caption=T("Фото обновлено ✨", lang))
    file_id = msg.photo[-1].file_id
    db.update_user(uid, photo_id=file_id)
    if config.ADMIN_CHAT_ID:  # премодерация, как при смене фото в боте
        await _bot.send_photo(config.ADMIN_CHAT_ID, file_id, caption=f"Новое фото (app): {user['name']}, id={uid}")
    return {"ok": True}


@app.get("/api/photo/{user_id}")
async def photo(user_id: int, x_init_data: str | None = Header(default=None)):
    """Прокси фото из Telegram (не раскрывая токен бота)."""
    uid = _auth(x_init_data)
    if user_id != uid and not db.was_shown(uid, user_id):
        raise HTTPException(404)  # не даём перебирать фото всей базы по id
    user = db.get_user(user_id)
    if not user or not user["photo_id"] or not _bot:
        raise HTTPException(404)
    file = await _bot.get_file(user["photo_id"])
    buf = await _bot.download_file(file.file_path)
    return Response(content=buf.read(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})
