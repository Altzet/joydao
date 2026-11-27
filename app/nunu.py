"""«Ну-ну» на сервере: вход через Telegram (initData), игра по одной ссылке, история игрока.

Игрок ничего не вводит: Telegram сам говорит, кто он. Ссылка t.me/<бот>/<app>?startapp=<id игры>
открывает ту же партию у друга, его ответы ложатся на сервер, хозяину приходит сообщение.
Сад, мосты и текущая партия хранятся одним JSON на игрока: на другом телефоне или ПК всё на месте.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import config

log = logging.getLogger("nunu")
DB = Path(__file__).parent.parent / "nunu.db"
N, PASS = 10, 6  # до 10 карточек; ответ 0–5, 6 = «Проехали» (как в static/nunu.html)
MIN_N = 3  # короткие партии (25.11): с другом — 3, бутылка — 3–9
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
CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, owner INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS friends (a INTEGER NOT NULL, b INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS wishes (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS deeds (a INTEGER NOT NULL, b INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS deed_ids (pid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS deed_calls (pid TEXT NOT NULL, by INTEGER NOT NULL, to_id INTEGER NOT NULL, text TEXT NOT NULL,
    ans TEXT, seed TEXT, PRIMARY KEY (pid, to_id));
CREATE TABLE IF NOT EXISTS plot_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, author INTEGER NOT NULL,
    obj TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS plot_visits (owner INTEGER NOT NULL, guest INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY (owner, guest));
CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, seed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meets (id TEXT PRIMARY KEY, by INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
    ok_a INTEGER NOT NULL DEFAULT 0, ok_b INTEGER NOT NULL DEFAULT 0, UNIQUE (by, a, b));
CREATE TABLE IF NOT EXISTS reminders (tg_id INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (tg_id, kind));
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, by INTEGER NOT NULL, uid INTEGER NOT NULL,
    kind TEXT NOT NULL, ref TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', created_at TEXT DEFAULT (datetime('now')), UNIQUE (by, kind, ref));
CREATE TABLE IF NOT EXISTS sanctions (uid INTEGER PRIMARY KEY, level INTEGER NOT NULL, at TEXT);
"""
MIGRATE = ("ALTER TABLE games ADD COLUMN to_id INTEGER",  # партия конкретному другу с карты
           "ALTER TABLE plays ADD COLUMN boards REAL",    # доски моста за эту партию
           "ALTER TABLE games ADD COLUMN story TEXT",     # своя ситуация автора: {q, a, real}
           "ALTER TABLE plays ADD COLUMN guess TEXT",     # догадка друга своими словами
           "ALTER TABLE plays ADD COLUMN mark INTEGER",   # автор отметил: 2 угадал, 1 почти, 0 нет
           "ALTER TABLE games ADD COLUMN bottle INTEGER",  # бутылка в море: угадывают незнакомцы
           "ALTER TABLE games ADD COLUMN reported INTEGER",  # жалоба: бутылка больше не ловится
           "ALTER TABLE games ADD COLUMN lv INTEGER",  # короткие партии (25.11): ступень лесенки, 9 — бутылка
           "ALTER TABLE state ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")  # версия состояния: устройства не затирают друг друга
BOTTLE_CATCHES = 5  # ponytail: столько незнакомцев могут выловить одну бутылку; подстроить по данным
STORY_BOARDS = {2: 1.0, 1: 0.5}  # своя ситуация: угадал — доска, почти — верёвочка


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


LOGIN_DAYS = 30  # ponytail: вход на сайте через Telegram живёт месяц, потом нажать «Войти» ещё раз
SITE = "tglogin:"  # так игра на сайте помечает данные виджета в том же заголовке X-Init-Data


def _site_login(tg_login: str):
    """Сайт: данные виджета «Войти через Telegram»
    (подпись: HMAC-SHA256 ключом sha256(токена бота), core.telegram.org/widgets/login). Не сошлось — None."""
    try:
        d = {k: str(v) for k, v in json.loads(urllib.parse.unquote(tg_login)).items()}
        h = d.pop("hash")
        check = "\n".join(f"{k}={d[k]}" for k in sorted(d))
        good = hmac.new(hashlib.sha256(config.BOT_TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(h, good) and time.time() - int(d["auth_date"]) < LOGIN_DAYS * 86400:
            return SimpleNamespace(id=int(d["id"]), first_name=d.get("first_name", "")[:30] or "Участник", username=d.get("username"))
    except Exception:
        pass
    return None


def _user(init_data: str | None):
    """Кто играет: в Telegram — подпись initData, на сайте — подпись виджета входа. Без них — 401."""
    from aiogram.utils.web_app import safe_parse_webapp_init_data
    site = (init_data or "").startswith(SITE)
    try:
        u = _site_login(init_data[len(SITE):]) if site else safe_parse_webapp_init_data(config.BOT_TOKEN, init_data or "").user
    except Exception:
        u = None
    if not u:
        raise HTTPException(401, "Войди через Telegram ещё раз" if site else "Откройте игру в Telegram")
    with closing(_db()) as c:
        if _level(c, u.id) >= 3:
            raise HTTPException(403, "Доступ к «Ну-ну» закрыт по жалобам участников. Если это ошибка — напиши автору игры.")
    return u


def _level(c: sqlite3.Connection, uid: int) -> int:
    """Ступень по жалобам (nunu_reports): 2 — нельзя писать другим, 3 — доступ закрыт."""
    r = c.execute("SELECT level FROM sanctions WHERE uid=?", (uid,)).fetchone()
    return r["level"] if r else 0


def _muted(c: sqlite3.Connection, uid: int) -> None:
    if _level(c, uid) >= 2:
        raise HTTPException(403, "Пока нельзя: ограничение по жалобам участников")


def _one(x) -> bool:
    """Ответ: индекс 0–6 или 1–2 разных образа [i, j] (карточки «Кто я, по-твоему?»)."""
    if isinstance(x, list):
        return 1 <= len(x) <= 2 and len(set(x)) == len(x) and all(isinstance(y, int) and 0 <= y < PASS for y in x)
    return isinstance(x, int) and 0 <= x <= PASS


def _answers(a: list, empty_ok: bool = False, n: int | None = None) -> str:
    """Ответы партии: старые партии — 10, короткие (с 25.11) — 3–9; n — сколько должно быть (как у хозяина)."""
    if (empty_ok and not a) or (MIN_N <= len(a) <= N and (n is None or len(a) == n) and all(_one(x) for x in a)):
        return json.dumps(a)
    raise HTTPException(422, "Неверное число ответов")


def link(gid: str = "") -> str:
    base = f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}"
    return f"{base}?startapp={gid}" if gid else base


class StateIn(BaseModel):
    data: dict
    base: int | None = None  # версия, от которой шли правки; None — старый клиент (пишет как раньше)


class Story(BaseModel):
    q: str     # что случилось
    a: str     # как поступил или поступил бы
    real: bool  # так было на самом деле
    o: list[str] = []  # другие варианты для угадывания (до 3): выбрал вариант — отметка ставится сама
    only: bool = False  # партия только из своей ситуации, без 10 карточек


class GameIn(BaseModel):
    name: str
    seed: int
    mode: str  # duo — оба отвечают; hero — все угадывают хозяина
    s: list[int | list[int]]
    g: list[int | list[int]] = []
    to: int | None = None  # tg id друга: партия с карты, ему сразу сообщение от бота
    story: Story | None = None  # своя ситуация: друг угадывает своими словами
    bottle: bool = False  # бутылка в море (только hero): ловят незнакомцы
    lv: int | None = None  # ступень лесенки (0–3) или 9 — бутылка; None — старая партия из 10




class PlayIn(BaseModel):
    name: str
    s: list[int | list[int]] = []  # в hero друг только угадывает
    g: list[int | list[int]]
    guess: str | None = None  # как, по-твоему, поступил автор своей ситуации


class MarkIn(BaseModel):
    player: int
    mark: int


@router.get("/state")
def get_state(since: int | None = None, x_init_data: str | None = Header(default=None)):
    """Состояние игрока + версия + входящие семена (общие дела от друзей). Входящие отдаются один раз:
    ponytail: если клиент не успел сохранить сад, семя потеряется — друг всё равно получил сообщение от бота.
    since=версия у клиента: если не менялось, данные не шлём (опрос раз в 15 с с телефона)."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT data, rev FROM state WHERE tg_id=?", (u.id,)).fetchone()
        inbox = [json.loads(x["seed"]) for x in c.execute("SELECT seed FROM inbox WHERE tg_id=? ORDER BY id", (u.id,))]
        with c:
            c.execute("DELETE FROM inbox WHERE tg_id=?", (u.id,))
        _seen(c, u)
    rev = r["rev"] if r else 0
    if since is not None and since == rev:
        return {"same": True, "rev": rev, "name": u.first_name, "inbox": inbox}
    return {"data": json.loads(r["data"]) if r else None, "rev": rev, "name": u.first_name, "inbox": inbox}


@router.put("/state")
def put_state(body: StateIn, x_init_data: str | None = Header(default=None)):
    """Сохранить всё состояние. Если base не совпал с версией на сервере — другое устройство успело раньше:
    409 с его данными, клиент сольёт правки и пришлёт снова. Так ни одно действие не затирается."""
    u = _user(x_init_data)
    data = json.dumps(body.data, ensure_ascii=False)
    if len(data) > 300_000:
        raise HTTPException(413, "Слишком большой сад")
    with closing(_db()) as c:
        c.execute("BEGIN IMMEDIATE")  # проверка версии и запись — одним шагом
        try:
            r = c.execute("SELECT data, rev FROM state WHERE tg_id=?", (u.id,)).fetchone()
            rev = r["rev"] if r else 0
            if body.base is not None and body.base != rev:
                raise HTTPException(409, {"rev": rev, "data": json.loads(r["data"]) if r else None})
            c.execute("INSERT INTO state(tg_id, data, rev) VALUES(?, ?, 1) ON CONFLICT(tg_id) DO UPDATE "
                      "SET data=excluded.data, rev=state.rev+1, updated_at=datetime('now')", (u.id, data))
            c.commit()
        except BaseException:
            c.rollback()
            raise
    return {"ok": True, "rev": rev + 1}


@router.post("/game")
async def new_game(body: GameIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    if body.mode not in ("duo", "hero") or not 0 <= body.seed < 2 * 36 ** 4 or (body.bottle and body.mode != "hero"):
        raise HTTPException(422, "Неизвестная игра")
    if body.lv is not None and body.lv not in (0, 1, 2, 3, 9):
        raise HTTPException(422, "Неизвестная ступень")
    s = _answers(body.s)
    g = _answers(body.g, empty_ok=body.mode == "hero", n=len(body.s))
    gid = secrets.token_hex(4)
    name = body.name.strip()[:30] or u.first_name
    story = None
    if body.story and body.story.q.strip() and body.story.a.strip():
        with closing(_db()) as c:
            _muted(c, u.id)
        a = body.story.a.strip()[:250]
        o = list(dict.fromkeys(x.strip()[:250] for x in body.story.o if x.strip() and x.strip() != a))[:3]
        story = json.dumps({"q": body.story.q.strip()[:400], "a": a, "real": body.story.real, "o": o,
                            "only": body.story.only}, ensure_ascii=False)
    with closing(_db()) as c, c:
        c.execute("INSERT INTO games(id, owner, name, seed, mode, s, g, to_id, story, bottle, lv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (gid, u.id, name, body.seed, body.mode, s, g, body.to, story, 1 if body.bottle else None, body.lv))
        _seen(c, u)
        ga = _gn(c, u.id)
    if body.to:
        _bg(_notify(body.to, f"{name} {gw(ga, 'прислал', 'прислала', 'шлёт')} тебе партию в «Ну-ну». Твой ход 🏝", link(gid), pic="party"))
    return {"id": gid, "link": link(gid)}


def _view(c: sqlite3.Connection, gid: str, uid: int) -> dict:
    """Чужие ответы видит только тот, кто уже ответил сам: иначе угадывать нечестно."""
    r = c.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not r:
        raise HTTPException(404, "Игра не найдена")
    mine = r["owner"] == uid
    plays = c.execute("SELECT player, name, s, g, guess, mark FROM plays WHERE game_id=? ORDER BY created_at",
                      (gid,)).fetchall()
    played = any(p["player"] == uid for p in plays)
    out = {"id": gid, "link": link(gid), "mode": r["mode"], "seed": r["seed"], "name": r["name"], "owner": r["owner"], "gn": _gn(c, r["owner"]),
           "mine": mine, "played": played, "plays": [], "bottle": bool(r["bottle"]),
           "lv": r["lv"], "n": len(json.loads(r["s"]))}
    if mine or played:
        out |= {"s": json.loads(r["s"]), "g": json.loads(r["g"])}
    if r["story"]:  # как поступил автор — только тем, кто уже угадал
        st = json.loads(r["story"])
        hidden = {"q": st["q"], "real": st["real"], "only": st.get("only", False)}
        if st.get("o"):  # варианты вперемешку: какой верный, не видно
            hidden["c"] = secrets.SystemRandom().sample([st["a"], *st["o"]], len(st["o"]) + 1)
        out["story"] = st if mine or played else hidden
    if played:
        me = next(p for p in plays if p["player"] == uid)
        out["my"] = {"name": me["name"], "s": json.loads(me["s"]), "g": json.loads(me["g"]),
                     "guess": me["guess"], "mark": me["mark"]}
        if r["bottle"]:
            out["my"]["wish"] = _wish(c, uid, r["owner"])
    if mine:
        out["plays"] = [{"id": p["player"], "name": p["name"], "gn": _gn(c, p["player"]), "s": json.loads(p["s"]), "g": json.loads(p["g"]),
                         "guess": p["guess"], "mark": p["mark"],
                         **({"wish": _wish(c, uid, p["player"])} if r["bottle"] else {})} for p in plays]
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
        r = c.execute("SELECT owner, mode, story, bottle, s FROM games WHERE id=?", (gid,)).fetchone()
        if not r:
            raise HTTPException(404, "Игра не найдена")
        if r["owner"] == u.id:
            raise HTTPException(409, "Это твоя игра")
        guess = (body.guess or "").strip()[:250] if r["story"] and _level(c, u.id) < 2 else ""
        st = json.loads(r["story"]) if r["story"] else {}
        auto = 2 if guess and guess == st.get("a") else 0 if guess in (st.get("o") or []) else None
        name = body.name.strip()[:30] or u.first_name
        with c:
            c.execute("INSERT OR REPLACE INTO plays(game_id, player, name, s, g, guess, mark) VALUES(?,?,?,?,?,?,?)",
                      (gid, u.id, name, _answers(body.s, empty_ok=r["mode"] == "hero", n=len(json.loads(r["s"]))),
                       _answers(body.g, n=len(json.loads(r["s"]))),
                       guess or None, auto))
        view = _view(c, gid, u.id)
        _seen(c, u)
        ga = _gn(c, u.id)
    V = lambda m, f, n: gw(ga, m, f, n)  # noqa: E731 — слово в роде того, кто сыграл
    text = (V(f"Твою бутылку выловил {name} и угадывал тебя 🍾 Посмотри, как он тебя понял",
              f"Твою бутылку выловила {name} и угадывала тебя 🍾 Посмотри, как она тебя поняла",
              f"{name}: твоя бутылка выловлена, догадки о тебе готовы 🍾 Посмотри, как тебя поняли") if r["bottle"]
            else V(f"{name} угадывал твою ситуацию и {'угадал' if auto else 'не угадал'} 🌉",
                   f"{name} угадывала твою ситуацию и {'угадала' if auto else 'не угадала'} 🌉",
                   f"{name}: догадка о твоей ситуации — {'угадано' if auto else 'не угадано'} 🌉") if auto is not None
            else f"{name} {V('ответил', 'ответила', 'отвечает')} в «Ну-ну» и {V('угадывал', 'угадывала', 'угадывает')} твою ситуацию. "
                 f"Отметь, {V('угадал ли', 'угадала ли', 'угадано ли')} ✍️" if guess
            else f"{name} {V('ответил', 'ответила', 'отвечает')} в «Ну-ну». Посмотри ваш мост 🌉")
    _bg(_notify(r["owner"], text, link(f"{gid}_{u.id}"), pic="bottle" if r["bottle"] else "party"))  # сразу мост с ответившим
    return view


@router.post("/game/{gid}/mark")
async def mark(gid: str, body: MarkIn, x_init_data: str | None = Header(default=None)):
    """Автор своей ситуации отмечает догадку друга: угадал — доска мосту, почти — верёвочка."""
    u = _user(x_init_data)
    if body.mark not in (0, 1, 2):
        raise HTTPException(422, "Отметка 0, 1 или 2")
    with closing(_db()) as c:
        r = c.execute("SELECT owner FROM games WHERE id=?", (gid,)).fetchone()
        if not r or r["owner"] != u.id:
            raise HTTPException(404, "Игра не найдена")
        with c:
            n = c.execute("UPDATE plays SET mark=? WHERE game_id=? AND player=? AND guess IS NOT NULL AND mark IS NULL",
                          (body.mark, gid, body.player)).rowcount
        ga, gp = _gn(c, u.id), _gn(c, body.player)
    if n:
        m_, f_, n_ = [("не совсем угадал", "не совсем угадала", "не совсем угадано"), ("почти угадал", "почти угадала", "почти угадано"),
                      ("угадал", "угадала", "угадано")][body.mark]
        word = gw(gp, "ты " + m_, "ты " + f_, n_)
        _bg(_notify(body.player, f"{u.first_name} {gw(ga, 'отметил', 'отметила', 'отмечает')} твою догадку: {word} 🌉", link(gid), pic="guess"))
    return {"ok": bool(n)}


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
        return {"ok": True, "total": _pair_boards(c, u.id, body.friend)}


def _pair_boards(c: sqlite3.Connection, a: int, b: int) -> float:
    """Весь мост пары: доски всех партий (кроме бутылок), своих ситуаций и дел вместе.
    Экран итога берёт число отсюда, а не складывает сам: иначе одна партия считалась дважды."""
    s = 0.0
    for r in c.execute("""SELECT p.boards, p.mark FROM plays p JOIN games g ON g.id = p.game_id
            WHERE g.bottle IS NULL AND ((g.owner = ? AND p.player = ?) OR (g.owner = ? AND p.player = ?))""",
                       (a, b, b, a)):
        s += (r["boards"] or 0) + STORY_BOARDS.get(r["mark"], 0)
    x, y = sorted((a, b))
    d = c.execute("SELECT n FROM deeds WHERE a=? AND b=?", (x, y)).fetchone()
    return s + 3 * (d["n"] if d else 0)


class DeedIn(BaseModel):
    pid: str | None = None  # id общего дела: отметят оба — доски засчитаются один раз


class PlanIn(BaseModel):
    obj: str | None = None  # предложено в гостях: у какого объекта участка друга посадить
    pid: str
    text: str
    due: str | None = None  # YYYY-MM-DD
    at: str | None = None  # HH:MM по времени того, кто зовёт
    tz: int | None = None  # его getTimezoneOffset(), минуты


def _related(c: sqlite3.Connection, a: int, b: int) -> bool:
    """Друзья — те, кто на карте друг у друга: приглашение или хоть одна партия (не бутылка)."""
    return _friends(c, a, b) or c.execute(
        "SELECT 1 FROM games g LEFT JOIN plays p ON p.game_id=g.id WHERE g.bottle IS NULL AND "
        "((g.owner=? AND (g.to_id=? OR p.player=?)) OR (g.owner=? AND (g.to_id=? OR p.player=?))) LIMIT 1",
        (a, b, b, b, a, a)).fetchone() is not None


@router.post("/friend/{fid}/deed")
def deed(fid: int, body: DeedIn | None = None, x_init_data: str | None = Header(default=None)):
    """Сделали дело вместе в жизни: +3 доски общему мосту (одно дело — один раз, даже если отметили оба)."""
    u = _user(x_init_data)
    a, b = sorted((u.id, fid))
    with closing(_db()) as c, c:
        if body and body.pid:
            try:
                c.execute("INSERT INTO deed_ids(pid) VALUES(?)", (body.pid[:16],))
            except sqlite3.IntegrityError:
                return {"ok": True, "counted": False}
        c.execute("INSERT INTO deeds(a, b, n) VALUES(?,?,1) ON CONFLICT(a, b) DO UPDATE SET n=n+1", (a, b))
    return {"ok": True, "counted": True}


@router.post("/friend/{fid}/plan")
async def plan_deed(fid: int, body: PlanIn, x_init_data: str | None = Header(default=None)):
    """Общее дело: другу — сообщение от бота и то же семя в его сад (появится при следующем открытии игры)."""
    u = _user(x_init_data)
    text, due = body.text.strip()[:300], (body.due or "")[:10] or None
    if not text or not body.pid.strip():
        raise HTTPException(422, "Пустое дело")
    with closing(_db()) as c:
        if not _related(c, u.id, fid):
            raise HTTPException(404, "Друг не найден")
        _muted(c, u.id)
        here = _plot_obj(c, fid, body.obj) if body.obj and not (_owner_state(c, fid).get("nunu-prefs") or {}).get("visitClosed") else None
        seed = deed_seed(text, body.pid.strip()[:16], u.id, u.id, u.first_name) | {
            "due": due, "at": (body.at or "")[:5] or None, "tz": body.tz}
        if here and "x" in here:  # семя встанет у этого объекта на участке друга
            seed["loc"] = {"x": here["x"] + (here.get("w") or 0) / 2, "y": here["y"] + (here.get("h") or 0) / 2,
                           "o": here["id"], "dx": 0, "dy": 0}
        with c:
            c.execute("INSERT INTO inbox(tg_id, seed) VALUES(?, ?)", (fid, json.dumps(seed, ensure_ascii=False)))
            c.execute("INSERT OR REPLACE INTO deed_calls(pid, by, to_id, text) VALUES(?,?,?,?)", (seed["pid"], u.id, fid, text))
        _seen(c, u)
        gf = _gn(c, fid)
    at = f" — на твоём участке у «{here.get('n', '')}»" if here else ""
    _bg(_notify(fid, f"{u.first_name} зовёт тебя на общее дело 🤝{at}\n\n{text}\n\nОно уже в твоём саду. "
                     f"Когда сделаете, отметьте «{gw(gf, 'Так и сделал', 'Так и сделала', 'Получилось')}» — ваш мост получит +3 доски", link("plot" if here else "garden"), pic="joint",
                     btns=[("🤝 Принимаю", f"jd:y:{seed['pid']}"), ("Не сейчас", f"jd:n:{seed['pid']}")]))
    return {"ok": True}


def deed_seed(text: str, pid: str, by: int, fid: int, fname: str) -> dict:
    """Семя общего дела для сада: с кем (fid, fname) и кто позвал (by). Один pid у обоих — доски засчитаются один раз."""
    return {"c": "Удиви себя", "q": f"Общее дело · вдвоём: я и {fname[:30]}", "r": text, "z": -1,
            "d": date.today().isoformat(), "due": None, "at": None, "tz": None, "life": None, "mine": False, "step": 1,
            "with": fname[:30], "fid": fid, "by": by, "pid": pid}


def deed_answer(data: str, uid: int) -> tuple[str, tuple[int, str] | None]:
    """Кнопка под приглашением на общее дело: «Принимаю» / «Не сейчас».
    Ответ — строчка тому, кто нажал, и сообщение автору дела (один раз: повторное нажатие автору не пишет)."""
    yes, pid = data.startswith("jd:y:"), data[5:]
    with closing(_db()) as c, c:
        r = c.execute("SELECT * FROM deed_calls WHERE pid=? AND to_id=?", (pid, uid)).fetchone()
        if not r:
            return "Это дело уже не найти — загляни в сад 🌱", None
        first = c.execute("UPDATE deed_calls SET ans=? WHERE pid=? AND to_id=? AND ans IS NULL",
                          ("y" if yes else "n", pid, uid)).rowcount
        if first and yes and r["seed"]:  # обмен: в сад принявшего дело попадает только сейчас
            c.execute("INSERT INTO inbox(tg_id, seed) VALUES(?, ?)", (uid, r["seed"]))
        nm = lambda x: (c.execute("SELECT name FROM users WHERE tg_id=?", (x,)).fetchone() or {"name": "Друг"})["name"]  # noqa: E731
        by_name, me = nm(r["by"]), nm(uid)
        g = _gn(c, uid)
    if yes:
        reply = f"Договорились 🤝 {by_name} узнает. Дело в твоём саду — когда сделаете, отметьте его там."
        note = (f"{me} {gw(g, 'принял', 'приняла', 'принимает')} общее дело 🤝\n\n«{r['text']}»\n\n"
                "Когда сделаете, отметьте в саду — ваш мост получит +3 доски.")
    else:
        reply = f"Хорошо, {by_name} узнает, что пока не выходит." + ("" if r["seed"] else " Дело останется в саду — вдруг получится.")
        note = f"{me}: пока не выходит 🙏\n\n«{r['text']}»\n\nМожно предложить другой день."
    return reply, (r["by"], note) if first else None


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря")


def _ru_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {_MONTHS[d.month - 1]}"


@router.post("/invite")
def invite(x_init_data: str | None = Header(default=None)):
    """Позвать друга: сначала он приходит на карту, потом вы вместе играете."""
    u = _user(x_init_data)
    iid = "i" + secrets.token_hex(4)
    with closing(_db()) as c, c:
        c.execute("INSERT INTO invites(id, owner) VALUES(?, ?)", (iid, u.id))
        _seen(c, u)
    return {"id": iid, "link": link(iid)}


@router.post("/invite/{iid}/accept")
async def accept(iid: str, x_init_data: str | None = Header(default=None)):
    """Друг открыл приглашение: между островами канатный мост, позвавшему — сообщение.
    ponytail: одно приглашение могут принять многие (ссылку переслали в чат) — это и хорошо."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT owner FROM invites WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, "Приглашение не найдено")
        if r["owner"] == u.id:
            return {"friend": None}
        a, b = sorted((u.id, r["owner"]))
        with c:
            new = c.execute("INSERT OR IGNORE INTO friends(a, b) VALUES(?, ?)", (a, b)).rowcount
        _seen(c, u)
        n = c.execute("SELECT name FROM users WHERE tg_id=?", (r["owner"],)).fetchone()
        gr, ga = _gn(c, r["owner"]), _gn(c, u.id)
    if new:
        _bg(_notify(r["owner"], f"{u.first_name} {gw(ga, 'пришёл на твою карту', 'пришла на твою карту', 'теперь на твоей карте')} в «Ну-ну». Между вами канатный мост — "
                                "начните партию 🏝", f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}", pic="friend"))
    return {"friend": {"id": r["owner"], "name": n["name"] if n else "Друг", "gn": gr}}


class MeetIn(BaseModel):
    other: int


@router.post("/friend/{fid}/meet")
async def meet(fid: int, body: MeetIn, x_init_data: str | None = Header(default=None)):
    """Позвать двух своих друзей друг к другу. У двоих с общим другом больше шансов сойтись (Granovetter 1973).
    Ответ всегда одинаковый: позвавший не узнаёт, были ли они уже знакомы, — чужие связи не раскрываем.
    ponytail: одну пару один человек зовёт один раз (UNIQUE) — защита от спама; повтор молча ничего не шлёт."""
    u = _user(x_init_data)
    a, b = sorted((fid, body.other))
    with closing(_db()) as c:
        if a == b or u.id in (a, b) or not (_friends(c, u.id, a) and _friends(c, u.id, b)):
            raise HTTPException(404, "Друг не найден")
        mid, new = "m" + secrets.token_hex(4), 0
        if not _friends(c, a, b):
            with c:
                new = c.execute("INSERT OR IGNORE INTO meets(id, by, a, b) VALUES(?,?,?,?)", (mid, u.id, a, b)).rowcount
        names = {r["tg_id"]: r["name"] for r in c.execute("SELECT tg_id, name FROM users WHERE tg_id IN (?, ?)", (a, b))}
        _seen(c, u)
    if new:
        for me, other in ((a, b), (b, a)):
            n = names.get(other, "Друг")
            _bg(_notify(me, f"{u.first_name} зовёт вас друг к другу в «Ну-ну»: ты и {n}. Открой приглашение — "
                            f"когда {n} тоже откроет, между вашими островами появится канатный мост 🏝", link(mid), pic="meet"))
    return {"ok": True}


@router.post("/meet/{mid}/accept")
async def meet_accept(mid: str, x_init_data: str | None = Header(default=None)):
    """Мост между двумя друзьями общего друга — только когда приглашение открыли оба."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT a, b, ok_a, ok_b FROM meets WHERE id=?", (mid,)).fetchone()
        if not r or u.id not in (r["a"], r["b"]):
            raise HTTPException(404, "Приглашение не найдено")
        col, other = ("ok_a", r["b"]) if u.id == r["a"] else ("ok_b", r["a"])
        both = bool(r["ok_b"] if u.id == r["a"] else r["ok_a"])
        with c:
            c.execute(f"UPDATE meets SET {col}=1 WHERE id=?", (mid,))
            new = c.execute("INSERT OR IGNORE INTO friends(a, b) VALUES(?, ?)", (r["a"], r["b"])).rowcount if both else 0
        _seen(c, u)
        n = c.execute("SELECT name FROM users WHERE tg_id=?", (other,)).fetchone()
        go, ga = _gn(c, other), _gn(c, u.id)
    if new:
        _bg(_notify(other, f"{u.first_name} {gw(ga, 'тоже открыл', 'тоже открыла', 'тоже открывает')} приглашение. Между вашими островами канатный мост — "
                           "начните партию 🏝", link(), pic="friend"))
    return {"friend": {"id": other, "name": n["name"] if n else "Друг", "gn": go}, "bridge": both}


def _friends(c: sqlite3.Connection, a: int, b: int) -> bool:
    a, b = sorted((a, b))
    return c.execute("SELECT 1 FROM friends WHERE a=? AND b=?", (a, b)).fetchone() is not None


def _wish(c: sqlite3.Connection, me: int, other: int) -> dict:
    """Взаимная бутылка: мост, только если оба захотели."""
    q = "SELECT 1 FROM wishes WHERE a=? AND b=?"
    return {"me": bool(c.execute(q, (me, other)).fetchone()), "them": bool(c.execute(q, (other, me)).fetchone()),
            "friends": _friends(c, me, other)}


@router.get("/bottle")
def catch(x_init_data: str | None = Header(default=None)):
    """Выловить бутылку: чужая, не друга, ещё не выловленная мной, без жалоб; реже ловленные — раньше."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        _seen(c, u)
        r = c.execute("""SELECT g.id FROM games g
            WHERE g.bottle = 1 AND g.reported IS NULL AND g.owner != :me
              AND NOT EXISTS (SELECT 1 FROM plays p WHERE p.game_id = g.id AND p.player = :me)
              AND NOT EXISTS (SELECT 1 FROM friends f WHERE (f.a = :me AND f.b = g.owner) OR (f.b = :me AND f.a = g.owner))
              AND (SELECT COUNT(*) FROM plays p WHERE p.game_id = g.id) < :cap
            ORDER BY (SELECT COUNT(*) FROM plays p WHERE p.game_id = g.id), RANDOM() LIMIT 1""",
                      {"me": u.id, "cap": BOTTLE_CATCHES}).fetchone()
    return {"gid": r["id"] if r else None}


class WishIn(BaseModel):
    other: int


@router.post("/bottle/{gid}/wish")
async def wish(gid: str, body: WishIn, x_init_data: str | None = Header(default=None)):
    """«Построить мост» с незнакомцем из бутылки. Оба захотели — острова на карте друг у друга."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT owner, bottle FROM games WHERE id=?", (gid,)).fetchone()
        player = body.other if r and r["owner"] == u.id else u.id
        ok = r and r["bottle"] and body.other != u.id and r["owner"] in (u.id, body.other) and c.execute(
            "SELECT 1 FROM plays WHERE game_id=? AND player=?", (gid, player)).fetchone()
        if not ok:
            raise HTTPException(404, "Бутылка не найдена")
        with c:
            c.execute("INSERT OR IGNORE INTO wishes(a, b) VALUES(?, ?)", (u.id, body.other))
            w = _wish(c, u.id, body.other)
            if w["them"] and not w["friends"]:
                a, b = sorted((u.id, body.other))
                c.execute("INSERT OR IGNORE INTO friends(a, b) VALUES(?, ?)", (a, b))
                w["friends"] = True
        _seen(c, u)
    if w["friends"]:
        _bg(_notify(body.other, f"Взаимная бутылка 🍾 {u.first_name} тоже захотел мост. Ваши острова теперь рядом",
                    f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}", pic="bottle"))
    else:
        _bg(_notify(body.other, f"{u.first_name} хочет построить с тобой мост 🍾 Захочешь тоже — острова окажутся рядом",
                    link(gid), pic="bottle"))
    return w


@router.post("/bottle/{gid}/report")
async def report(gid: str, x_init_data: str | None = Header(default=None)):
    """Жалоба: бутылка сразу перестаёт ловиться, админ-чату — сообщение."""
    u = _user(x_init_data)
    with closing(_db()) as c, c:
        r = c.execute("SELECT owner, name, story FROM games WHERE id=? AND bottle=1", (gid,)).fetchone()
        if not r:
            raise HTTPException(404, "Бутылка не найдена")
        c.execute("UPDATE games SET reported=1 WHERE id=?", (gid,))
        if r["owner"] != u.id:  # считается и в доле жалоб на автора (nunu_reports)
            c.execute("INSERT OR IGNORE INTO reports(by, uid, kind, ref, why, text) VALUES(?, ?, 'bottle', ?, '', '')", (u.id, r["owner"], gid))
    from . import nunu_reports
    with closing(_db()) as c:
        old, new, pct = nunu_reports._judge(c, r["owner"])
    if new > old:
        _bg(nunu_reports._apply(r["owner"], old, new, pct))
    if config.ADMIN_CHAT_ID:
        story = json.loads(r["story"])["q"] if r["story"] else "—"
        _bg(_notify(int(config.ADMIN_CHAT_ID), f"Жалоба на бутылку {gid} от {u.id}. Автор: {r['name']} ({r['owner']}).\n"
                                               f"Ситуация: {story}", link(gid), pic=None))
    return {"ok": True}


def _island(c: sqlite3.Connection, uid: int) -> dict:
    """Сад, лес, скалы: сколько семян в каждой зоне и сколько из них взошло в жизни («так и сделал»)."""
    r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
    d = json.loads(r["data"]) if r else {}
    z, b = [0, 0, 0], [0, 0, 0]
    for s in d.get("nunu-seeds") or []:
        if isinstance(s, dict) and s.get("z") in (0, 1, 2):
            z[s["z"]] += 1
            if isinstance(s.get("life"), dict) and s["life"].get("same"):
                b[s["z"]] += 1
    out = {"island": z, "bloom": b, "gn": g if (g := (d.get("nunu-prefs") or {}).get("gn")) in ("m", "f", "n") else ""}
    if isinstance(d.get("nunu-prefs"), dict) and d["nunu-prefs"].get("moodShare"):
        out |= _mood(d)  # цвет вокруг острова — только если сам разрешил друзьям
    return out


def _mood(d: dict) -> dict:
    """Отметки «Как ты сейчас?» за 3 дня (состояние, возраст в часах) и дела: распустилось за 14 дней, посажено за 7.
    Цвет считает страница (та же формула, что у своего острова)."""
    now, mood = datetime.now(timezone.utc), []
    for m in d.get("nunu-mood") or []:
        try:
            age = (now - datetime.fromisoformat(str(m["t"]).replace("Z", "+00:00"))).total_seconds() / 3600
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= age < 72 and m.get("q") in (0, 1, 2, 3):
            mood.append([m["q"], round(age, 1)])
    d14, d7 = (str(date.today() - timedelta(days=n)) for n in (14, 7))
    seeds = [s for s in d.get("nunu-seeds") or [] if isinstance(s, dict)]
    act = [sum(1 for s in seeds if isinstance(s.get("life"), dict) and s["life"].get("same") and str(s["life"].get("d", "")) >= d14),
           sum(1 for s in seeds if str(s.get("d", "")) >= d7)]
    return {"mood": mood, "act": act}


# ---------- «Мой участок»: уклон по открытым данным рельефа ----------
# Open Topo Data (api.opentopodata.org): EU-DEM 25 м, иначе SRTM 30 м; публичный API — до 100 точек, 1 запрос в секунду,
# 1000 в день, CORS не обещан, поэтому ходит сервер. Модель рельефа усредняет высоту по ячейке 25–30 м: это общий
# наклон местности вокруг участка, а не микрорельеф. ponytail: кэш в памяти, до перезапуска; хватит, пока игроков мало.
_slope_cache: dict = {}
_LL = re.compile(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")
_GMAPS = re.compile(r"^https://(maps\.app\.goo\.gl|goo\.gl/maps|(www\.)?google\.[a-z.]{2,6}/maps)/")
DEM = (("eudem25m", "EU-DEM 25 м"), ("srtm30m", "SRTM 30 м"))


def _get(url: str, timeout: float = 8) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "joydao-nunu/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(400_000)


def _coords(q: str):
    """Координаты из «48.43, 22.71» или из ссылки Google Карт (короткую раскрываем на сервере)."""
    m = _LL.search(q)
    if m:
        return float(m[1]), float(m[2])
    if not _GMAPS.match(q):
        return None
    final, body = _get(q)
    for s in (urllib.parse.unquote(final), urllib.parse.unquote(body.decode("utf-8", "ignore"))):
        m = (re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", s) or re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", s)
             or re.search(r"[?&](?:q|ll|query|center)=(-?\d+\.\d+),\+?(-?\d+\.\d+)", s))
        if m:
            return float(m[1]), float(m[2])
    return None


def slope_fit(pts: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Плоскость z = a·восток + b·север + c методом наименьших квадратов → (уклон %, куда понижение °от севера, разброс м)."""
    n = len(pts)
    me, mn, mz = (sum(p[i] for p in pts) / n for i in range(3))
    ee = sum((p[0] - me) ** 2 for p in pts)
    nn = sum((p[1] - mn) ** 2 for p in pts)
    en = sum((p[0] - me) * (p[1] - mn) for p in pts)
    ez = sum((p[0] - me) * (p[2] - mz) for p in pts)
    nz = sum((p[1] - mn) * (p[2] - mz) for p in pts)
    d = ee * nn - en * en
    a, b = (ez * nn - nz * en) / d, (nz * ee - ez * en) / d
    rms = math.sqrt(sum((p[2] - (mz + a * (p[0] - me) + b * (p[1] - mn))) ** 2 for p in pts) / n)
    return math.hypot(a, b) * 100, (math.degrees(math.atan2(-a, -b)) + 360) % 360, rms


class SlopeIn(BaseModel):
    q: str


@router.post("/slope")
async def slope(body: SlopeIn, x_init_data: str | None = Header(default=None)):
    _user(x_init_data)
    try:
        ll = await asyncio.to_thread(_coords, body.q.strip()[:600])
    except Exception:
        ll = None
    if not ll or not (-60 <= ll[0] <= 60 and -180 <= ll[1] <= 180):
        raise HTTPException(422, "Не нашёл координаты. Нажми «Я на участке» или вставь ссылку на точку в Google Картах.")
    key = (round(ll[0], 4), round(ll[1], 4))
    if key in _slope_cache:
        return _slope_cache[key]
    grid = [(e, n) for n in (-60, -30, 0, 30, 60) for e in (-60, -30, 0, 30, 60)]  # 5×5 точек, шаг 30 м ≈ ячейка модели
    k = 111_320
    locs = "|".join(f"{ll[0] + n / k:.6f},{ll[1] + e / (k * math.cos(math.radians(ll[0]))):.6f}" for e, n in grid)
    zs, src = None, None
    for i, (ds, name) in enumerate(DEM):
        if i:
            await asyncio.sleep(1.1)  # публичный API: не чаще раза в секунду
        try:
            _, raw = await asyncio.to_thread(_get, f"https://api.opentopodata.org/v1/{ds}?locations={locs}&interpolation=bilinear")
            res = [r.get("elevation") for r in json.loads(raw).get("results", [])]
        except Exception:
            continue
        if len(res) == len(grid) and all(z is not None for z in res):
            zs, src = res, name
            break
    if not zs:
        raise HTTPException(502, "Сервис рельефа сейчас не отвечает. Попробуй позже.")
    pct, az, rms = slope_fit([(e, n, z) for (e, n), z in zip(grid, zs)])
    out = {"lat": round(ll[0], 6), "lon": round(ll[1], 6), "z": round(zs[12], 1), "pct": round(pct, 1), "az": round(az), "rms": round(rms, 1), "src": src}
    _slope_cache[key] = out
    return out


# ---------- «Мой участок»: спутниковая подложка ----------
# Esri World Imagery: публичные плитки, подпись «Esri, Maxar» на карте обязательна. Если в .env есть ESRI_KEY (ArcGIS Location
# Platform), он добавляется к запросу. Плитки кэшируются на диске — снимок участка меняется раз в годы.
# ponytail: эндпойнт без авторизации (картинки грузит <image>, заголовок не передать); ограничение — только крупные масштабы 16–20.
TILES = DB.parent / "tiles"
ESRI_TILE = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"


# Где детального снимка нет, Esri отдаёт 200 с заглушкой «Map data not yet available» — одинаковой картинкой для любых
# плиток этого масштаба. Узнаём её по отпечатку: совпала с плиткой посреди Тихого океана или уже встречалась на другом месте →
# отвечаем 404, а клиент берёт плитку на масштаб крупнее и растягивает. ponytail: эвристика; однотонная вода тоже совпадёт — и это правильно.
_NOTILE: set = set()
_SEEN: dict = {}


async def _fetch_tile(z: int, x: int, y: int) -> bytes:
    f = TILES / str(z) / str(x) / f"{y}"
    if f.exists():
        return f.read_bytes()
    key = os.environ.get("ESRI_KEY", "")
    url = ESRI_TILE.format(z=z, x=x, y=y) + (f"?token={urllib.parse.quote(key)}" if key else "")
    try:
        _, raw = await asyncio.to_thread(_get, url, 10)
    except Exception:
        raise HTTPException(502, "Снимок сейчас недоступен")
    if not raw.startswith((b"\xff\xd8", b"\x89PNG")):
        raise HTTPException(502, "Снимок сейчас недоступен")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(raw)
    return raw


async def _is_blank(z: int, x: int, y: int, raw: bytes) -> bool:
    h = hashlib.md5(raw).hexdigest()
    if (z, "ocean") not in _SEEN:
        _SEEN[(z, "ocean")] = 1
        try:
            _NOTILE.add(hashlib.md5(await _fetch_tile(z, int(2 ** z * 40 / 360), 2 ** (z - 1))).hexdigest())
        except HTTPException:
            pass
    first = _SEEN.setdefault(h, (z, x, y))
    if first != (z, x, y):
        _NOTILE.add(h)
    return h in _NOTILE


@router.get("/tile/{z}/{x}/{y}")
async def tile(z: int, x: int, y: int):
    from fastapi.responses import Response
    if not (16 <= z <= 20 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404)
    raw = await _fetch_tile(z, x, y)
    if await _is_blank(z, x, y, raw):
        raise HTTPException(404, "Здесь нет снимка такой детальности")
    return Response(raw, media_type="image/png" if raw.startswith(b"\x89PNG") else "image/jpeg",
                    headers={"Cache-Control": "public, max-age=2592000"})


@router.get("/world")
def world(x_init_data: str | None = Header(default=None)):
    """Карта: мой остров и острова тех, с кем есть хоть одна партия или приглашение.
    ponytail: всё считается на лету из games/plays; кэш, когда друзей станет сотни."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        _seen(c, u)
        rows = c.execute("""SELECT g.id, g.owner, g.to_id, g.created_at, p.player, p.boards, p.created_at AS pt,
            p.guess, p.mark
            FROM games g LEFT JOIN plays p ON p.game_id = g.id
            WHERE (g.owner = :me OR g.to_id = :me OR p.player = :me) AND g.bottle IS NULL""", {"me": u.id}).fetchall()
        fr: dict[int, dict] = {}
        for r in rows:
            other = r["player"] if r["owner"] == u.id else r["owner"]
            if r["player"] is None:  # партия ещё без ответа
                other = r["to_id"] if r["owner"] == u.id else r["owner"]
            if not other or other == u.id or (r["player"] not in (None, u.id) and r["owner"] != u.id):
                continue
            f = fr.setdefault(other, {"id": other, "boards": 0.0, "turn": None, "gid": None, "last": None, "mark": None,
                                      "t": ""})
            if r["player"] is None:
                f["turn"], f["gid"] = ("them" if r["owner"] == u.id else "me"), r["id"]
            else:
                f["boards"] += (r["boards"] or 0) + STORY_BOARDS.get(r["mark"], 0)
                if r["owner"] == u.id and r["guess"] and r["mark"] is None:
                    f["mark"] = r["id"]  # друг угадывал мою ситуацию — жду отметки
                if (r["pt"] or "") >= f["t"]:
                    f["last"], f["t"] = r["id"], r["pt"] or ""
        for r in c.execute("SELECT a, b FROM friends WHERE a=:me OR b=:me", {"me": u.id}):
            other = r["b"] if r["a"] == u.id else r["a"]
            fr.setdefault(other, {"id": other, "boards": 0.0, "turn": None, "gid": None, "last": None, "mark": None,
                                  "t": ""})
        for f in fr.values():
            a, b = sorted((u.id, f["id"]))
            d = c.execute("SELECT n FROM deeds WHERE a=? AND b=?", (a, b)).fetchone()
            f["boards"] += 3 * (d["n"] if d else 0)
            n = c.execute("SELECT name FROM users WHERE tg_id=?", (f["id"],)).fetchone()
            f["name"] = n["name"] if n else "Друг"
            f |= _island(c, f["id"])
            del f["t"]
        # в тумане: сколько ещё игроков в море — только число, без имён и id
        fog = c.execute("SELECT COUNT(*) FROM users WHERE tg_id != ?", (u.id,)).fetchone()[0] - len(fr)
        return {"me": {"id": u.id, "name": u.first_name, **_island(c, u.id)},
                "friends": sorted(fr.values(), key=lambda f: -f["boards"]), "fog": max(0, fog)}


class PicIn(BaseModel):
    data: str  # JPEG в base64 (телефон уже сжал до ~1000 px)


@router.put("/seedpic/{pid}")
def seedpic_put(pid: str, body: PicIn, x_init_data: str | None = Header(default=None)):
    """Своё фото к делу: бот пришлёт его в напоминании вместо картинки по умолчанию.
    Видят только хозяин и друг в общем деле; удаляется через месяц после «Так и сделал» (pic_gc)."""
    u = _user(x_init_data)
    if not PID_RE.fullmatch(pid):
        raise HTTPException(422, "Неверное семя")
    with closing(_db()) as c:
        _muted(c, u.id)
    try:
        raw = base64.b64decode(body.data.split(",")[-1], validate=True)
    except ValueError:
        raise HTTPException(422, "Не картинка")
    if not raw.startswith(b"\xff\xd8") or len(raw) > 800_000:
        raise HTTPException(422, "Нужна фотография JPEG до 800 КБ")
    d = _media(u.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.jpg").write_bytes(raw)
    return {"ok": True}


@router.delete("/seedpic/{pid}")
def seedpic_del(pid: str, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    f = seed_pic(u.id, pid)
    if f:
        f.unlink()
    return {"ok": True}


def pic_gc(days: int = 30) -> int:
    """Убрать фото, чьё дело уже отмечено (или удалено), если фото старше месяца. Возвращает, сколько удалено."""
    root, n, old = Path(DB).parent / "media", 0, datetime.now().timestamp() - days * 86400
    if not root.exists():
        return 0
    with closing(_db()) as c:
        for d in root.iterdir():
            if not d.name.isdigit():
                continue
            r = c.execute("SELECT data FROM state WHERE tg_id=?", (int(d.name),)).fetchone()
            try:
                wait = {str(x.get("pid")) for x in json.loads(r["data"]).get("nunu-seeds") or [] if isinstance(x, dict) and not x.get("life")} if r else set()
            except ValueError:
                wait = set()
            for f in d.glob("*.jpg"):
                if f.stem not in wait and f.stat().st_mtime < old:
                    f.unlink()
                    n += 1
    return n


class ShareIn(BaseModel):
    text: str
    url: str


@router.post("/share")
async def share(body: ShareIn, x_init_data: str | None = Header(default=None)):
    """«Отправить в Telegram» (Bot API 8.0 shareMessage): бот готовит сообщение со ссылкой, Telegram сам
    открывает выбор чата — работает и на компьютере, где t.me/share/url из мини-приложения молчит (25.11).
    Ссылка — только на наш бот (t.me/<бот>/…), чтобы через нас нельзя было рассылать чужие ссылки."""
    u = _user(x_init_data)
    if not body.url.startswith(f"https://t.me/{config.BOT_USERNAME}/") or len(body.url) > 300:
        raise HTTPException(422, "Не та ссылка")
    from aiogram import Bot
    from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                               InputTextMessageContent)
    bot = Bot(token=config.BOT_TOKEN)
    try:
        m = await bot.save_prepared_inline_message(
            user_id=u.id, allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True,
            result=InlineQueryResultArticle(
                id=secrets.token_hex(8), title="«Ну-ну»", description=body.text[:120],
                input_message_content=InputTextMessageContent(message_text=f"{body.text[:900]}\n{body.url}"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть «Ну-ну»", url=body.url)]])))
    except Exception as e:  # старый Telegram у бота/библиотеки — клиент откроет t.me/share как раньше
        log.info("shareMessage: %s", e)
        raise HTTPException(503, "Не получилось подготовить")
    finally:
        await bot.session.close()
    return {"id": m.id}


# ---------- картинки к сообщениям бота (25.11) ----------
# Свои — у каждого вида сообщения (static/img/bot/<вид>.png, холст «Картинки бота»); своё фото игрока — у его дела.
PICS = Path(__file__).resolve().parent.parent / "static" / "img" / "bot"
_pic_ids: dict[str, str] = {}  # картинка уже в Telegram — дальше шлём по file_id, без повторной загрузки
PID_RE = re.compile(r"[A-Za-z0-9_-]{4,16}")


def _media(uid: int) -> Path:
    return Path(DB).parent / "media" / str(int(uid))


def seed_pic(uid: int, pid) -> Path | None:
    """Фото игрока к делу (pid — id семени), если он его добавил."""
    if not pid or not PID_RE.fullmatch(str(pid)):
        return None
    f = _media(uid) / f"{pid}.jpg"
    return f if f.exists() else None


class Rem(tuple):
    """(uid, text) как раньше, плюс .pic — какая картинка к напоминанию."""
    def __new__(cls, uid: int, text: str, pic):
        t = super().__new__(cls, (uid, text))
        t.pic = pic
        return t


async def _notify(tg_id: int, text: str, url: str, html: bool = False, pic: "str | Path | None" = "note",
                  btns: list[tuple[str, str]] | None = None) -> None:
    """Сообщение игроку от бота, с картинкой: pic — вид (имя файла в static/img/bot), путь к фото игрока или None.
    btns — кнопки-ответы (текст, callback_data) строкой над «Открыть».
    Не написал боту /start — Telegram не пустит, это не ошибка игры."""
    from aiogram import Bot
    from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
    bot = Bot(token=config.BOT_TOKEN)
    kb = InlineKeyboardMarkup(inline_keyboard=([[InlineKeyboardButton(text=t, callback_data=d) for t, d in btns]] if btns else [])
                              + [[InlineKeyboardButton(text="Открыть «Ну-ну»", url=url)]])
    f = pic if isinstance(pic, Path) else PICS / f"{pic}.png" if pic else None
    try:
        if f and f.exists() and len(text) <= 1024:  # подпись к фото — до 1024 знаков
            m = await bot.send_photo(tg_id, _pic_ids.get(str(f)) or FSInputFile(f), caption=text,
                                     parse_mode="HTML" if html else None, reply_markup=kb)
            if isinstance(pic, str) and m.photo:
                _pic_ids[str(f)] = m.photo[-1].file_id
        else:
            await bot.send_message(tg_id, text, parse_mode="HTML" if html else None, reply_markup=kb)
    except Exception as e:
        log.info("Не отправил уведомление %s: %s", tg_id, e)
    finally:
        await bot.session.close()


# ---------- напоминания: «сегодня ваше дело», «пора полить», «посади новые» ----------
# Наука: напоминание в заранее выбранный день усиливает план «если — то» (Gollwitzer & Sheeran 2006),
# а частые пуши раздражают и их отключают — поэтому про сад не чаще раза в неделю, и всё выключается в настройках.
GARDEN_EVERY = 7  # ponytail: дней между напоминаниями про сад; подстроить по отпискам


def _when(s: dict) -> datetime | None:
    """Момент напоминания в UTC (без tzinfo) для дела с часом: due + at по времени игрока (tz = getTimezoneOffset)."""
    try:
        return datetime.fromisoformat(f"{s['due']}T{s['at']}") + timedelta(minutes=int(s.get("tz") or 0))
    except Exception:
        return None


def reminders_due(today: date, now: datetime | None = None, daily: bool = True, garden_every: int = GARDEN_EVERY) -> list[tuple[int, str]]:
    """Кому и что напомнить. daily — утренний проход (дела без часа и сад); now — минутный проход для дел с часом.
    Отметки пишутся сразу: одно и то же не придёт дважды."""
    out, iso, week_ago = [], today.isoformat(), (today - timedelta(days=garden_every)).isoformat()
    with closing(_db()) as c, c:
        for r in c.execute("SELECT tg_id, data FROM state").fetchall():
            try:
                d = json.loads(r["data"])
                seeds = [s for s in (d.get("nunu-seeds") or []) if isinstance(s, dict)]
            except Exception:
                continue
            if (d.get("nunu-prefs") or {}).get("remind") is False or not seeds:
                continue
            # в саду — только семена намерений и «Это было со мной»; прочие ответы партий — для портрета острова (как inGarden в nunu.html)
            uid, wait = r["tg_id"], [s for s in seeds if not s.get("life") and any(s.get(k) for k in ("step", "mine", "pid", "loc"))]
            for s in wait:
                w = _when(s) if s.get("due") and s.get("at") else None
                if w:  # дело с часом: в его минуту (и не позже суток — если сервер лежал)
                    if not (now and w <= now < w + timedelta(days=1)):
                        continue
                elif not (daily and s.get("due") == iso):
                    continue
                kind = "due:" + str(s.get("pid") or s.get("r", ""))[:60]
                if c.execute("INSERT OR IGNORE INTO reminders VALUES(?,?,?)", (uid, kind, iso)).rowcount:
                    own = seed_pic(uid, s.get("pid")) or (s.get("by") and seed_pic(s["by"], s.get("pid")))  # общее дело — фото автора
                    out.append(Rem(uid, f"{'Пора: твоё дело ⏰' if w else 'Сегодня твоё дело 📅'}\n\n{s.get('r', '')}\n\n"
                                     f"Когда сделаешь, отметь в саду «{gw((d.get('nunu-prefs') or {}).get('gn') or '', 'Так и сделал', 'Так и сделала', 'Получилось')}».", own or "deed"))
            if not daily:
                continue
            last = c.execute("SELECT at FROM reminders WHERE tg_id=? AND kind='garden'", (uid,)).fetchone()
            if last and last["at"] > week_ago:
                continue
            old = [s for s in wait if str(s.get("d") or iso) <= week_ago]
            if old:
                text = (f"Пора полить семена 🌱 Неделю ждут отметки: {len(old)}. "
                        "Загляни в сад: что из этого случилось в жизни?")
            elif max(str(s.get("d") or iso) for s in seeds) <= week_ago:
                text = "Сад неделю без новых семян 🌱 Загляни в «Удиви себя» — посадим новые."
            else:
                continue
            c.execute("INSERT INTO reminders VALUES(?, 'garden', ?) ON CONFLICT(tg_id, kind) DO UPDATE SET at=excluded.at",
                      (uid, iso))
            out.append(Rem(uid, text, "garden"))
    return out


async def remind_loop() -> None:
    """Раз в минуту: дела с часом — в их минуту; дела без часа и сад — после часа из админки (по Киеву, по умолчанию 8).
    Перезапуск сервиса не дублирует: отметки в базе."""
    # ponytail: каждую минуту читаем всю таблицу state — хватит на тысячи игроков; дальше — индекс по due
    from zoneinfo import ZoneInfo
    from . import admin_more
    while True:
        now = datetime.now(timezone.utc)
        try:
            cfg = admin_more.get("remind")  # время и частота — из админки («Напоминания»), час — по Киеву
            # ponytail: час до 2–3 ночи по Киеву попадает на прошлые сутки UTC — утренние напоминания так рано не нужны
            hour_utc = (cfg["hour"] - int(now.astimezone(ZoneInfo("Europe/Kyiv")).utcoffset().total_seconds() // 3600)) % 24
            for r in (reminders_due(now.date(), now.replace(tzinfo=None), daily=now.hour >= hour_utc,
                                    garden_every=cfg["garden_every"]) if cfg["on"] else []):
                await _notify(r[0], r[1], link("garden"), pic=getattr(r, "pic", "deed"))  # про дела и сад — сразу в сад
            if now.hour == 1 and now.minute == 0:
                await asyncio.to_thread(pic_gc)
                await asyncio.sleep(0.05)
        except Exception as e:
            log.warning("Напоминания: %s", e)
        await asyncio.sleep(60)


# ---------- в гости на участок: только друзья по мосту, только карта и 3D, комментарии к объектам ----------
def _gn(c: sqlite3.Connection, uid: int) -> str:
    """Как человек просит к себе обращаться (Настройки → «Как к тебе обращаться?»): m, f, n или "" — не выбрано."""
    g = (_owner_state(c, uid).get("nunu-prefs") or {}).get("gn")
    return g if g in ("m", "f", "n") else ""


def gw(g: str, m: str, f: str, n: str | None = None) -> str:
    """Слово в роде человека; «без разницы» — нейтральный вариант, если он есть; не выбрано — как раньше (м. р.)."""
    return f if g == "f" else n if g == "n" and n is not None else m


def _owner_state(c: sqlite3.Connection, uid: int) -> dict:
    r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
    return json.loads(r["data"]) if r else {}


def _plot_obj(c: sqlite3.Connection, uid: int, oid: str) -> dict | None:
    objs = ((_owner_state(c, uid).get("nunu-plot") or {}).get("map") or {}).get("objs") or []
    return next((o for o in objs if isinstance(o, dict) and o.get("id") == oid), None)


def _comments(c: sqlite3.Connection, owner: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT pc.id, pc.obj, pc.text, pc.author, pc.created_at AS at, COALESCE(us.name, 'Друг') AS name "
        "FROM plot_comments pc LEFT JOIN users us ON us.tg_id = pc.author WHERE pc.owner=? ORDER BY pc.id", (owner,))]


@router.get("/visit/{fid}")
async def visit(fid: int, x_init_data: str | None = Header(default=None)):
    """Участок друга: форма, стороны и объекты — без анкеты, ресурсов, координат и снимка."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        if fid == u.id or not _related(c, u.id, fid):
            raise HTTPException(404, "Друг не найден")
        d = _owner_state(c, fid)
        p, prefs = d.get("nunu-plot") or {}, d.get("nunu-prefs") or {}
        if prefs.get("visitClosed"):  # гости — по обоюдному желанию: хозяин закрыл участок
            raise HTTPException(403, "Участок закрыт для гостей")
        site = p.get("site") or {}
        plot = {"site": {"n": site.get("n"), "face": site.get("face")}, "sides": p.get("sides") or {},
                "diags": p.get("diags") or {}, "map": {"objs": ((p.get("map") or {}).get("objs") or [])}}
        r = c.execute("SELECT name FROM users WHERE tg_id=?", (fid,)).fetchone()
        last = c.execute("SELECT at FROM plot_visits WHERE owner=? AND guest=?", (fid, u.id)).fetchone()
        fresh = not last or last["at"] < (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=12)).isoformat()
        with c:
            c.execute("INSERT OR REPLACE INTO plot_visits(owner, guest, at) VALUES(?,?,?)", (fid, u.id, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        _seen(c, u)
        out = {"name": r["name"] if r else None, "plot": plot, "comments": _comments(c, fid)}
    if fresh and not prefs.get("visitOff"):  # не чаще раза в 12 ч от одного друга; хозяин может выключить
        _bg(_notify(fid, f"👋 {u.first_name} заглянул к тебе на участок", link("plot"), pic="visit"))
    return out


@router.get("/plot/comments")
def my_comments(x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    with closing(_db()) as c:
        return _comments(c, u.id)


class CommentIn(BaseModel):
    obj: str
    text: str


@router.post("/visit/{fid}/comment")
async def comment(fid: int, body: CommentIn, x_init_data: str | None = Header(default=None)):
    u = _user(x_init_data)
    text = body.text.strip()[:300]
    if not text:
        raise HTTPException(422, "Пустой комментарий")
    with closing(_db()) as c:
        if fid == u.id or not _related(c, u.id, fid):
            raise HTTPException(404, "Друг не найден")
        if (_owner_state(c, fid).get("nunu-prefs") or {}).get("visitClosed"):
            raise HTTPException(403, "Участок закрыт для гостей")
        _muted(c, u.id)
        o = _plot_obj(c, fid, body.obj)
        if not o:
            raise HTTPException(404, "Такого объекта уже нет")
        if c.execute("SELECT COUNT(*) FROM plot_comments WHERE author=? AND created_at >= datetime('now','-1 day')",
                     (u.id,)).fetchone()[0] >= 30:
            raise HTTPException(429, "На сегодня комментариев хватит")
        with c:
            c.execute("INSERT INTO plot_comments(owner, author, obj, text) VALUES(?,?,?,?)", (fid, u.id, body.obj, text))
        _seen(c, u)
        prefs = _owner_state(c, fid).get("nunu-prefs") or {}
        out = _comments(c, fid)
    if not prefs.get("commOff"):
        _bg(_notify(fid, f"💬 {u.first_name} у «{str(o.get('n', ''))[:40]}»: {text}", link("plot"), pic="visit"))
    return out


@router.delete("/comment/{cid}")
def comment_del(cid: int, x_init_data: str | None = Header(default=None)):
    """Удалить может автор или хозяин участка."""
    u = _user(x_init_data)
    with closing(_db()) as c, c:
        r = c.execute("SELECT owner, author FROM plot_comments WHERE id=?", (cid,)).fetchone()
        if not r or u.id not in (r["owner"], r["author"]):
            raise HTTPException(404)
        c.execute("DELETE FROM plot_comments WHERE id=?", (cid,))
    return {"ok": True}
