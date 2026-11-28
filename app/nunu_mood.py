"""Опыт «Как ты сейчас?» (19.11.2026): стоит ли боту раз в день спрашивать о состоянии.

Гипотеза: вопрос в чате раз в день → больше дней с отметкой, и никто не отключает.
Опоры: назвать чувство — значит немного его успокоить (affect labeling, Lieberman et al. 2007);
ежедневная отметка в телефоне повышает понимание своих эмоций (Kauer et al. 2012);
на напоминания отвечают ~79% раз, почти независимо от их частоты (Wrzus & Neubauer 2023).
⚠ Против: лишние уведомления раздражают (данные приложений, не статьи) — поэтому проверяем.

Дизайн для малой выборки — ABAB, опыт «сам на себе» с чередованием (single-case, Kazdin 2011):
неделя A — без вопроса, B — бот спрашивает в 19:00 по Киеву. Правило решения записано ДО опыта:
  оставить — если в обе недели B дней с отметкой больше, чем в соседних A, хотя бы на 2, и никто не нажал «Не надо»;
  убрать — если разницы нет или вопрос отключали; неоднозначно — ещё пара недель A/B.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Header, Request

from . import nunu, nunu_admin

log = logging.getLogger("nunu.mood")
START = date(2026, 11, 23)  # понедельник: первая неделя A
PHASES = "ABAB"
HOUR_UTC = 17  # 19:00 в Киеве зимой — вечером оглянуться на день
KYIV = timezone(timedelta(hours=2))  # ponytail: зимнее время; опыт кончается до перехода на летнее
MOOD = ["Бодро и светло", "Спокойно", "Напряжённо", "Грустно, без сил"]
MIN_DIFF = 2  # на сколько дней в неделю B должно быть больше отметок, чем в соседних A

SCHEMA = """
CREATE TABLE IF NOT EXISTS mood_ask (tg_id INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY (tg_id, day));
CREATE TABLE IF NOT EXISTS mood_no (tg_id INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY (tg_id, day));
"""
admin = APIRouter(prefix="/nunu/admin/api/moodexp")


def _db():
    c = nunu._db()
    c.executescript(SCHEMA)
    return c


def phase(d: date) -> str | None:
    """Неделя опыта для дня: 'A', 'B' или None (до начала и после конца)."""
    k = (d - START).days // 7
    return PHASES[k] if 0 <= k < len(PHASES) else None


def kyiv_day(t: str) -> date | None:
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(KYIV).date()
    except (TypeError, ValueError):
        return None


def _marked_days(d: dict) -> set[date]:
    return {k for m in d.get("nunu-mood") or [] if isinstance(m, dict) and (k := kyiv_day(m.get("t"))) is not None}


def due(today: date) -> list[int]:
    """Кого спросить сегодня: неделя B, вопрос не отключён, сегодня ещё не отмечался и не спрашивали."""
    if phase(today) != "B":
        return []
    out = []
    with closing(_db()) as c, c:
        for r in c.execute("SELECT tg_id, data FROM state").fetchall():
            d = json.loads(r["data"] or "{}")
            if (d.get("nunu-prefs") or {}).get("moodAsk") is False or today in _marked_days(d):
                continue
            if c.execute("INSERT OR IGNORE INTO mood_ask(tg_id, day) VALUES(?, ?)", (r["tg_id"], str(today))).rowcount:
                out.append(r["tg_id"])
    return out


def _update(uid: int, fn) -> None:
    """Правка состояния игрока сервером: версия растёт, страница подтянет при следующем опросе (раз в 15 с)."""
    with closing(nunu._db()) as c, c:
        r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
        d = json.loads(r["data"]) if r else {}
        fn(d)
        c.execute("INSERT INTO state(tg_id, data, rev) VALUES(?, ?, 1) ON CONFLICT(tg_id) DO UPDATE "
                  "SET data=excluded.data, rev=state.rev+1, updated_at=datetime('now')", (uid, json.dumps(d, ensure_ascii=False)))


def mark(uid: int, q: int, now: datetime | None = None) -> None:
    """Отметка из кнопки в чате — то же, что в игре (источник «bot»)."""
    now = now or datetime.now(timezone.utc)

    def add(d):
        keep = [m for m in d.get("nunu-mood") or [] if isinstance(m, dict) and (k := kyiv_day(m.get("t")))
                and k >= now.astimezone(KYIV).date() - timedelta(days=14)]
        d["nunu-mood"] = (keep + [{"id": f"m{int(now.timestamp() * 1000)}", "t": now.isoformat(), "q": q, "src": "bot"}])[-60:]
    _update(uid, add)


def stop(uid: int, today: date) -> None:
    """«Не надо»: больше не спрашивать (вернуть можно в настройках) — и это сигнал опыта."""
    _update(uid, lambda d: d.setdefault("nunu-prefs", {}).__setitem__("moodAsk", False))
    with closing(_db()) as c, c:
        c.execute("INSERT OR IGNORE INTO mood_no(tg_id, day) VALUES(?, ?)", (uid, str(today)))


def answer(data: str, uid: int) -> str:
    """Кнопка в чате: mood:0…3 или mood:no."""
    if data == "mood:no":
        stop(uid, datetime.now(KYIV).date())
        return "Хорошо, больше не спрошу. Вернуть можно в игре: Настройки → Цвет вокруг острова."
    q = int(data.split(":")[1])
    mark(uid, q)
    return f"Отмечено: {MOOD[q].lower()} 🌊 Вода вокруг твоего острова уже меняет цвет."


async def send(bot, uid: int) -> None:
    from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
    t_ = nunu.user_tr(uid)
    b = lambda t, d: InlineKeyboardButton(text=t_(t), callback_data=d)  # noqa: E731
    pic = nunu.PICS / "mood.png"  # картинка «Как ты сейчас?» (холст «Картинки бота»)
    try:
        await bot.send_photo(uid, FSInputFile(pic), caption=t_("🌊 Как ты себя чувствуешь?\nОдно касание — и вода вокруг твоего острова станет цвета твоего состояния."),
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [b("🟡 Бодро и светло", "mood:0"), b("🟢 Спокойно", "mood:1")],
                                   [b("🟠 Напряжённо", "mood:2"), b("🟣 Грустно, без сил", "mood:3")],
                                   [b("Не надо спрашивать", "mood:no")]]))
    except Exception as e:
        log.info("Не спросил %s: %s", uid, e)


async def loop(bot) -> None:
    """Раз в сутки в HOUR_UTC, только в недели B. Повтор после перезапуска не шлёт: отметка в mood_ask."""
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=HOUR_UTC, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            for uid in await asyncio.to_thread(due, datetime.now(KYIV).date()):
                await send(bot, uid)
                await asyncio.sleep(0.05)
        except Exception as e:
            log.warning("Как ты сейчас: %s", e)


# ---------- админка: цифры по неделям и вывод по заранее записанному правилу ----------
def weeks() -> list[dict]:
    return [{"n": k + 1, "phase": p, "from": str(START + timedelta(days=7 * k)), "to": str(START + timedelta(days=7 * k + 6))}
            for k, p in enumerate(PHASES)]


def verdict(days: list[int], no: int, today: date) -> str:
    """Правило решения, записанное до опыта (см. начало файла)."""
    if today <= START + timedelta(days=7 * len(PHASES) - 1):
        return "идёт опыт"
    a1, b1, a2, b2 = days
    if no:
        return "убрать: вопрос отключали"
    if b1 >= max(a1, a2) + MIN_DIFF and b2 >= a2 + MIN_DIFF:
        return "оставить: в обе недели B отметок больше"
    if b1 <= max(a1, a2) and b2 <= a2:
        return "убрать: разницы нет"
    return "неоднозначно: ещё пара недель A/B"


def report(today: date | None = None) -> dict:
    today = today or datetime.now(KYIV).date()
    W = weeks()
    with closing(_db()) as c:
        asks = {(r["tg_id"], r["day"]) for r in c.execute("SELECT tg_id, day FROM mood_ask")}
        nos = {(r["tg_id"], r["day"]) for r in c.execute("SELECT tg_id, day FROM mood_no")}
        names = {r["tg_id"]: r["name"] for r in c.execute("SELECT tg_id, name FROM users")}
        rows = c.execute("SELECT tg_id, data FROM state").fetchall()
    people = []
    for r in rows:
        d = json.loads(r["data"] or "{}")
        marked = _marked_days(d)
        bloom = [kd for s in d.get("nunu-seeds") or [] if isinstance(s, dict) and isinstance(s.get("life"), dict)
                 and s["life"].get("same") and (kd := _date(s["life"].get("d")))]
        ws = []
        for w in W:
            lo, hi = date.fromisoformat(w["from"]), date.fromisoformat(w["to"])
            inw = lambda x: lo <= x <= hi  # noqa: E731
            ws.append({"marked": sum(1 for x in marked if inw(x)),
                       "asked": sum(1 for u, x in asks if u == r["tg_id"] and inw(date.fromisoformat(x))),
                       "no": sum(1 for u, x in nos if u == r["tg_id"] and inw(date.fromisoformat(x))),
                       "bloom": sum(1 for x in bloom if inw(x))})
        if any(w["marked"] or w["asked"] or w["no"] for w in ws):
            no = sum(w["no"] for w in ws)
            people.append({"uid": r["tg_id"], "name": names.get(r["tg_id"], str(r["tg_id"])), "weeks": ws,
                           "verdict": verdict([w["marked"] for w in ws], no, today)})
    n = len(people) or 1
    avg = [round(sum(p["weeks"][k]["marked"] for p in people) / n, 1) for k in range(len(W))]
    no = sum(w["no"] for p in people for w in p["weeks"])
    return {"weeks": W, "today": str(today), "now": phase(today), "people": people, "avg": avg,
            "verdict": verdict(avg, no, today) if people else "пока нет данных",
            "rule": f"Оставить, если в обе недели B дней с отметкой больше, чем в соседних A, хотя бы на {MIN_DIFF}, "
                    "и никто не нажал «Не надо». Убрать, если разницы нет или вопрос отключали. Неоднозначно — ещё пара недель."}


def _date(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


@admin.get("")
def admin_report(req: Request, x_admin_key: str | None = Header(default=None)):
    nunu_admin._auth(req, x_admin_key)
    return report()
