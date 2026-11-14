"""Комната администратора «Ну-ну»: правка ситуаций, карточек, дел и названий из браузера.

Видна только владельцу: вход по ключу NUNU_ADMIN_KEY из .env (нет ключа — комнаты нет, 404).
Правки копятся в черновике, «Опубликовать» делает их живыми и пишет версию в историю.
Колоды: партия выбирает карточки по номеру, поэтому набор карточек на номер не меняется никогда.
Новый набор (добавили или скрыли карточку) = новая колода и новый диапазон номеров; старые партии
играют своей колодой. Карточки только добавляются и скрываются, номера не сдвигаются.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from . import config, landing, nunu
from .nunu import _db

STATIC = Path(__file__).resolve().parent.parent / "static"
router = APIRouter(prefix="/nunu/admin")
KEYS = ("S", "C0", "C1", "C3", "DEEP", "SW", "SO", "SP")
TEXTS = {"nav.map": "Карта", "nav.garden": "Сад", "nav.week": "Удиви себя", "nav.plot": "Участок",
         "nav.settings": "Настройки", "lv0": "Разминка", "lv1": "Кто я, по-твоему?", "title": "Ну-ну"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_versions (v INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS admin_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""
_fails: dict[str, list[float]] = {}
_cache: dict = {}


def _c() -> sqlite3.Connection:
    c = _db()
    c.executescript(SCHEMA)
    return c


def _key_ok(key: str | None, ip: str) -> None:
    want = getattr(config, "NUNU_ADMIN_KEY", "")
    if len(want) < 16:
        raise HTTPException(404)  # комната выключена, пока ключ не задан
    now = time.time()
    f = [t for t in _fails.get(ip, []) if now - t < 600]
    if len(f) >= 10:
        raise HTTPException(429, "Слишком много попыток, подожди 10 минут")
    if not key or not secrets.compare_digest(key.encode(), want.encode()):
        _fails[ip] = f + [now]
        raise HTTPException(401, "Неверный ключ")
    _fails.pop(ip, None)


def _auth(req: Request, key: str | None) -> None:
    _key_ok(key, req.client.host if req.client else "?")


# ---------- встроенные тексты: читаем прямо из static/nunu.html, один источник ----------
def _lit(s: str, name: str):
    """JS-массив из nunu.html → Python: строки в кавычках, числа, висячие запятые, комментарии."""
    m = re.search(r"(?:\bconst |,)" + name + r"=\[", s)
    k, d, q, out = m.end() - 1, 0, False, []
    while True:
        ch = s[k]
        if q:
            out.append(ch)
            if ch == "\\":
                out.append(s[k + 1])
                k += 1
            elif ch == '"':
                q = False
        elif ch == '"':
            q = True
            out.append(ch)
        elif ch == "/" and s[k + 1] == "*":
            k = s.index("*/", k) + 1
        else:
            if ch == "[":
                d += 1
            elif ch == "]":
                d -= 1
                while out and out[-1] in " \n\t\r":
                    out.pop()
                if out and out[-1] == ",":
                    out.pop()
            out.append(ch)
            if d == 0:
                break
        k += 1
    return json.loads("".join(out))


def base() -> dict:
    p = STATIC / "nunu.html"
    mt = p.stat().st_mtime
    if _cache.get("base_mt") != mt:
        s = p.read_text(encoding="utf-8")
        _cache.update(base={k: _lit(s, k) for k in KEYS} | {"T": dict(TEXTS), "H": {}}, base_mt=mt, html=s)
    return _cache["base"] | {"LP": landing.default()}


def _deck1(b: dict) -> dict:
    """Колода партий до комнаты администратора — ровно как считала nunu.html."""
    return {"C0": list(range(len(b["C0"]))), "C1": list(range(len(b["C1"]))),
            "P2": [k for k in range(len(b["S"])) if k < 50 and k not in b["DEEP"]],
            "P3": [f"s{k}" for k in b["DEEP"]] + [f"c{k}" for k in range(len(b["C3"]))]}


def _deck(d: dict) -> dict:
    H = {k: set(v) for k, v in (d.get("H") or {}).items()}
    hs, deep = H.get("S", set()), d["DEEP"]
    return {"C0": [k for k in range(len(d["C0"])) if k not in H.get("C0", set())],
            "C1": [k for k in range(len(d["C1"])) if k not in H.get("C1", set())],
            "P2": [k for k in range(len(d["S"])) if k not in deep and k not in hs],
            "P3": [f"s{k}" for k in deep if k not in hs] + [f"c{k}" for k in range(len(d["C3"])) if k not in H.get("C3", set())]}


# ---------- проверка черновика: всё, что попадёт в приложение ----------
def _s(x, n=600) -> bool:
    return isinstance(x, str) and 0 < len(x.strip()) <= n


def _check(d: dict, prev: dict) -> list[str]:
    e = []
    for k in KEYS:
        if not isinstance(d.get(k), list):
            return [f"нет раздела {k}"]
        if len(d[k]) < len(prev[k]):
            e.append(f"{k}: карточки нельзя удалять, только скрывать")
    for i, x in enumerate(d["S"]):
        if not (isinstance(x, list) and len(x) == 5 and _s(x[0], 40) and _s(x[1])
                and all(isinstance(p, list) and len(p) == 2 and all(_s(o, 120) for o in p) for p in x[2:])):
            e.append(f"Ситуация №{i + 1}: нужны тема, текст и 6 ответов")
    for i, x in enumerate(d["C0"]):
        if not (isinstance(x, list) and len(x) == 3 and _s(x[0], 200) and isinstance(x[1], list) and len(x[1]) == 2
                and all(_s(o, 60) for o in x[1]) and isinstance(x[2], list) and len(x[2]) == 2 and all(_s(o, 12) for o in x[2])):
            e.append(f"Разминка №{i + 1}: вопрос, 2 ответа и 2 значка")
    for i, x in enumerate(d["C1"]):
        if not (isinstance(x, list) and 3 <= len(x) <= 5 and _s(x[0], 40) and _s(x[1], 300) and isinstance(x[2], list)
                and len(x[2]) == 4 and all(_s(o, 120) for o in x[2]) and (len(x) < 4 or x[3] in (0, 1))
                and (len(x) < 5 or _s(x[4], 300))):
            e.append(f"«Кто я» №{i + 1}: черта, вопрос и 4 образа")
    for i, x in enumerate(d["C3"]):
        if not (isinstance(x, list) and len(x) == 3 and _s(x[0], 40) and _s(x[1], 300) and isinstance(x[2], list)
                and len(x[2]) == 4 and all(_s(o, 120) for o in x[2])):
            e.append(f"Личное №{i + 1}: тема, вопрос и 4 ответа")
    if not all(isinstance(k, int) and 0 <= k < len(d["S"]) for k in d["DEEP"]) or len(set(d["DEEP"])) != len(d["DEEP"]):
        e.append("Список личных ситуаций сломан")
    if not all(_s(x, 120) for x in d["SW"] + d["SO"]):
        e.append("Дела и помехи не могут быть пустыми")
    if not d["SW"] or not d["SO"] or len(d["SP"]) != len(d["SW"]) \
            or not all(isinstance(p, list) and len(p) == len(d["SO"]) and all(_s(x, 300) for x in p) for p in d["SP"]):
        e.append("У каждого дела нужен план «если — то» на каждую помеху")
    e += landing.check(d.get("LP"))
    T = d.get("T") or {}
    if not isinstance(T, dict) or any(k not in TEXTS or not _s(v, 40) for k, v in T.items()):
        e.append("Названия: пустое или слишком длинное")
    AU = d.get("AU") or {}
    if not isinstance(AU, dict) or any(not k.isdigit() or int(k) >= len(d["S"]) or not _s(v, 40) for k, v in AU.items()):
        e.append("Подписи авторов сломаны")
    H = d.get("H") or {}
    if not isinstance(H, dict) or any(k not in ("S", "C0", "C1", "C3") or not isinstance(v, list)
                                      or not all(isinstance(i, int) and 0 <= i < len(d[k]) for i in v) for k, v in H.items()):
        e.append("Список скрытых сломан")
    if not e:
        dk = _deck(d)
        if len(dk["C0"]) < 3 or len(dk["P2"]) < 4 or len(dk["P3"]) < 3 or len(dk["C1"]) < 2 \
                or not any(not (len(d["C1"][k]) > 3 and d["C1"][k][3]) for k in dk["C1"]):
            e.append("Слишком много скрыто: в партии нужно 3 разминки, 2 «Кто я» (одна без подарка), 4 ситуации и 3 личных")
    return e


def _live(c) -> tuple[int, dict]:
    r = c.execute("SELECT v, data FROM admin_versions ORDER BY v DESC LIMIT 1").fetchone()
    if not r:
        return 0, base()
    d = json.loads(r["data"])
    d.setdefault("LP", landing.default())  # версии до лендинга
    return r["v"], d


def _decks(c) -> dict:
    r = c.execute("SELECT v FROM admin_kv WHERE k='decks'").fetchone()
    return json.loads(r["v"]) if r else {}


def _bump_deck(c, d: dict) -> str | None:
    """Другой набор карточек — новая колода, новые партии пойдут по ней."""
    decks = _decks(c)
    last = decks[max(decks, key=int)] if decks else _deck1(base())
    new = _deck(d)
    if new == last:
        return None
    deck = str(max([1, *map(int, decks)]) + 1)
    decks[deck] = new
    c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES('decks', ?)", (json.dumps(decks),))
    return deck


def add_scene(scene: list, by: str, note: str) -> int:
    """Ситуация участника, принятая голосованием, — сразу в игру новой версией. Черновик владельца
    получает её тоже, иначе его следующая публикация «удалила» бы карточку."""
    with closing(_c()) as c, c:
        _, d = _live(c)
        d = json.loads(json.dumps(d))
        d["S"].append(scene)
        idx = len(d["S"]) - 1
        if by:
            d.setdefault("AU", {})[str(idx)] = by
        _bump_deck(c, d)
        c.execute("INSERT INTO admin_versions(data, note) VALUES(?, ?)", (json.dumps(d, ensure_ascii=False), note[:200]))
        r = c.execute("SELECT v FROM admin_kv WHERE k='draft'").fetchone()
        if r:
            dr = json.loads(r["v"])
            if len(dr.get("S", [])) == idx:
                dr["S"].append(scene)
                if by:
                    dr.setdefault("AU", {})[str(idx)] = by
                c.execute("UPDATE admin_kv SET v=? WHERE k='draft'", (json.dumps(dr, ensure_ascii=False),))
    _cache.pop("cfg", None)
    return idx


def cfg() -> dict | None:
    """Что подставить в приложение: живые правки и колоды. None — правок не было."""
    if "cfg" not in _cache:
        with closing(_c()) as c:
            v, d = _live(c)
            _cache["cfg"] = {k: d[k] for k in (*KEYS, "T")} | {"AU": d.get("AU") or {}, "decks": _decks(c), "v": v} if v else None
    return _cache["cfg"]


def page() -> HTMLResponse:
    """nunu.html с живыми правками: данные идут до основного скрипта, без лишнего запроса."""
    base()
    s, c = _cache["html"], cfg()
    if c:
        js = json.dumps(c, ensure_ascii=False).replace("</", "<\\/")
        s = s.replace("<script>\n", f"<script>window.NUNU_CFG={js}</script>\n<script>\n", 1)
        t = (c.get("T") or {}).get("title")
        if t:
            s = s.replace("<title>Ну-ну</title>", f"<title>{t.replace('<', '').replace('&', '')}</title>", 1)
    return HTMLResponse(s)


def landing_page() -> HTMLResponse:
    """Главная сайта: лендинг из опубликованных настроек."""
    if "lp" not in _cache:
        with closing(_c()) as c:
            _cache["lp"] = landing.render(_live(c)[1]["LP"])
    return HTMLResponse(_cache["lp"])


# ---------- страница и API ----------
@router.get("")
def admin_page():
    if len(getattr(config, "NUNU_ADMIN_KEY", "")) < 16:
        raise HTTPException(404)
    return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex",
                                                        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.post("/api/login")
def login(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    return {"ok": True}


@router.get("/api/content")
def content(req: Request, x_admin_key: str | None = Header(default=None)):
    """Всё для редактора: встроенное, живое, черновик, колоды."""
    _auth(req, x_admin_key)
    with closing(_c()) as c:
        v, live = _live(c)
        r = c.execute("SELECT v FROM admin_kv WHERE k='draft'").fetchone()
        return {"base": base(), "live": live, "v": v, "draft": json.loads(r["v"]) if r else None,
                "decks": _decks(c), "texts": TEXTS}


@router.put("/api/draft")
async def save_draft(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    raw = await req.body()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "Слишком большой черновик")
    d = json.loads(raw) if raw.strip() not in (b"", b"null") else None
    with closing(_c()) as c, c:
        if d is None:
            c.execute("DELETE FROM admin_kv WHERE k='draft'")
        else:
            c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES('draft', ?)", (json.dumps(d, ensure_ascii=False),))
    return {"ok": True}


@router.post("/api/publish")
async def publish(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    body = await req.json()
    note = str(body.get("note") or "")[:200]
    with closing(_c()) as c, c:
        r = c.execute("SELECT v FROM admin_kv WHERE k='draft'").fetchone()
        if not r:
            raise HTTPException(409, "Черновик пуст")
        d = json.loads(r["v"])
        _, live = _live(c)
        e = _check(d, live)
        if e:
            raise HTTPException(422, {"errors": e[:20]})
        d = {k: d[k] for k in KEYS} | {"LP": d["LP"], "AU": d.get("AU") or {}, "T": {k: v for k, v in (d.get("T") or {}).items() if v != TEXTS[k]},
                                         "H": {k: sorted(set(v)) for k, v in (d.get("H") or {}).items() if v}}
        deck = _bump_deck(c, d)
        cur = c.execute("INSERT INTO admin_versions(data, note) VALUES(?, ?)", (json.dumps(d, ensure_ascii=False), note))
        c.execute("DELETE FROM admin_kv WHERE k='draft'")
    _cache.pop("cfg", None)
    _cache.pop("lp", None)
    return {"ok": True, "v": cur.lastrowid, "deck": deck}


@router.get("/api/versions")
def versions(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    with closing(_c()) as c:
        return [dict(r) for r in c.execute("SELECT v, note, at FROM admin_versions ORDER BY v DESC LIMIT 200")]


@router.post("/api/restore/{v}")
def restore(v: int, req: Request, x_admin_key: str | None = Header(default=None)):
    """Старая версия → черновик. Карточки, добавленные позже, остаются скрытыми: номера не сдвигаются."""
    _auth(req, x_admin_key)
    with closing(_c()) as c, c:
        r = c.execute("SELECT data FROM admin_versions WHERE v=?", (v,)).fetchone()
        if not r:
            raise HTTPException(404)
        d, (_, live) = json.loads(r["data"]), _live(c)
        d.setdefault("LP", landing.default())
        d["AU"] = (live.get("AU") or {}) | (d.get("AU") or {})
        H = {k: set(x) for k, x in (d.get("H") or {}).items()}
        for k in KEYS:
            extra = live[k][len(d[k]):]
            if extra:
                if k in ("S", "C0", "C1", "C3"):
                    H.setdefault(k, set()).update(range(len(d[k]), len(live[k])))
                d[k] = d[k] + extra
        if len(d["SP"]) < len(d["SW"]):
            d["SP"] += live["SP"][len(d["SP"]):len(d["SW"])]
        d["H"] = {k: sorted(x) for k, x in H.items()}
        c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES('draft', ?)", (json.dumps(d, ensure_ascii=False),))
    return {"ok": True, "draft": d}


@router.get("/api/stats")
def stats(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    with closing(_c()) as c:
        one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
        days = {r[0]: r[1] for r in c.execute(
            "SELECT date(created_at), COUNT(*) FROM games WHERE created_at >= date('now','-29 day') GROUP BY 1")}
        plays = {r[0]: r[1] for r in c.execute(
            "SELECT date(created_at), COUNT(*) FROM plays WHERE created_at >= date('now','-29 day') GROUP BY 1")}
        return {"users": one("SELECT COUNT(*) FROM users"), "players": one("SELECT COUNT(*) FROM state"),
                "active7": one("SELECT COUNT(*) FROM state WHERE updated_at >= datetime('now','-7 day')"),
                "games": one("SELECT COUNT(*) FROM games"), "plays": one("SELECT COUNT(*) FROM plays"),
                "bottles": one("SELECT COUNT(*) FROM games WHERE bottle=1"),
                "reported": one("SELECT COUNT(*) FROM games WHERE reported=1"),
                "friends": one("SELECT COUNT(*) FROM friends"), "games7": one(
                    "SELECT COUNT(*) FROM games WHERE created_at >= datetime('now','-7 day')"),
                "days": days, "playdays": plays}


@router.post("/api/preview")
async def preview(req: Request, x_admin_key: str | None = Header(default=None)):
    """Превью лендинга из черновика — в админке, до публикации."""
    _auth(req, x_admin_key)
    lp = await req.json()
    e = landing.check(lp)
    if e:
        return HTMLResponse("<body style='font:16px system-ui;padding:24px;color:#B5452F'>" +
                            "<br>".join(landing.E(x) for x in e) + "</body>")
    return HTMLResponse(landing.render(lp))


IMG = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp", b"GIF8": "gif"}


def _up() -> Path:
    p = nunu.DB.parent / "uploads"
    p.mkdir(exist_ok=True)
    return p


@router.post("/api/upload")
async def upload(req: Request, x_admin_key: str | None = Header(default=None)):
    """Картинка для лендинга: только PNG/JPG/WebP/GIF до 3 МБ, имя — по содержимому."""
    _auth(req, x_admin_key)
    raw = await req.body()
    if len(raw) > 3_000_000:
        raise HTTPException(413, "Картинка больше 3 МБ")
    ext = next((x for m, x in IMG.items() if raw.startswith(m) and (x != "webp" or raw[8:12] == b"WEBP")), None)
    if not ext:
        raise HTTPException(415, "Нужна картинка PNG, JPG, WebP или GIF")
    name = f"{hashlib.sha256(raw).hexdigest()[:20]}.{ext}"
    (_up() / name).write_bytes(raw)
    return {"url": f"/nunu/admin/up/{name}"}


@router.get("/up/{name}")
def uploaded(name: str):
    if not re.fullmatch(r"[0-9a-f]{20}\.(png|jpg|webp|gif)", name) or not (_up() / name).exists():
        raise HTTPException(404)
    return FileResponse(_up() / name, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------- ситуации участников ----------
@router.get("/api/subs")
def subs_list(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    from . import nunu_subs as S
    with closing(S._c()) as c:
        rows = c.execute("SELECT * FROM subs ORDER BY created_at DESC, rowid DESC LIMIT 300").fetchall()
    return {"cfg": S.cfg_get(), "rule": S.rule_text(S.cfg_get()), "ai": bool(config.ANTHROPIC_API_KEY),
            "channel": config.CHANNEL_ID, "subs": [S._pub(r) | {"name": r["name"], "at": r["created_at"]} for r in rows]}


@router.put("/api/subs/cfg")
async def subs_cfg(req: Request, x_admin_key: str | None = Header(default=None)):
    _auth(req, x_admin_key)
    from . import nunu_subs as S
    try:
        return S.cfg_set(await req.json())
    except (TypeError, ValueError):
        raise HTTPException(422, "Числа заданы неверно")


@router.post("/api/subs/{sid}/{act}")
async def subs_act(sid: str, act: str, req: Request, x_admin_key: str | None = Header(default=None)):
    """Снять с голосования; для ждущих (ИИ не ответил) — опубликовать или отклонить."""
    _auth(req, x_admin_key)
    from . import nunu_subs as S
    why = str((await req.json() if (await req.body()) else {}).get("why") or "")[:300]
    if act == "remove":
        await S.remove(sid, why)
    elif act in ("publish", "reject"):
        with closing(S._c()) as c:
            r = c.execute("SELECT status FROM subs WHERE id=?", (sid,)).fetchone()
        if not r or r["status"] != "wait":
            raise HTTPException(409, "Решать можно только ждущие")
        if act == "publish":
            if not await S.publish(sid):
                raise HTTPException(502, "Не удалось опубликовать — проверь, что бот админ канала")
        else:
            await S.remove(sid, why or "Не подошла под правила")
    else:
        raise HTTPException(404)
    return {"ok": True}
