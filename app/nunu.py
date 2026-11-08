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
CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deeds (a INTEGER NOT NULL, b INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (a, b));
"""
MIGRATE = ("ALTER TABLE games ADD COLUMN to_id INTEGER",  # партия конкретному другу с карты
           "ALTER TABLE plays ADD COLUMN boards REAL")    # доски моста за эту партию


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)  # ponytail: схема на каждом подключении, IF NOT EXISTS дешёвый
    for m in MIGRATE:
        try:
            c.execute(m)
        except sqlite3.OperationalError:
            pass  # колонка уже есть
    return c


def _seen(c: sqlite3.Connection, u) -> None:
    """Имя из Telegram — для подписи острова у друзей."""
    with c:
        c.execute("INSERT OR REPLACE INTO users(tg_id, name) VALUES(?, ?)", (u.id, u.first_name[:30]))


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


def _one(x) -> bool:
    """Ответ: индекс 0–6 или 1–2 разных образа [i, j] (карточки «Кто я, по-твоему?»)."""
    if isinstance(x, list):
        return 1 <= len(x) <= 2 and len(set(x)) == len(x) and all(isinstance(y, int) and 0 <= y < PASS for y in x)
    return isinstance(x, int) and 0 <= x <= PASS


def _answers(a: list, empty_ok: bool = False) -> str:
    if (empty_ok and not a) or (len(a) == N and all(_one(x) for x in a)):
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
    s: list[int | list[int]]
    g: list[int | list[int]] = []
    to: int | None = None  # tg id друга: партия с карты, ему сразу сообщение от бота


class PlayIn(BaseModel):
    name: str
    s: list[int | list[int]] = []  # в hero друг только угадывает
    g: list[int | list[int]]


@router.get("/state")
def get_state(x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT data FROM state WHERE tg_id=?", (u.id,)).fetchone()
        _seen(c, u)
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
async def new_game(body: GameIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    if body.mode not in ("duo", "hero") or not 0 <= body.seed < 36 ** 4:
        raise HTTPException(422, "Неизвестная игра")
    s, g = _answers(body.s), _answers(body.g, empty_ok=body.mode == "hero")
    gid = secrets.token_hex(4)
    name = body.name.strip()[:30] or u.first_name
    with closing(_db()) as c, c:
        c.execute("INSERT INTO games(id, owner, name, seed, mode, s, g, to_id) VALUES(?,?,?,?,?,?,?,?)",
                  (gid, u.id, name, body.seed, body.mode, s, g, body.to))
        _seen(c, u)
    if body.to:
        _bg(_notify(body.to, f"{name} прислал тебе партию в «Ну-ну». Твой ход 🏝", link(gid)))
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
    out = {"id": gid, "link": link(gid), "mode": r["mode"], "seed": r["seed"], "name": r["name"], "owner": r["owner"],
           "mine": mine, "played": played, "plays": []}
    if mine or played:
        out |= {"s": json.loads(r["s"]), "g": json.loads(r["g"])}
    if played:
        me = next(p for p in plays if p["player"] == uid)
        out["my"] = {"name": me["name"], "s": json.loads(me["s"]), "g": json.loads(me["g"])}
    if mine:
        out["plays"] = [{"id": p["player"], "name": p["name"], "s": json.loads(p["s"]), "g": json.loads(p["g"])} for p in plays]
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
        _seen(c, u)
    _bg(_notify(r["owner"], f"{name} ответил в «Ну-ну». Посмотри ваш мост 🌉", link(gid)))
    return view


def _bg(coro) -> None:
    """Не ждём Telegram: ответ игроку не должен висеть, пока бот шлёт сообщение."""
    t = asyncio.get_running_loop().create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


class BoardsIn(BaseModel):
    friend: int
    n: float


@router.post("/game/{gid}/boards")
def boards(gid: str, body: BoardsIn, x_init_data: str | None = Header(default=None)):
    """Доски моста за партию считает клиент (зоны ответов живут в карточках на странице).
    Оба игрока видят одно и то же, поэтому пишется только первое значение."""
    u = _user(x_init_data)
    if not 0 <= body.n <= 2 * N:
        raise HTTPException(422, "Столько досок не бывает")
    with closing(_db()) as c, c:
        r = c.execute("SELECT owner FROM games WHERE id=?", (gid,)).fetchone()
        if not r or r["owner"] not in (u.id, body.friend):
            raise HTTPException(404, "Игра не найдена")
        player = body.friend if r["owner"] == u.id else u.id
        c.execute("UPDATE plays SET boards=? WHERE game_id=? AND player=? AND boards IS NULL",
                  (body.n, gid, player))
    return {"ok": True}


@router.post("/friend/{fid}/deed")
def deed(fid: int, x_init_data: str | None = Header(default=None)):
    """Сделали дело вместе в жизни: +3 доски общему мосту."""
    u = _user(x_init_data)
    a, b = sorted((u.id, fid))
    with closing(_db()) as c, c:
        c.execute("INSERT INTO deeds(a, b, n) VALUES(?,?,1) ON CONFLICT(a, b) DO UPDATE SET n=n+1", (a, b))
    return {"ok": True}


def _island(c: sqlite3.Connection, uid: int) -> list[int]:
    """Сад, лес, скалы: сколько семян в каждой зоне (из сада игрока)."""
    r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
    z = [0, 0, 0]
    for s in (json.loads(r["data"]).get("nunu-seeds") or [] if r else []):
        if isinstance(s, dict) and s.get("z") in (0, 1, 2):
            z[s["z"]] += 1
    return z


@router.get("/world")
def world(x_init_data: str | None = Header(default=None)):
    """Карта: мой остров и острова тех, с кем есть хоть одна партия или приглашение.
    ponytail: всё считается на лету из games/plays; кэш, когда друзей станет сотни."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        _seen(c, u)
        rows = c.execute("""SELECT g.id, g.owner, g.to_id, g.created_at, p.player, p.boards, p.created_at AS pt
            FROM games g LEFT JOIN plays p ON p.game_id = g.id
            WHERE g.owner = :me OR g.to_id = :me OR p.player = :me""", {"me": u.id}).fetchall()
        fr: dict[int, dict] = {}
        for r in rows:
            other = r["player"] if r["owner"] == u.id else r["owner"]
            if r["player"] is None:  # партия ещё без ответа
                other = r["to_id"] if r["owner"] == u.id else r["owner"]
            if not other or other == u.id or (r["player"] not in (None, u.id) and r["owner"] != u.id):
                continue
            f = fr.setdefault(other, {"id": other, "boards": 0.0, "turn": None, "gid": None, "last": None, "t": ""})
            if r["player"] is None:
                f["turn"], f["gid"] = ("them" if r["owner"] == u.id else "me"), r["id"]
            else:
                f["boards"] += r["boards"] or 0
                if (r["pt"] or "") >= f["t"]:
                    f["last"], f["t"] = r["id"], r["pt"] or ""
        for f in fr.values():
            a, b = sorted((u.id, f["id"]))
            d = c.execute("SELECT n FROM deeds WHERE a=? AND b=?", (a, b)).fetchone()
            f["boards"] += 3 * (d["n"] if d else 0)
            n = c.execute("SELECT name FROM users WHERE tg_id=?", (f["id"],)).fetchone()
            f["name"] = n["name"] if n else "Друг"
            f["island"] = _island(c, f["id"])
            del f["t"]
        return {"me": {"id": u.id, "name": u.first_name, "island": _island(c, u.id)},
                "friends": sorted(fr.values(), key=lambda f: -f["boards"])}


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
