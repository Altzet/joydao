"""Обмен излишками: «Отдам», «Ищу», «Умею» — вещи и услуги между участниками «Ну-ну».

Вход тот же, что в игре (Telegram initData). Кто видит предложение, решает автор:
мостам (друзья), друзьям друзей или всем. Денег нет: «Дарю», «Меняю на…» или «Часами» (банк времени) —
модель ещё выбирается, поэтому все три варианта пока живут рядом.
Отклик — сообщение автору от бота со ссылкой на откликнувшегося; дальше договариваются в Telegram.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import time
import urllib.parse
from types import SimpleNamespace
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import config, landing, nunu

router = APIRouter()
PAGE = Path(__file__).resolve().parent.parent / "static" / "obmen.html"
KINDS, TYPES, HOWS, CIRCLES = ("give", "seek", "skill"), ("food", "thing", "service"), ("gift", "swap", "hours"), ("bridge", "fof", "all")
MAX_OPEN = 20  # ponytail: открытых предложений на человека; поднять, если упрутся

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL,
    kind TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
    place TEXT NOT NULL DEFAULT '', how TEXT NOT NULL, how_note TEXT NOT NULL DEFAULT '',
    circle TEXT NOT NULL, username TEXT NOT NULL DEFAULT '', closed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS offer_replies (offer INTEGER NOT NULL, by INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (offer, by));
"""


LOGIN_DAYS = 30  # ponytail: вход на сайте через Telegram живёт месяц, потом нажать «Войти» ещё раз


def _who(init_data: str | None, tg_login: str | None):
    """В Telegram — initData, как в игре. На сайте — данные виджета «Войти через Telegram»
    (подпись: HMAC-SHA256 ключом sha256(токена бота), core.telegram.org/widgets/login)."""
    if init_data or not tg_login:
        return nunu._user(init_data)
    try:
        d = {k: str(v) for k, v in json.loads(urllib.parse.unquote(tg_login)).items()}
        h = d.pop("hash")
        check = "\n".join(f"{k}={d[k]}" for k in sorted(d))
        good = hmac.new(hashlib.sha256(config.BOT_TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(h, good) and time.time() - int(d["auth_date"]) < LOGIN_DAYS * 86400
        u = SimpleNamespace(id=int(d["id"]), first_name=d.get("first_name", "")[:30] or "Участник", username=d.get("username"))
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(401, "Войди через Telegram ещё раз")
    with closing(nunu._db()) as c:
        if nunu._level(c, u.id) >= 3:
            raise HTTPException(403, "Доступ к «Ну-ну» закрыт по жалобам участников.")
    return u


def _db():
    c = nunu._db()
    c.executescript(SCHEMA)
    return c


def _circles(c, me: int) -> tuple[set, set]:
    """Мои друзья по мостам и друзья друзей (без меня и без прямых друзей)."""
    pair = lambda r, x: r["b"] if r["a"] == x else r["a"]  # noqa: E731
    fr = {pair(r, me) for r in c.execute("SELECT a, b FROM friends WHERE a=? OR b=?", (me, me))}
    fof = set()
    for f in fr:
        fof |= {pair(r, f) for r in c.execute("SELECT a, b FROM friends WHERE a=? OR b=?", (f, f))}
    return fr, fof - fr - {me}


def _rel(owner: int, me: int, fr: set, fof: set) -> str:
    return "me" if owner == me else "bridge" if owner in fr else "fof" if owner in fof else "all"


def _visible(o, rel: str) -> bool:
    """Автор выбрал круг: bridge — только мосты, fof — ещё их друзья, all — все."""
    return rel == "me" or CIRCLES.index(rel) <= CIRCLES.index(o["circle"])


@router.get("/obmen")
def page():
    """Вне Telegram — вход виджетом Telegram (домен сайта задан боту в @BotFather → /setdomain)."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8").replace("{{PLAY}}", nunu.link("obmen"))
                        .replace("{{BOT}}", config.BOT_USERNAME)
                        .replace("{{HEADER}}", landing.header("/obmen")).replace("{{HEADER_CSS}}", landing.HEADER_CSS)
                        .replace("{{TG_JS}}", landing.tg_js()),
                        headers={"Cache-Control": "no-cache"})


@router.get("/obmen/api/list")
def offers(x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    u = _who(x_init_data, x_tg_login)
    with closing(_db()) as c:
        nunu._seen(c, u)
        fr, fof = _circles(c, u.id)
        rows = c.execute("""SELECT o.*, COALESCE(n.name, 'Участник') AS name,
                EXISTS (SELECT 1 FROM offer_replies r WHERE r.offer = o.id AND r.by = ?) AS replied,
                (SELECT COUNT(*) FROM offer_replies r WHERE r.offer = o.id) AS replies
            FROM offers o LEFT JOIN users n ON n.tg_id = o.owner
            WHERE o.closed = 0 AND o.owner NOT IN (SELECT uid FROM sanctions WHERE level >= 3)
            ORDER BY o.id DESC LIMIT 500""", (u.id,)).fetchall()
        out = []
        for o in rows:
            rel = _rel(o["owner"], u.id, fr, fof)
            if _visible(o, rel):
                out.append({k: o[k] for k in ("id", "kind", "type", "title", "text", "place", "how", "how_note",
                                              "circle", "username", "name", "created_at")}
                           | {"rel": rel, "replied": bool(o["replied"]),
                              "replies": o["replies"] if rel == "me" else None})
        st = c.execute("SELECT data FROM state WHERE tg_id=?", (u.id,)).fetchone()
        prefs = (json.loads(st["data"]) if st else {}).get("nunu-prefs") or {}  # тема из настроек игры, с любого устройства
        return {"me": {"id": u.id, "name": u.first_name, "bridges": len(fr)}, "offers": out,
                "prefs": {k: prefs.get(k) for k in ("theme", "acc", "fs")}}


class OfferIn(BaseModel):
    kind: str
    type: str
    title: str = Field(min_length=2, max_length=80)
    text: str = Field(default="", max_length=500)
    place: str = Field(default="", max_length=80)
    how: str
    how_note: str = Field(default="", max_length=80)
    circle: str


@router.post("/obmen/api/offer")
def create(body: OfferIn, x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    u = _who(x_init_data, x_tg_login)
    if body.kind not in KINDS or body.type not in TYPES or body.how not in HOWS or body.circle not in CIRCLES:
        raise HTTPException(422, "Неверный выбор")
    with closing(_db()) as c:
        nunu._muted(c, u.id)
        nunu._seen(c, u)
        if c.execute("SELECT COUNT(*) FROM offers WHERE owner=? AND closed=0", (u.id,)).fetchone()[0] >= MAX_OPEN:
            raise HTTPException(429, f"Открыто уже {MAX_OPEN} предложений — закрой старые")
        with c:
            cur = c.execute("INSERT INTO offers(owner, kind, type, title, text, place, how, how_note, circle, username) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (u.id, body.kind, body.type, body.title.strip(), body.text.strip(), body.place.strip(),
                             body.how, body.how_note.strip() if body.how != "gift" else "", body.circle,
                             u.username or ""))
        return {"id": cur.lastrowid}


class RepIn(BaseModel):
    why: str = Field(default="", max_length=40)


@router.post("/obmen/api/offer/{oid}/report")
async def report(oid: int, body: RepIn, x_init_data: str | None = Header(default=None),
                 x_tg_login: str | None = Header(default=None)):
    """⚑ на предложение: те же жалобы и ступени, что в игре (app/nunu_reports.py)."""
    from . import nunu_reports
    u = _who(x_init_data, x_tg_login)
    return await nunu_reports.submit(u.id, nunu_reports.RepIn(kind="offer", ref=str(oid), why=body.why))


@router.post("/obmen/api/offer/{oid}/close")
def close(oid: int, x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    u = _who(x_init_data, x_tg_login)
    with closing(_db()) as c, c:
        n = c.execute("UPDATE offers SET closed=1 WHERE id=? AND owner=?", (oid, u.id)).rowcount
    if not n:
        raise HTTPException(404, "Нет такого предложения")
    return {"ok": True}


class ReplyIn(BaseModel):
    text: str = Field(default="", max_length=300)


@router.post("/obmen/api/offer/{oid}/reply")
def reply(oid: int, body: ReplyIn, x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    """Отклик: один на человека и предложение. Автору пишет бот, с упоминанием — ответить можно в один тап."""
    u = _who(x_init_data, x_tg_login)
    with closing(_db()) as c:
        nunu._muted(c, u.id)
        o = c.execute("SELECT * FROM offers WHERE id=? AND closed=0", (oid,)).fetchone()
        fr, fof = _circles(c, u.id)
        if not o or o["owner"] == u.id or not _visible(o, _rel(o["owner"], u.id, fr, fof)):
            raise HTTPException(404, "Нет такого предложения")
        with c:
            if not c.execute("INSERT OR IGNORE INTO offer_replies(offer, by, text) VALUES(?,?,?)",
                             (oid, u.id, body.text.strip())).rowcount:
                return {"ok": True, "again": True}
    who = f'<a href="tg://user?id={u.id}">{html.escape(u.first_name)}</a>' + (f" (@{u.username})" if u.username else "")
    note = f"\n«{html.escape(body.text.strip())}»" if body.text.strip() else ""
    nunu._bg(nunu._notify(o["owner"], f"🌱 {who} откликается на твоё «{html.escape(o['title'])}»{note}\n\n"
                                      "Напиши в ответ — договоритесь напрямую.", nunu.link("obmen"), html=True))
    return {"ok": True}
