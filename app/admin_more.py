"""Админка, вторая часть (24.11): обмен, путь игрока, люди, рассылка, напоминания, страницы сайта, копия базы.

Всё за тем же ключом NUNU_ADMIN_KEY. Настройки лежат в admin_kv (JSON по ключу), их читают
obmen.py, pages.py и remind_loop — меняются без правки кода и без перезапуска.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from . import nunu, nunu_admin

admin = APIRouter(prefix="/nunu/admin/api/x")
log = logging.getLogger(__name__)
STARTED = time.time()

# ---------- настройки в admin_kv ----------
DEFAULTS = {
    "obmen": {"hows": ["gift", "swap", "hours"], "types": ["food", "thing", "service"], "max_open": 20,
              "h1": "Обмен излишками",
              "lead": "То, что у тебя в избытке — урожай, вещи, умения, — кому-то рядом как раз нужно. "
                      "Сначала видят друзья по мостам, потом их друзья, потом все."},
    "remind": {"hour": 8, "garden_every": 7, "on": True},  # hour — по Киеву
    "pages": {},  # slug → {title, desc, h1, body}; пусто — текст из кода
    "me": {"uid": 0},  # куда слать пробную рассылку
}


def get(name: str) -> dict:
    """Настройка из админки поверх встроенной; читается при каждом вызове (дёшево: одна строка SQLite)."""
    d = json.loads(json.dumps(DEFAULTS[name]))
    try:
        with closing(nunu_admin._c()) as c:
            r = c.execute("SELECT v FROM admin_kv WHERE k=?", ("x:" + name,)).fetchone()
        if r:
            d.update(json.loads(r[0]))
    except (sqlite3.Error, ValueError):
        pass
    return d


def _put(name: str, d: dict) -> dict:
    with closing(nunu_admin._c()) as c, c:
        c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES(?, ?)", ("x:" + name, json.dumps(d, ensure_ascii=False)))
    return get(name)


def _a(req: Request, key: str | None) -> None:
    nunu_admin._auth(req, key)


# ---------- последние ошибки сервиса: в памяти, без чтения journald ----------
class _Tail(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.q: collections.deque = collections.deque(maxlen=50)

    def emit(self, r):
        self.q.append({"at": time.strftime("%d.%m %H:%M:%S", time.localtime(r.created)), "lvl": r.levelname,
                       "where": r.name, "msg": r.getMessage()[:400]})


TAIL = _Tail()
logging.getLogger().addHandler(TAIL)


def _seeds(d: dict) -> list:
    s = d.get("nunu-seeds")
    return s if isinstance(s, list) else []


def _states(c) -> dict[int, dict]:
    out = {}
    for r in c.execute("SELECT tg_id, data, updated_at FROM state"):
        try:
            d = json.loads(r["data"])
        except ValueError:
            d = {}
        out[r["tg_id"]] = {"d": d if isinstance(d, dict) else {}, "at": r["updated_at"]}
    return out


# ---------- 1. Обмен ----------
@admin.get("/obmen")
def obmen_list(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    from . import obmen
    with closing(obmen._db()) as c:
        rows = [dict(r) for r in c.execute(
            """SELECT o.id, o.owner, o.kind, o.type, o.title, o.text, o.how, o.how_note, o.circle, o.closed, o.created_at,
                      COALESCE(u.name, '?') AS name, (SELECT COUNT(*) FROM offer_replies r WHERE r.offer=o.id) AS replies
               FROM offers o LEFT JOIN users u ON u.tg_id=o.owner ORDER BY o.id DESC LIMIT 300""")]
        replies = [dict(r) for r in c.execute(
            """SELECT r.offer, r.text, r.created_at, COALESCE(u.name, '?') AS name, o.title
               FROM offer_replies r JOIN offers o ON o.id=r.offer LEFT JOIN users u ON u.tg_id=r.by
               ORDER BY r.created_at DESC LIMIT 50""")]
    return {"cfg": get("obmen"), "offers": rows, "replies": replies}


@admin.post("/obmen/{oid}/{act}")
def obmen_act(oid: int, act: str, req: Request, x_admin_key: str | None = Header(default=None)):
    """hide — закрыть чужое предложение (closed=2: «закрыто админом»), open — вернуть."""
    _a(req, x_admin_key)
    if act not in ("hide", "open"):
        raise HTTPException(404)
    from . import obmen
    with closing(obmen._db()) as c, c:
        c.execute("UPDATE offers SET closed=? WHERE id=?", (2 if act == "hide" else 0, oid))
    return {"ok": True}


@admin.put("/obmen/cfg")
async def obmen_cfg(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    b, d = await req.json(), get("obmen")
    hows = [h for h in b.get("hows", []) if h in DEFAULTS["obmen"]["hows"]]
    types = [t for t in b.get("types", []) if t in DEFAULTS["obmen"]["types"]]
    if not hows or not types:
        raise HTTPException(422, "Нужен хотя бы один способ обмена и хотя бы один вид")
    d.update(hows=hows, types=types, max_open=max(1, min(200, int(b.get("max_open") or 20))),
             h1=str(b.get("h1") or DEFAULTS["obmen"]["h1"])[:80], lead=str(b.get("lead") or DEFAULTS["obmen"]["lead"])[:400])
    return _put("obmen", d)


# ---------- 2. Путь игрока ----------
@admin.get("/funnel")
def funnel(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    with closing(nunu_admin._c()) as c:
        st = _states(c)
        one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
        bridged = {r[0] for r in c.execute("SELECT a FROM friends UNION SELECT b FROM friends")}
        played = {r[0] for r in c.execute(
            "SELECT g.owner FROM games g JOIN plays p ON p.game_id=g.id WHERE g.bottle=0 AND p.player<>g.owner "
            "UNION SELECT p.player FROM games g JOIN plays p ON p.game_id=g.id WHERE g.bottle=0 AND p.player<>g.owner")}
        planted = {u for u, s in st.items() if _seeds(s["d"])}
        did = {u for u, s in st.items() if any(isinstance(x, dict) and (x.get("life") or {}).get("same") for x in _seeds(s["d"]))}
        weeks = []
        for w in range(7, -1, -1):  # 8 недель, последняя — текущая
            a, b = f"date('now','-{w * 7 + 6} day')", f"date('now','-{w * 7 - 1} day')"
            rng = f"created_at >= {a} AND created_at < {b}"
            weeks.append({"from": one(f"SELECT {a}"), "bridges": one(f"SELECT COUNT(*) FROM friends WHERE {rng}"),
                          "games": one(f"SELECT COUNT(*) FROM games WHERE bottle=0 AND {rng}"),
                          "plays": one(f"SELECT COUNT(*) FROM plays WHERE {rng}")})
        steps = [("В боте (открывали приложение)", one("SELECT COUNT(*) FROM users")),
                 ("Сохранили игру", len(st)), ("Есть мост с другом", len(bridged)),
                 ("Сыграли партию с другом", len(played)), ("Посадили семя", len(planted)),
                 ("Хоть раз «Так и сделал»", len(did))]
        since = {k: one(f"SELECT datetime('now','-{n} day')") for k, n in (("d1", 1), ("d7", 7), ("d30", 30))}
        active = {k: sum(1 for s in st.values() if (s["at"] or "") >= t) for k, t in since.items()}
    return {"steps": steps, "goal": {"name": "Сыграли партию с другом", "have": len(played), "want": 20},
            "active": active, "weeks": weeks}


# ---------- 3. Люди ----------
@admin.get("/people")
def people(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    with closing(nunu_admin._c()) as c:
        st = _states(c)
        br = collections.Counter()
        for r in c.execute("SELECT a, b FROM friends"):
            br[r[0]] += 1
            br[r[1]] += 1
        games = collections.Counter(r[0] for r in c.execute("SELECT owner FROM games WHERE bottle=0"))
        lv = {r[0]: r[1] for r in c.execute("SELECT uid, level FROM sanctions")}
        offers = collections.Counter(r[0] for r in c.execute("SELECT owner FROM offers")) if c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='offers'").fetchone() else collections.Counter()
        out = []
        for r in c.execute("SELECT tg_id, name FROM users"):
            s = st.get(r[0], {"d": {}, "at": None})
            seeds = _seeds(s["d"])
            out.append({"uid": r[0], "name": r[1], "seen": s["at"], "bridges": br[r[0]], "games": games[r[0]],
                        "seeds": len(seeds), "done": sum(1 for x in seeds if isinstance(x, dict) and (x.get("life") or {}).get("same")),
                        "offers": offers[r[0]], "level": lv.get(r[0], 0)})
    out.sort(key=lambda x: x["seen"] or "", reverse=True)
    return {"people": out, "me": get("me")["uid"]}


@admin.post("/people/{uid}/msg")
async def people_msg(uid: int, req: Request, x_admin_key: str | None = Header(default=None)):
    """Одно сообщение человеку от бота, с кнопкой «Открыть»."""
    _a(req, x_admin_key)
    text = str((await req.json()).get("text") or "").strip()[:2000]
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    try:
        await nunu._notify(uid, text, nunu.link())
    except Exception as e:  # заблокировал бота, ни разу не писал /start
        raise HTTPException(409, f"Не дошло: {e}")
    return {"ok": True}


@admin.put("/me")
async def set_me(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    return _put("me", {"uid": int((await req.json()).get("uid") or 0)})


# ---------- 4. Рассылка ----------
SEGMENTS = {"all": "Все игроки", "quiet7": "Не заходили 7 дней и больше", "active7": "Заходили за последние 7 дней",
            "bridged": "У кого есть мост", "nobridge": "У кого ещё нет моста"}
_run = {"on": False, "sent": 0, "fail": 0, "total": 0}


def _segment(c, seg: str) -> list[int]:
    st = _states(c)
    week = c.execute("SELECT datetime('now','-7 day')").fetchone()[0]
    br = {r[0] for r in c.execute("SELECT a FROM friends UNION SELECT b FROM friends")}
    ids = [r[0] for r in c.execute("SELECT tg_id FROM users")]
    seen = lambda u: (st.get(u) or {}).get("at") or ""  # noqa: E731
    pick = {"all": lambda u: True, "quiet7": lambda u: seen(u) < week, "active7": lambda u: seen(u) >= week,
            "bridged": lambda u: u in br, "nobridge": lambda u: u not in br}[seg]
    blocked = {r[0] for r in c.execute("SELECT uid FROM sanctions WHERE level >= 3")}
    return [u for u in ids if pick(u) and u not in blocked]


@admin.get("/bc")
def bc_info(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    with closing(nunu_admin._c()) as c:
        counts = {k: len(_segment(c, k)) for k in SEGMENTS}
    return {"segments": SEGMENTS, "counts": counts, "run": _run, "me": get("me")["uid"]}


async def _send_all(ids: list[int], text: str) -> None:
    try:
        for uid in ids:
            try:
                await nunu._notify(uid, text, nunu.link())
                _run["sent"] += 1
            except Exception as e:
                log.info("Рассылка %s: %s", uid, e)
                _run["fail"] += 1
            await asyncio.sleep(0.1)  # Telegram: не больше ~30 сообщений в секунду
    finally:
        _run["on"] = False


@admin.post("/bc")
async def bc_send(req: Request, x_admin_key: str | None = Header(default=None)):
    """test=true — только себе (uid из «Люди → это я»); иначе всем из сегмента, в фоне."""
    _a(req, x_admin_key)
    b = await req.json()
    text, seg = str(b.get("text") or "").strip()[:2000], b.get("segment")
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    if b.get("test"):
        me = int(b.get("uid") or get("me")["uid"])
        if not me:
            raise HTTPException(422, "Укажи свой Telegram ID")
        _put("me", {"uid": me})
        try:
            await nunu._notify(me, text, nunu.link())
        except Exception as e:
            raise HTTPException(409, f"Не дошло: {e}")
        return {"ok": True, "count": 1}
    if seg not in SEGMENTS:
        raise HTTPException(422, "Выбери, кому")
    if _run["on"]:
        raise HTTPException(409, "Рассылка уже идёт")
    with closing(nunu_admin._c()) as c:
        ids = _segment(c, seg)
    _run.update(on=True, sent=0, fail=0, total=len(ids))
    nunu._bg(_send_all(ids, text))
    return {"ok": True, "count": len(ids)}


# ---------- 5. Напоминания ----------
@admin.get("/remind")
def remind_get(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    return get("remind")


@admin.put("/remind")
async def remind_put(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    b = await req.json()
    return _put("remind", {"hour": max(0, min(23, int(b.get("hour", 8)))),
                           "garden_every": max(1, min(60, int(b.get("garden_every", 7)))), "on": bool(b.get("on", True))})


# ---------- 6. Страницы сайта ----------
@admin.get("/pages")
def pages_get(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    from . import pages
    over = get("pages")
    return {"pages": {k: {f: (over.get(k) or {}).get(f) or v[f] for f in ("title", "desc", "h1", "body")}
                      for k, v in pages.BASE.items()}, "changed": list(over)}


@admin.put("/pages/{slug}")
async def pages_put(slug: str, req: Request, x_admin_key: str | None = Header(default=None)):
    """Пустое поле — вернуть текст из кода. body — HTML статьи (как в коде)."""
    _a(req, x_admin_key)
    from . import pages
    if slug not in pages.BASE:
        raise HTTPException(404)
    b, over = await req.json(), get("pages")
    over[slug] = {f: str(b.get(f) or "")[:20000 if f == "body" else 300] for f in ("title", "desc", "h1", "body")
                  if str(b.get(f) or "").strip() and str(b.get(f)) != pages.BASE[slug][f]}
    if not over[slug]:
        over.pop(slug)
    _put("pages", over)
    return {"ok": True}


# ---------- 7. Копия базы и здоровье ----------
@admin.get("/sys")
def sys_info(req: Request, x_admin_key: str | None = Header(default=None)):
    _a(req, x_admin_key)
    db = Path(nunu.DB)
    tiles = db.parent / "tiles"
    return {"db_mb": round(db.stat().st_size / 1e6, 2) if db.exists() else 0,
            "tiles_mb": round(sum(f.stat().st_size for f in tiles.rglob("*") if f.is_file()) / 1e6, 1) if tiles.exists() else 0,
            "up_h": round((time.time() - STARTED) / 3600, 1), "errors": list(TAIL.q)[::-1]}


@admin.get("/backup")
def backup(req: Request, x_admin_key: str | None = Header(default=None)):
    """Целая копия nunu.db (SQLite backup — безопасно на живой базе)."""
    _a(req, x_admin_key)
    f = Path(tempfile.mkstemp(suffix=".db")[1])
    with closing(sqlite3.connect(nunu.DB)) as src, closing(sqlite3.connect(f)) as dst:
        src.backup(dst)
    return FileResponse(f, filename=f"nunu-{time.strftime('%Y-%m-%d-%H%M')}.db", media_type="application/octet-stream",
                        background=BackgroundTask(f.unlink))
