"""Ситуации участников: игрок, прошедший все ситуации, предлагает свою → проверка ИИ → пост и опрос в канале →
бот сам считает голоса и по правилу из админки добавляет ситуацию в игру. Владелец может только снять пост.

Честность: правило (часы, минимум голосов, % «за») пишется прямо в посте и фиксируется в записи на момент
публикации — смена настроек не трогает идущие голосования. Опрос Telegram анонимный, один аккаунт — один голос.
Голоса приходят обновлениями poll (бот видит свои опросы), в конце опрос закрывается и итог публикуется ответом.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import config, nunu, nunu_admin

log = logging.getLogger("nunu.subs")
router = APIRouter(prefix="/nunu/api/subs")
SCHEMA = """CREATE TABLE IF NOT EXISTS subs (id TEXT PRIMARY KEY, uid INTEGER NOT NULL, name TEXT NOT NULL, sign TEXT NOT NULL,
    data TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', rule TEXT, post INTEGER, poll INTEGER,
    poll_id TEXT, yes INTEGER NOT NULL DEFAULT 0, no INTEGER NOT NULL DEFAULT 0, ends TEXT, extended INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0, idx INTEGER, created_at TEXT DEFAULT (datetime('now')))"""
DEF = {"on": True, "hours": 72, "min": 10, "pct": 60, "extend": True}
OPEN = ("check", "wait", "voting")  # ещё не решено
ZN = (("🌸", "Цветы — иду к людям"), ("🌳", "Деревья — беру паузу"), ("🪨", "Скалы — стою за своё"))
AI_DAY = 5  # попыток проверки в сутки: правка после отказа ИИ не съедает недельное право


def _c():
    c = nunu_admin._c()
    c.execute(SCHEMA)
    return c


def cfg_get() -> dict:
    with closing(_c()) as c:
        r = c.execute("SELECT v FROM admin_kv WHERE k='subs'").fetchone()
    return DEF | (json.loads(r["v"]) if r else {})


def cfg_set(v: dict) -> dict:
    v = {"on": bool(v.get("on", True)), "hours": max(1, min(24 * 14, int(v.get("hours", 72)))),
         "min": max(1, min(10000, int(v.get("min", 10)))), "pct": max(1, min(100, int(v.get("pct", 60)))),
         "extend": bool(v.get("extend", True))}
    with closing(_c()) as c, c:
        c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES('subs', ?)", (json.dumps(v),))
    return v


def rule_text(r: dict) -> str:
    return (f"Голосование {r['hours']} ч. Ситуация войдёт в игру, если проголосуют не меньше {r['min']} "
            f"{_pl(r['min'], 'человека', 'человек', 'человек')} и «за» будет не меньше {r['pct']}%."
            + (" Если голосов не хватит, голосование продлится один раз." if r.get("extend") else ""))


def _pl(n, a, b, c):
    return a if n % 10 == 1 and n % 100 != 11 else b if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else c


# ---------- что прошёл игрок ----------
def pool() -> list[str]:
    """Все ситуации, которые сейчас попадают в новые партии (колода последней версии)."""
    with closing(nunu_admin._c()) as c:
        decks = nunu_admin._decks(c)
    d = decks[max(decks, key=int)] if decks else nunu_admin._deck1(nunu_admin.base())
    return sorted({f"s{k}" for k in d["P2"]} | {k for k in d["P3"] if k.startswith("s")})


def eligible(uid: int) -> tuple[bool, int]:
    """Прошёл все ситуации: ответы на них лежат в его состоянии (nunu-seen)."""
    with closing(nunu._db()) as c:
        r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
    seen = set((json.loads(r["data"]).get("nunu-seen") or []) if r else [])
    left = [k for k in pool() if k not in seen]
    return not left, len(left)


# ---------- приём от игрока ----------
class SubIn(BaseModel):
    scene: list
    anon: bool = False


def _scene_ok(x) -> bool:
    s = nunu_admin._s
    return (isinstance(x, list) and len(x) == 5 and s(x[0], 40) and s(x[1], 400)
            and all(isinstance(p, list) and len(p) == 2 and all(s(o, 120) for o in p) for p in x[2:]))


@router.get("/me")
def me(x_init_data: str | None = Header(default=None)):
    u = nunu._user(x_init_data)
    ok, left = eligible(u.id)
    cf = cfg_get()
    with closing(_c()) as c:
        rows = c.execute("SELECT * FROM subs WHERE uid=? ORDER BY created_at DESC, rowid DESC LIMIT 20", (u.id,)).fetchall()
        week = _week_used(c, u.id)
    nxt = None
    if week:
        nxt = (datetime.fromisoformat(week) + timedelta(days=7)).date().isoformat()
    return {"eligible": ok, "left": left, "on": cf["on"], "rule": rule_text(cf), "next": nxt,
            "mine": [_pub(r) for r in rows], "name": u.first_name[:30]}


def _week_used(c, uid) -> str | None:
    """Недельное право тратит только ситуация, прошедшая проверку (ушла на голосование или ждёт)."""
    r = c.execute("SELECT MAX(created_at) FROM subs WHERE uid=? AND status NOT IN ('rejected','check','removed') "
                  "AND created_at >= datetime('now','-7 day')", (uid,)).fetchone()
    return r[0]


def _link(post) -> str | None:
    ch = config.CHANNEL_ID
    return f"https://t.me/{ch.lstrip('@')}/{post}" if post and ch.startswith("@") else None


def _pub(r) -> dict:
    return {"id": r["id"], "status": r["status"], "reason": r["reason"], "scene": json.loads(r["data"]),
            "yes": r["yes"], "no": r["no"], "ends": r["ends"], "link": _link(r["post"]), "sign": r["sign"]}


@router.post("")
async def submit(body: SubIn, x_init_data: str | None = Header(default=None)):
    u = nunu._user(x_init_data)
    cf = cfg_get()
    if not cf["on"]:
        raise HTTPException(403, "Приём ситуаций сейчас закрыт")
    sc = [x.strip() if isinstance(x, str) else [y.strip() for y in x] if isinstance(x, list) else x for x in body.scene]
    if not _scene_ok(sc):
        raise HTTPException(422, "Нужны тема, ситуация и шесть поступков")
    ok, left = eligible(u.id)
    if not ok:
        raise HTTPException(403, f"Сначала пройди все ситуации: осталось {left}")
    with closing(_c()) as c, c:
        if _week_used(c, u.id):
            raise HTTPException(429, "Одна ситуация в неделю — следующую можно будет предложить позже")
        if c.execute("SELECT COUNT(*) FROM subs WHERE uid=? AND created_at >= datetime('now','-1 day')",
                     (u.id,)).fetchone()[0] >= AI_DAY:
            raise HTTPException(429, "На сегодня попыток хватит — продолжим завтра")
        if c.execute("SELECT 1 FROM subs WHERE uid=? AND status IN ('check','wait','voting')", (u.id,)).fetchone():
            raise HTTPException(409, "Твоя ситуация уже на проверке или голосовании")
        sid = secrets.token_hex(4)
        name = u.first_name[:30]
        c.execute("INSERT INTO subs(id, uid, name, sign, data, status) VALUES(?,?,?,?,?, 'check')",
                  (sid, u.id, name, "Участник" if body.anon else name, json.dumps(sc, ensure_ascii=False)))
    nunu._bg(process(sid))
    return {"id": sid, "status": "check"}


# ---------- проверка ИИ ----------
MOD = """Ты модератор игры «Ну-ну». Игроки предлагают бытовые ситуации и по 6 поступков: по два на «цветы»
(иду к людям, открываюсь), «деревья» (беру паузу, отхожу, обдумываю) и «скалы» (стою за своё, отстаиваю).
Проверь ситуацию. Отклони, если есть: грубость, оскорбления, ненависть, сексуальное, жестокость, опасные советы,
реклама или ссылки, настоящие имена и личные данные людей, политика; если это не жизненная ситуация; если поступки
явно не подходят к своей стихии; если текст бессвязный. Мелкие опечатки — не причина.
Ответь только JSON: {"ok": true} или {"ok": false, "reason": "коротко и дружелюбно, что поправить"}."""


async def moderate(sc: list) -> dict | None:
    """None — ИИ недоступен (нет ключа или ошибка): ситуация ждёт решения владельца."""
    if not config.ANTHROPIC_API_KEY:
        return None
    from .agent import _ask
    text = (f"Тема: {sc[0]}\nСитуация: {sc[1]}\n" + "\n".join(
        f"{n}: {sc[i + 2][0]} / {sc[i + 2][1]}" for i, (_, n) in enumerate(ZN)))
    try:
        out = await _ask(MOD, text, max_tokens=300)
        m = re.search(r"\{.*\}", out, re.S)
        r = json.loads(m.group(0)) if m else {}
        return {"ok": bool(r.get("ok")), "reason": str(r.get("reason") or "")[:300]}
    except Exception as e:
        log.warning("Проверка ситуации: %s", e)
        return None


async def process(sid: str) -> None:
    with closing(_c()) as c:
        r = c.execute("SELECT * FROM subs WHERE id=?", (sid,)).fetchone()
    if not r or r["status"] != "check":
        return
    m = await moderate(json.loads(r["data"]))
    with closing(_c()) as c, c:
        c.execute("UPDATE subs SET tries=tries+1 WHERE id=?", (sid,))
        if m is None:
            if not config.ANTHROPIC_API_KEY or r["tries"] + 1 >= 3:  # ИИ не ответил трижды — решит владелец в админке
                c.execute("UPDATE subs SET status='wait' WHERE id=?", (sid,))
            return
        if not m["ok"]:
            c.execute("UPDATE subs SET status='rejected', reason=? WHERE id=?", (m["reason"] or "Не прошла проверку", sid))
    if not m["ok"]:
        await nunu._notify(r["uid"], f"Твоя ситуация пока не прошла проверку: {m['reason'] or 'не подходит под правила'}\n"
                                     "Поправь и отправь ещё раз — недельное право не потрачено.", nunu.link())
        return
    await publish(sid)


# ---------- канал ----------
def _bot():
    from aiogram import Bot
    return Bot(token=config.BOT_TOKEN)


def post_text(sc: list, sign: str, rule: dict) -> str:
    from html import escape as E
    lines = [f"🗳 <b>Ситуации участников</b>\n\n<b>{E(sc[0])}</b>\n{E(sc[1])}\n"]
    lines += [f"{e} <i>{n}</i>: {E(sc[i + 2][0])} · {E(sc[i + 2][1])}" for i, (e, n) in enumerate(ZN)]
    lines += [f"\nАвтор: {E(sign)}", f"\n{rule_text(rule)}", "\n#ситуации_участников"]
    return "\n".join(lines)


async def publish(sid: str) -> bool:
    """Пост + опрос. Правило фиксируется в записи: настройки потом не меняют итог."""
    if not config.CHANNEL_ID:
        with closing(_c()) as c, c:
            c.execute("UPDATE subs SET status='wait', reason='Канал не задан (CHANNEL_ID)' WHERE id=?", (sid,))
        return False
    with closing(_c()) as c:
        r = c.execute("SELECT * FROM subs WHERE id=?", (sid,)).fetchone()
    rule = cfg_get()
    sc = json.loads(r["data"])
    bot = _bot()
    try:
        p = await bot.send_message(config.CHANNEL_ID, post_text(sc, r["sign"], rule), parse_mode="HTML")
        q = await bot.send_poll(config.CHANNEL_ID, "Добавить эту ситуацию в «Ну-ну»?", ["Да, в игру", "Нет, доработать"],
                                is_anonymous=True, reply_to_message_id=p.message_id)
    except Exception as e:
        log.warning("Публикация ситуации %s: %s", sid, e)
        with closing(_c()) as c, c:
            c.execute("UPDATE subs SET status='wait', reason=? WHERE id=?", (f"Не удалось опубликовать: {e}"[:300], sid))
        return False
    finally:
        await bot.session.close()
    ends = (datetime.now(timezone.utc) + timedelta(hours=rule["hours"])).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_c()) as c, c:
        c.execute("UPDATE subs SET status='voting', reason='', rule=?, post=?, poll=?, poll_id=?, ends=? WHERE id=?",
                  (json.dumps(rule), p.message_id, q.message_id, q.poll.id, ends, sid))
    await nunu._notify(r["uid"], f"Твоя ситуация «{sc[0]}» опубликована в канале — идёт голосование. "
                                 f"{rule_text(rule)}", _link(p.message_id) or nunu.link())
    return True


def on_poll(poll_id: str, counts: list[int]) -> None:
    """Обновление опроса от Telegram: держим счёт голосов в базе."""
    if len(counts) < 2:
        return
    with closing(_c()) as c, c:
        c.execute("UPDATE subs SET yes=?, no=? WHERE poll_id=? AND status='voting'", (counts[0], counts[1], poll_id))


def verdict(yes: int, no: int, rule: dict) -> str:
    """accept / extend / decline — одно правило для всех, без исключений."""
    total = yes + no
    if total >= rule["min"]:
        return "accept" if yes * 100 >= rule["pct"] * total else "decline"
    return "extend" if rule.get("extend") else "decline"


async def finish_due(now: datetime | None = None) -> None:
    now = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_c()) as c:
        due = c.execute("SELECT * FROM subs WHERE status='voting' AND ends<=?", (now,)).fetchall()
        stuck = c.execute("SELECT id FROM subs WHERE status='check' AND created_at <= datetime('now','-2 minute')").fetchall()
    for r in stuck:  # проверка не успела (перезапуск, ошибка сети) — ещё попытка
        await process(r["id"])
    for r in due:
        rule = json.loads(r["rule"])
        rule["extend"] = rule.get("extend") and not r["extended"]
        yes, no = r["yes"], r["no"]
        v = verdict(yes, no, rule)
        sc = json.loads(r["data"])
        bot = _bot()
        try:
            if v == "extend":
                ends = (datetime.now(timezone.utc) + timedelta(hours=rule["hours"])).strftime("%Y-%m-%d %H:%M:%S")
                with closing(_c()) as c, c:
                    c.execute("UPDATE subs SET ends=?, extended=1 WHERE id=?", (ends, r["id"]))
                await bot.send_message(config.CHANNEL_ID, f"⏳ Голосов пока {yes + no} из {rule['min']} нужных — "
                                       f"голосование продлено на {rule['hours']} ч.", reply_to_message_id=r["poll"])
                continue
            try:
                p = await bot.stop_poll(config.CHANNEL_ID, r["poll"])
                yes, no = p.options[0].voter_count, p.options[1].voter_count
                v = verdict(yes, no, rule | {"extend": False})
            except Exception as e:
                log.warning("Закрытие опроса %s: %s", r["id"], e)
            idx = nunu_admin.add_scene(sc, r["sign"] if r["sign"] != "Участник" else "", f"Ситуация участника: {sc[0]}") \
                if v == "accept" else None
            with closing(_c()) as c, c:
                c.execute("UPDATE subs SET status=?, yes=?, no=?, idx=? WHERE id=?",
                          ("accepted" if v == "accept" else "declined", yes, no, idx, r["id"]))
            tot = yes + no
            res = (f"✅ Принята: «за» {yes} из {tot}. Ситуация уже в игре — спасибо автору!" if v == "accept" else
                   f"Итог: «за» {yes} из {tot}" + ("" if tot >= rule["min"] else f", а нужно не меньше {rule['min']} голосов")
                   + ". В этот раз ситуация не прошла.")
            await bot.send_message(config.CHANNEL_ID, res, reply_to_message_id=r["poll"])
            await nunu._notify(r["uid"], (f"🎉 Твоя ситуация «{sc[0]}» принята: «за» {yes} из {tot}. Она уже в игре!"
                                          if v == "accept" else f"Голосование по «{sc[0]}» закончилось: «за» {yes} из {tot}. "
                                          "Ситуация не прошла — можно доработать и предложить новую через неделю."),
                               _link(r["post"]) or nunu.link())
        except Exception as e:
            log.warning("Итог голосования %s: %s", r["id"], e)
        finally:
            await bot.session.close()


async def loop() -> None:
    while True:
        try:
            await finish_due()
        except Exception as e:
            log.warning("Ситуации участников: %s", e)
        await asyncio.sleep(60)


async def remove(sid: str, why: str) -> None:
    """Владелец снимает ситуацию: пост и опрос удаляются, автору — причина."""
    with closing(_c()) as c:
        r = c.execute("SELECT * FROM subs WHERE id=?", (sid,)).fetchone()
    if not r or r["status"] not in OPEN:
        raise HTTPException(409, "Её уже нельзя снять")
    if r["post"] and config.CHANNEL_ID:
        bot = _bot()
        try:
            for m in (r["poll"], r["post"]):
                try:
                    await bot.delete_message(config.CHANNEL_ID, m)
                except Exception:
                    pass
        finally:
            await bot.session.close()
    with closing(_c()) as c, c:
        c.execute("UPDATE subs SET status='removed', reason=? WHERE id=?", (why[:300], sid))
    await nunu._notify(r["uid"], f"Твоя ситуация «{json.loads(r['data'])[0]}» снята модератором"
                                 + (f": {why}" if why else ".") + " Недельное право вернулось.", nunu.link())
