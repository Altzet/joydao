"""«Ну-ну» на сервере: вход через Telegram (initData), игра по одной ссылке, история игрока.

Игрок ничего не вводит: Telegram сам говорит, кто он. Ссылка t.me/<бот>/<app>?startapp=<id игры>
открывает ту же партию у друга, его ответы ложатся на сервер, хозяину приходит сообщение.
Сад, мосты и текущая партия хранятся одним JSON на игрока: на другом телефоне или ПК всё на месте.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import config

log = logging.getLogger("nunu")
DB = Path(__file__).parent.parent / "nunu.db"
N, PASS = 10, 6  # 10 карточек; ответ 0–5, 6 = «Проехали» (как в static/nunu.html)
router = APIRouter(prefix="/nunu/api")
_tasks: set = set()  # фоновые уведомления (держим ссылку, чтобы задачу не собрал GC)

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (tg_id INTEGER PRIMARY KEY, data TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, owner INTEGER NOT NULL, name TEXT NOT NULL,
    seed INTEGER NOT NULL, mode TEXT NOT NULL, s TEXT NOT NULL, g TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS plays (game_id TEXT NOT NULL, player INTEGER NOT NULL, name TEXT NOT NULL,
    s TEXT NOT NULL, g TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (game_id, player));
"""


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)  # ponytail: схема на каждом подключении, IF NOT EXISTS дешёвый
    return c


def _user(init_data: str | None):
    """Кто играет: проверка подписи Telegram initData. Без неё — 401."""
    from aiogram.utils.web_app import safe_parse_webapp_init_data
    try:
        u = safe_parse_webapp_init_data(config.BOT_TOKEN, init_data or "").user
    except Exception:
        u = None
    if not u:
        raise HTTPException(401, "Откройте игру в Telegram")
    return u


def _answers(a: list[int], empty_ok: bool = False) -> str:
    if (empty_ok and not a) or (len(a) == N and all(0 <= x <= PASS for x in a)):
        return json.dumps(a)
    raise HTTPException(422, "Нужно 10 ответов")


def link(gid: str) -> str:
    return f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}?startapp={gid}"


class StateIn(BaseModel):
    data: dict


class GameIn(BaseModel):
    name: str
    seed: int
    mode: str  # duo — оба отвечают; hero — все угадывают хозяина
    s: list[int]
    g: list[int] = []


class PlayIn(BaseModel):
    name: str
    s: list[int] = []  # в hero друг только угадывает
    g: list[int]


@router.get("/state")
def get_state(x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT data FROM state WHERE tg_id=?", (u.id,)).fetchone()
    return {"data": json.loads(r["data"]) if r else None, "name": u.first_name}


@router.put("/state")
def put_state(body: StateIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    data = json.dumps(body.data, ensure_ascii=False)
    if len(data) > 300_000:
        raise HTTPException(413, "Слишком большой сад")
    with closing(_db()) as c, c:
        c.execute("INSERT INTO state(tg_id, data) VALUES(?, ?) ON CONFLICT(tg_id) DO UPDATE "
                  "SET data=excluded.data, updated_at=datetime('now')", (u.id, data))
    return {"ok": True}


@router.post("/game")
def new_game(body: GameIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    if body.mode not in ("duo", "hero") or not 0 <= body.seed < 36 ** 4:
        raise HTTPException(422, "Неизвестная игра")
    s, g = _answers(body.s), _answers(body.g, empty_ok=body.mode == "hero")
    gid = secrets.token_hex(4)
    with closing(_db()) as c, c:
        c.execute("INSERT INTO games(id, owner, name, seed, mode, s, g) VALUES(?,?,?,?,?,?,?)",
                  (gid, u.id, body.name.strip()[:30] or u.first_name, body.seed, body.mode, s, g))
    return {"id": gid, "link": link(gid)}


def _view(c: sqlite3.Connection, gid: str, uid: int) -> dict:
    """Чужие ответы видит только тот, кто уже ответил сам: иначе угадывать нечестно."""
    r = c.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not r:
        raise HTTPException(404, "Игра не найдена")
    mine = r["owner"] == uid
    plays = c.execute("SELECT player, name, s, g FROM plays WHERE game_id=? ORDER BY created_at",
                      (gid,)).fetchall()
    played = any(p["player"] == uid for p in plays)
    out = {"id": gid, "link": link(gid), "mode": r["mode"], "seed": r["seed"], "name": r["name"],
           "mine": mine, "played": played, "plays": []}
    if mine or played:
        out |= {"s": json.loads(r["s"]), "g": json.loads(r["g"])}
    if played:
        me = next(p for p in plays if p["player"] == uid)
        out["my"] = {"name": me["name"], "s": json.loads(me["s"]), "g": json.loads(me["g"])}
    if mine:
        out["plays"] = [{"name": p["name"], "s": json.loads(p["s"]), "g": json.loads(p["g"])} for p in plays]
    return out


@router.get("/game/{gid}")
def get_game(gid: str, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    with closing(_db()) as c:
        return _view(c, gid, u.id)


@router.post("/game/{gid}/play")
async def play(gid: str, body: PlayIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT owner, mode FROM games WHERE id=?", (gid,)).fetchone()
        if not r:
            raise HTTPException(404, "Игра не найдена")
        if r["owner"] == u.id:
            raise HTTPException(409, "Это твоя игра")
        name = body.name.strip()[:30] or u.first_name
        with c:
            c.execute("INSERT OR REPLACE INTO plays(game_id, player, name, s, g) VALUES(?,?,?,?,?)",
                      (gid, u.id, name, _answers(body.s, empty_ok=r["mode"] == "hero"), _answers(body.g)))
        view = _view(c, gid, u.id)
    # не ждём Telegram: ответ другу не должен висеть, пока бот шлёт сообщение
    t = asyncio.get_running_loop().create_task(
        _notify(r["owner"], f"{name} ответил в «Ну-ну». Посмотри ваш мост 🌉", link(gid)))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return view


async def _notify(tg_id: int, text: str, url: str) -> None:
    """Сообщение хозяину игры. Не написал боту /start — Telegram не пустит, это не ошибка игры."""
    from aiogram import Bot
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    bot = Bot(token=config.BOT_TOKEN)
    try:
        await bot.send_message(tg_id, text, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть «Ну-ну»", url=url)]]))
    except Exception as e:
        log.info("Не отправил уведомление %s: %s", tg_id, e)
    finally:
        await bot.session.close()
