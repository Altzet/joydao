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
from datetime import date, datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, owner INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS friends (a INTEGER NOT NULL, b INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS wishes (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS deeds (a INTEGER NOT NULL, b INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS deed_ids (pid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, seed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders (tg_id INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (tg_id, kind));
"""
MIGRATE = ("ALTER TABLE games ADD COLUMN to_id INTEGER",  # партия конкретному другу с карты
           "ALTER TABLE plays ADD COLUMN boards REAL",    # доски моста за эту партию
           "ALTER TABLE games ADD COLUMN story TEXT",     # своя ситуация автора: {q, a, real}
           "ALTER TABLE plays ADD COLUMN guess TEXT",     # догадка друга своими словами
           "ALTER TABLE plays ADD COLUMN mark INTEGER",   # автор отметил: 2 угадал, 1 почти, 0 нет
           "ALTER TABLE games ADD COLUMN bottle INTEGER",  # бутылка в море: угадывают незнакомцы
           "ALTER TABLE games ADD COLUMN reported INTEGER")  # жалоба: бутылка больше не ловится
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


def link(gid: str = "") -> str:
    base = f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}"
    return f"{base}?startapp={gid}" if gid else base


class StateIn(BaseModel):
    data: dict


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




class PlayIn(BaseModel):
    name: str
    s: list[int | list[int]] = []  # в hero друг только угадывает
    g: list[int | list[int]]
    guess: str | None = None  # как, по-твоему, поступил автор своей ситуации


class MarkIn(BaseModel):
    player: int
    mark: int


@router.get("/state")
def get_state(x_init_data: str | None = Header(default=None)):
    """Состояние игрока + входящие семена (общие дела от друзей). Входящие отдаются один раз:
    ponytail: если клиент не успел сохранить сад, семя потеряется — друг всё равно получил сообщение от бота."""
    u = _user(x_init_data)
    with closing(_db()) as c:
        r = c.execute("SELECT data FROM state WHERE tg_id=?", (u.id,)).fetchone()
        inbox = [json.loads(x["seed"]) for x in c.execute("SELECT seed FROM inbox WHERE tg_id=? ORDER BY id", (u.id,))]
        with c:
            c.execute("DELETE FROM inbox WHERE tg_id=?", (u.id,))
        _seen(c, u)
    return {"data": json.loads(r["data"]) if r else None, "name": u.first_name, "inbox": inbox}


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
    if body.mode not in ("duo", "hero") or not 0 <= body.seed < 2 * 36 ** 4 or (body.bottle and body.mode != "hero"):
        raise HTTPException(422, "Неизвестная игра")
    s, g = _answers(body.s), _answers(body.g, empty_ok=body.mode == "hero")
    gid = secrets.token_hex(4)
    name = body.name.strip()[:30] or u.first_name
    story = None
    if body.story and body.story.q.strip() and body.story.a.strip():
        a = body.story.a.strip()[:250]
        o = list(dict.fromkeys(x.strip()[:250] for x in body.story.o if x.strip() and x.strip() != a))[:3]
        story = json.dumps({"q": body.story.q.strip()[:400], "a": a, "real": body.story.real, "o": o,
                            "only": body.story.only}, ensure_ascii=False)
    with closing(_db()) as c, c:
        c.execute("INSERT INTO games(id, owner, name, seed, mode, s, g, to_id, story, bottle) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (gid, u.id, name, body.seed, body.mode, s, g, body.to, story, 1 if body.bottle else None))
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
    plays = c.execute("SELECT player, name, s, g, guess, mark FROM plays WHERE game_id=? ORDER BY created_at",
                      (gid,)).fetchall()
    played = any(p["player"] == uid for p in plays)
    out = {"id": gid, "link": link(gid), "mode": r["mode"], "seed": r["seed"], "name": r["name"], "owner": r["owner"],
           "mine": mine, "played": played, "plays": [], "bottle": bool(r["bottle"])}
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
        out["plays"] = [{"id": p["player"], "name": p["name"], "s": json.loads(p["s"]), "g": json.loads(p["g"]),
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
        r = c.execute("SELECT owner, mode, story, bottle FROM games WHERE id=?", (gid,)).fetchone()
        if not r:
            raise HTTPException(404, "Игра не найдена")
        if r["owner"] == u.id:
            raise HTTPException(409, "Это твоя игра")
        guess = (body.guess or "").strip()[:250] if r["story"] else ""
        st = json.loads(r["story"]) if r["story"] else {}
        auto = 2 if guess and guess == st.get("a") else 0 if guess in (st.get("o") or []) else None
        name = body.name.strip()[:30] or u.first_name
        with c:
            c.execute("INSERT OR REPLACE INTO plays(game_id, player, name, s, g, guess, mark) VALUES(?,?,?,?,?,?,?)",
                      (gid, u.id, name, _answers(body.s, empty_ok=r["mode"] == "hero"), _answers(body.g),
                       guess or None, auto))
        view = _view(c, gid, u.id)
        _seen(c, u)
    text = (f"Твою бутылку выловил {name} и угадывал тебя 🍾 Посмотри, как он тебя понял" if r["bottle"]
            else f"{name} угадывал твою ситуацию и {'угадал' if auto else 'не угадал'} 🌉" if auto is not None
            else f"{name} ответил в «Ну-ну» и угадывал твою ситуацию. Отметь, угадал ли ✍️" if guess
            else f"{name} ответил в «Ну-ну». Посмотри ваш мост 🌉")
    _bg(_notify(r["owner"], text, link(gid)))
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
    if n:
        word = ["не совсем угадал", "почти угадал", "угадал"][body.mark]
        _bg(_notify(body.player, f"{u.first_name} отметил твою догадку: ты {word} 🌉", link(gid)))
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
    pid: str
    text: str
    due: str | None = None  # YYYY-MM-DD


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
        seed = {"c": "Удиви себя", "q": f"Общее дело · вдвоём: я и {u.first_name[:30]}", "r": text, "z": -1,
                "d": date.today().isoformat(), "due": due, "life": None, "mine": False, "step": 1,
                "with": u.first_name[:30], "fid": u.id, "pid": body.pid.strip()[:16]}
        with c:
            c.execute("INSERT INTO inbox(tg_id, seed) VALUES(?, ?)", (fid, json.dumps(seed, ensure_ascii=False)))
        _seen(c, u)
    _bg(_notify(fid, f"{u.first_name} зовёт тебя на общее дело 🤝\n\n{text}\n\nОно уже в твоём саду. "
                     f"Когда сделаете, отметьте «Так и сделал» — ваш мост получит +3 доски", link()))
    return {"ok": True}


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
    if new:
        _bg(_notify(r["owner"], f"{u.first_name} пришёл на твою карту в «Ну-ну». Между вами канатный мост — "
                                "начните партию 🏝", f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}"))
    return {"friend": {"id": r["owner"], "name": n["name"] if n else "Друг"}}


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
                    f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}"))
    else:
        _bg(_notify(body.other, f"{u.first_name} хочет построить с тобой мост 🍾 Захочешь тоже — острова окажутся рядом",
                    link(gid)))
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
    if config.ADMIN_CHAT_ID:
        story = json.loads(r["story"])["q"] if r["story"] else "—"
        _bg(_notify(int(config.ADMIN_CHAT_ID), f"Жалоба на бутылку {gid} от {u.id}. Автор: {r['name']} ({r['owner']}).\n"
                                               f"Ситуация: {story}", link(gid)))
    return {"ok": True}


def _island(c: sqlite3.Connection, uid: int) -> dict:
    """Сад, лес, скалы: сколько семян в каждой зоне и сколько из них распустилось в жизни («так и сделал»)."""
    r = c.execute("SELECT data FROM state WHERE tg_id=?", (uid,)).fetchone()
    z, b = [0, 0, 0], [0, 0, 0]
    for s in (json.loads(r["data"]).get("nunu-seeds") or [] if r else []):
        if isinstance(s, dict) and s.get("z") in (0, 1, 2):
            z[s["z"]] += 1
            if isinstance(s.get("life"), dict) and s["life"].get("same"):
                b[s["z"]] += 1
    return {"island": z, "bloom": b}


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
        return {"me": {"id": u.id, "name": u.first_name, **_island(c, u.id)},
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


# ---------- напоминания: «сегодня ваше дело», «пора полить», «посади новые» ----------
# Наука: напоминание в заранее выбранный день усиливает план «если — то» (Gollwitzer & Sheeran 2006),
# а частые пуши раздражают и их отключают — поэтому про сад не чаще раза в неделю, и всё выключается в настройках.
GARDEN_EVERY = 7  # ponytail: дней между напоминаниями про сад; подстроить по отпискам


def reminders_due(today: date) -> list[tuple[int, str]]:
    """Кому и что напомнить сегодня. Отметки пишутся сразу: одно и то же не придёт дважды."""
    out, iso, week_ago = [], today.isoformat(), (today - timedelta(days=GARDEN_EVERY)).isoformat()
    with closing(_db()) as c, c:
        for r in c.execute("SELECT tg_id, data FROM state").fetchall():
            try:
                d = json.loads(r["data"])
                seeds = [s for s in (d.get("nunu-seeds") or []) if isinstance(s, dict)]
            except Exception:
                continue
            if (d.get("nunu-prefs") or {}).get("remind") is False or not seeds:
                continue
            uid, wait = r["tg_id"], [s for s in seeds if not s.get("life")]
            for s in wait:
                if s.get("due") == iso:
                    kind = "due:" + str(s.get("pid") or s.get("r", ""))[:60]
                    if c.execute("INSERT OR IGNORE INTO reminders VALUES(?,?,?)", (uid, kind, iso)).rowcount:
                        out.append((uid, f"Сегодня твоё дело 📅\n\n{s.get('r', '')}\n\n"
                                         "Когда сделаешь, отметь в саду «Так и сделал»."))
            last = c.execute("SELECT at FROM reminders WHERE tg_id=? AND kind='garden'", (uid,)).fetchone()
            if last and last["at"] > week_ago:
                continue
            old = [s for s in wait if str(s.get("d") or iso) <= week_ago]
            if old:
                text = (f"Пора полить семена 🌱 Неделю ждут отметки: {len(old)}. "
                        "Загляни в сад: что из этого случилось в жизни?")
            elif max(str(s.get("d") or iso) for s in seeds) <= week_ago:
                text = "Сад неделю без новых семян 🌱 Сыграй партию или придумай, чем удивишь себя, — посадим новые."
            else:
                continue
            c.execute("INSERT INTO reminders VALUES(?, 'garden', ?) ON CONFLICT(tg_id, kind) DO UPDATE SET at=excluded.at",
                      (uid, iso))
            out.append((uid, text))
    return out


async def remind_loop(hour_utc: int = 6) -> None:
    """Раз в сутки после hour_utc (6 UTC = 9 утра по Москве). Перезапуск сервиса не дублирует: отметки в базе."""
    while True:
        now = datetime.now(timezone.utc)
        if now.hour >= hour_utc:
            try:
                for uid, text in reminders_due(now.date()):
                    await _notify(uid, text, link())
                    await asyncio.sleep(0.05)
            except Exception as e:
                log.warning("Напоминания: %s", e)
        await asyncio.sleep(3600)
