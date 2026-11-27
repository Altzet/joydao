"""Обмен излишками: «Отдам», «Ищу», «Умею» — вещи и услуги между участниками «Ну-ну».

Вход тот же, что в игре (Telegram initData). Кто видит предложение, решает автор:
мостам (друзья), друзьям друзей или всем. Денег нет: «Дарю», «Меняю на…» или «Часами» (банк времени) —
модель ещё выбирается, поэтому все три варианта пока живут рядом.
Отклик — сообщение автору от бота со ссылкой на откликнувшегося; дальше договариваются в Telegram.
"""
from __future__ import annotations

import asyncio
import html
import secrets
import json
from datetime import date
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import admin_more, config, landing, nunu

router = APIRouter()
PAGE = Path(__file__).resolve().parent.parent / "static" / "obmen.html"
KINDS, TYPES, HOWS, CIRCLES = ("give", "seek", "skill"), ("food", "thing", "service"), ("gift", "swap", "hours"), ("bridge", "fof", "all")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL,
    kind TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
    place TEXT NOT NULL DEFAULT '', how TEXT NOT NULL, how_note TEXT NOT NULL DEFAULT '',
    circle TEXT NOT NULL, username TEXT NOT NULL DEFAULT '', closed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS offer_replies (offer INTEGER NOT NULL, by INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (offer, by));
"""


def _who(init_data: str | None, tg_login: str | None):
    """В Telegram — initData, на сайте — виджет «Войти через Telegram»: проверка та же, что в игре (nunu._user)."""
    return nunu._user(init_data or (nunu.SITE + tg_login if tg_login else None))


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


def _page_cfg() -> str:
    c = admin_more.get("obmen")
    return json.dumps({k: c[k] for k in ("hows", "types", "h1", "lead")}, ensure_ascii=False).replace("</", "<\\/")


@router.get("/obmen")
def page():
    """Вне Telegram — вход виджетом Telegram (домен сайта задан боту в @BotFather → /setdomain)."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8").replace("{{PLAY}}", nunu.link("obmen"))
                        .replace("{{BOT}}", config.BOT_USERNAME)
                        .replace("{{CFG}}", _page_cfg())
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
async def create(body: OfferIn, x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    u = _who(x_init_data, x_tg_login)
    cfg = admin_more.get("obmen")  # какие виды и способы включены, лимит — из админки
    if body.kind not in KINDS or body.type not in cfg["types"] or body.how not in cfg["hows"] or body.circle not in CIRCLES:
        raise HTTPException(422, "Неверный выбор")
    with closing(_db()) as c:
        nunu._muted(c, u.id)
        nunu._seen(c, u)
        if c.execute("SELECT COUNT(*) FROM offers WHERE owner=? AND closed=0", (u.id,)).fetchone()[0] >= cfg["max_open"]:
            raise HTTPException(429, f"Открыто уже {cfg['max_open']} предложений — закрой старые")
        with c:
            cur = c.execute("INSERT INTO offers(owner, kind, type, title, text, place, how, how_note, circle, username) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (u.id, body.kind, body.type, body.title.strip(), body.text.strip(), body.place.strip(),
                             body.how, body.how_note.strip() if body.how != "gift" else "", body.circle,
                             u.username or ""))
        to = _tell_bridges(c, u, body.kind, body.title.strip())
    if to:
        nunu._bg(_send_all(to))
    return {"id": cur.lastrowid}


async def _send_all(to: list[tuple[int, str]]) -> None:
    """По одному, с паузой: лимит Telegram ~30 сообщений/с (как анонс в pause.py)."""
    for uid, text in to:
        await nunu._notify(uid, text, nunu.link("obmen"), pic="exchange")
        await asyncio.sleep(0.05)


VERB = {"give": "отдаёт", "seek": "ищет", "skill": "предлагает помочь:"}


def _tell_bridges(c, u, kind: str, title: str) -> list[tuple[int, str]]:
    """Новое предложение — друзьям по мостам: обмен начинается с тех, кого знаешь.
    Не чаще раза в день от одного человека одному другу — иначе это спам, а не повод."""
    today, out = date.today().isoformat(), []
    fr, _ = _circles(c, u.id)
    with c:
        for f in sorted(fr):
            if c.execute("INSERT INTO reminders VALUES(?, ?, ?) ON CONFLICT(tg_id, kind) DO UPDATE SET at=excluded.at "
                         "WHERE at < excluded.at", (f, f"ob:{u.id}", today)).rowcount:
                out.append((f, f"🌱 {u.first_name} в «Обмене» {VERB[kind]} «{title}»\n\n"
                               "Вы на одном мосту. Пригодится — откликнись, договоритесь напрямую."))
    return out


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
async def reply(oid: int, body: ReplyIn, x_init_data: str | None = Header(default=None),
           x_tg_login: str | None = Header(default=None)):
    """Отклик: один на человека и предложение. Обмен — это общее дело: у откликнувшегося оно сразу в саду,
    автору приходит приглашение с «Принимаю / Не сейчас» (как в nunu.plan_deed); приняли и отметили — мост +3 доски."""
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
            owner = (c.execute("SELECT name FROM users WHERE tg_id=?", (o["owner"],)).fetchone() or {"name": "Участник"})["name"]
            pid, deed = "o" + secrets.token_hex(5), f"Обмен: «{o['title']}»"
            c.execute("INSERT INTO inbox(tg_id, seed) VALUES(?, ?)",
                      (u.id, json.dumps(nunu.deed_seed(deed, pid, u.id, o["owner"], owner), ensure_ascii=False)))
            c.execute("INSERT INTO deed_calls(pid, by, to_id, text, seed) VALUES(?,?,?,?,?)",
                      (pid, u.id, o["owner"], deed,
                       json.dumps(nunu.deed_seed(deed, pid, u.id, u.id, u.first_name), ensure_ascii=False)))
    who = f'<a href="tg://user?id={u.id}">{html.escape(u.first_name)}</a>' + (f" (@{u.username})" if u.username else "")
    note = f"\n«{html.escape(body.text.strip())}»" if body.text.strip() else ""
    nunu._bg(nunu._notify(o["owner"], f"🌱 {who} откликается на твоё «{html.escape(o['title'])}»{note}\n\n"
                                      "Это общее дело 🤝 Примешь — оно появится в саду, когда сделаете, отметьте — "
                                      "ваш мост получит +3 доски. Договоритесь напрямую: напиши в ответ.",
                          nunu.link("obmen"), html=True, pic="exchange",
                          btns=[("🤝 Принимаю", f"jd:y:{pid}"), ("Не сейчас", f"jd:n:{pid}")]))
    return {"ok": True}
