"""Жалобы участников: кнопка «⚑ Пожаловаться» там, где правила могут быть нарушены.

Ступени — по доле жалоб на человека: сколько разных людей пожаловалось / сколько людей с ним связано
(друзья + те, кто жаловался со стороны), но не меньше «аудитории» из настроек — один голос не решает.
1 — убрать нарушения, 2 — + нельзя предлагать ситуации, комментировать и звать на общие дела, 3 — + доступ закрыт.
Ступень сама только растёт; снимает её автор в админке. Пороги — в админке («Жалобы»).
"""
import json
import logging
from types import SimpleNamespace
from contextlib import closing

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from . import config, nunu, nunu_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/nunu/api/report")
admin = APIRouter(prefix="/nunu/admin/api/reports")
DEF = {"min": 2, "aud": 5, "p1": 20, "p2": 40, "p3": 60, "days": 180}
KINDS = ("comment", "story", "guess", "bottle", "sub", "plan", "offer")
LEVEL = {1: "убраны нарушения", 2: "нельзя предлагать ситуации и комментировать", 3: "доступ закрыт"}


def cfg_get() -> dict:
    with closing(nunu_admin._c()) as c:
        r = c.execute("SELECT v FROM admin_kv WHERE k='reports'").fetchone()
    return DEF | (json.loads(r["v"]) if r else {})


def cfg_set(v: dict) -> dict:
    p = [max(1, min(100, int(v.get(k, DEF[k])))) for k in ("p1", "p2", "p3")]
    if not p[0] <= p[1] <= p[2]:
        raise ValueError
    v = {"min": max(1, min(100, int(v.get("min", 2)))), "aud": max(1, min(1000, int(v.get("aud", 5)))),
         "p1": p[0], "p2": p[1], "p3": p[2], "days": max(7, min(3650, int(v.get("days", 180))))}
    with closing(nunu_admin._c()) as c, c:
        c.execute("INSERT OR REPLACE INTO admin_kv(k, v) VALUES('reports', ?)", (json.dumps(v),))
    return v


def _target(c, uid: int, kind: str, ref: str) -> tuple[int, str]:
    """Чьё это и что там написано. Пожаловаться можно только на то, что тебе действительно показали."""
    if kind == "comment":
        r = c.execute("SELECT owner, author, text FROM plot_comments WHERE id=?", (int(ref) if ref.isdigit() else -1,)).fetchone()
        if r and r["owner"] == uid:
            return r["author"], r["text"]
    elif kind in ("story", "bottle"):
        r = c.execute("SELECT owner, story FROM games WHERE id=?", (ref,)).fetchone()
        seen = r and (kind == "bottle" or c.execute("SELECT 1 FROM plays WHERE game_id=? AND player=?", (ref, uid)).fetchone())
        if seen and r["story"]:
            s = json.loads(r["story"])
            return r["owner"], f"{s.get('q', '')} — {s.get('a', '')}"
    elif kind == "guess":
        gid, _, pid = ref.partition(":")
        r = c.execute("SELECT p.player, p.guess FROM plays p JOIN games g ON g.id=p.game_id "
                      "WHERE g.id=? AND g.owner=? AND p.player=?", (gid, uid, int(pid) if pid.isdigit() else -1)).fetchone()
        if r and r["guess"]:
            return r["player"], r["guess"]
    elif kind == "sub":
        from . import nunu_subs
        with closing(nunu_subs._c()) as a:
            r = a.execute("SELECT uid, data FROM subs WHERE idx=? AND status='accepted'", (int(ref) if ref.isdigit() else -1,)).fetchone()
        if r:
            d = json.loads(r["data"])
            return r["uid"], f"{d[0]}: {d[1]}"
    elif kind == "plan":
        for s in nunu._owner_state(c, uid).get("nunu-seeds") or []:
            if isinstance(s, dict) and s.get("pid") == ref and isinstance(s.get("by"), int) and s["by"] != uid:
                return s["by"], str(s.get("r") or "")
    elif kind == "offer":  # ponytail: не сверяем круг видимости — решает доля разных пожаловавшихся, а не один голос
        r = c.execute("SELECT owner, title, text FROM offers WHERE id=? AND closed=0", (int(ref) if ref.isdigit() else -1,)).fetchone()
        if r:
            return r["owner"], f"{r['title']}: {r['text']}"
    raise HTTPException(404, "Не нашли, на что жалоба")


def _judge(c, uid: int) -> tuple[int, int, float]:
    """(было, стало, доля %): ступень только растёт."""
    cf = cfg_get()
    by = {r[0] for r in c.execute("SELECT DISTINCT by FROM reports WHERE uid=? AND status!='rejected' "
                                  "AND created_at >= datetime('now', ?)", (uid, f"-{cf['days']} day"))}
    fr = {r[0] for r in c.execute("SELECT CASE WHEN a=? THEN b ELSE a END FROM friends WHERE a=? OR b=?", (uid, uid, uid))}
    pct = 100 * len(by) / max(cf["aud"], len(fr | by))
    new = next((n for n, p in ((3, cf["p3"]), (2, cf["p2"]), (1, cf["p1"])) if len(by) >= cf["min"] and pct >= p), 0)
    old = nunu._level(c, uid)
    if new > old:
        with c:
            c.execute("INSERT OR REPLACE INTO sanctions(uid, level, at) VALUES(?, ?, datetime('now'))", (uid, new))
    return old, max(old, new), round(pct, 1)


async def _clean(uid: int) -> list[str]:
    """Ступень 1: убрать всё, на что жаловались. Принятые ситуации уже в колоде — их снимает автор в админке."""
    left = []
    with closing(nunu._db()) as c:
        rows = c.execute("SELECT kind, ref FROM reports WHERE uid=? AND status='open'", (uid,)).fetchall()
        with c:
            for r in rows:
                k, ref = r["kind"], r["ref"]
                if k == "comment":
                    c.execute("DELETE FROM plot_comments WHERE id=?", (int(ref),))
                elif k in ("story", "bottle"):
                    c.execute("UPDATE games SET story=NULL, reported=1 WHERE id=?", (ref,))
                elif k == "guess":
                    gid, _, pid = ref.partition(":")
                    c.execute("UPDATE plays SET guess=NULL WHERE game_id=? AND player=?", (gid, int(pid)))
                elif k == "offer":
                    c.execute("UPDATE offers SET closed=1 WHERE id=?", (int(ref),))
                elif k == "sub":
                    left.append(f"ситуация №{ref} в колоде")
                c.execute("UPDATE reports SET status='removed' WHERE uid=? AND kind=? AND ref=?", (uid, k, ref))
    return left


async def _apply(uid: int, old: int, new: int, pct: float) -> None:
    left = await _clean(uid)
    txt = {1: "По жалобам участников убрано то, что нарушало правила «Ну-ну».",
           2: "По жалобам участников убрано то, что нарушало правила, и пока нельзя предлагать ситуации, "
              "писать комментарии и звать на общие дела.",
           3: "По жалобам участников доступ к «Ну-ну» закрыт."}[new]
    await nunu._notify(uid, txt + " Если это ошибка — напиши автору игры.", nunu.link())
    if config.ADMIN_CHAT_ID:
        with closing(nunu._db()) as c:
            r = c.execute("SELECT name FROM users WHERE tg_id=?", (uid,)).fetchone()
        await nunu._notify(int(config.ADMIN_CHAT_ID), f"⚑ Жалобы: {r['name'] if r else uid} ({uid}) — ступень {old} → {new}, "
                           f"доля {pct}%: {LEVEL[new]}." + (f" Убрать вручную: {', '.join(left)}." if left else ""), nunu.link())


class RepIn(BaseModel):
    kind: str
    ref: str
    why: str = ""


@router.post("")
async def report(body: RepIn, x_init_data: str | None = Header(default=None)):
    return await submit(nunu._user(x_init_data).id, body)


async def submit(by: int, body: RepIn) -> dict:
    """Общая для игры и обмена на сайте (там свой вход через Telegram)."""
    u = SimpleNamespace(id=by)
    if body.kind not in KINDS:
        raise HTTPException(422, "Неизвестно, на что жалоба")
    with closing(nunu._db()) as c:
        uid, text = _target(c, u.id, body.kind, body.ref[:60])
        if uid == u.id:
            raise HTTPException(422, "На себя не жалуются")
        with c:
            c.execute("INSERT OR IGNORE INTO reports(by, uid, kind, ref, why, text) VALUES(?,?,?,?,?,?)",
                      (u.id, uid, body.kind, body.ref[:60], body.why.strip()[:40], text[:500]))
            if body.kind == "comment":  # хозяин и так может убрать комментарий на своём участке — убираем сразу
                c.execute("DELETE FROM plot_comments WHERE id=?", (int(body.ref),))
            if body.kind == "bottle":  # бутылка сразу перестаёт ловиться, как раньше
                c.execute("UPDATE games SET reported=1 WHERE id=?", (body.ref,))
        old, new, pct = _judge(c, uid)
    if new > old:
        nunu._bg(_apply(uid, old, new, pct))
    return {"ok": True}


# ---------- админка ----------
@admin.get("")
def list_(req: Request, x_admin_key: str | None = Header(default=None)):
    nunu_admin._auth(req, x_admin_key)
    with closing(nunu._db()) as c:
        rows = c.execute("SELECT r.*, u.name AS who, b.name AS byname FROM reports r LEFT JOIN users u ON u.tg_id=r.uid "
                         "LEFT JOIN users b ON b.tg_id=r.by ORDER BY r.created_at DESC, r.id DESC LIMIT 300").fetchall()
        lv = {r["uid"]: r["level"] for r in c.execute("SELECT uid, level FROM sanctions")}
        people = {}
        for r in rows:
            p = people.setdefault(r["uid"], {"uid": r["uid"], "name": r["who"] or str(r["uid"]), "level": lv.get(r["uid"], 0), "by": set()})
            if r["status"] != "rejected":
                p["by"].add(r["by"])
        for uid in lv:
            if uid not in people:
                n = c.execute("SELECT name FROM users WHERE tg_id=?", (uid,)).fetchone()
                people[uid] = {"uid": uid, "name": n["name"] if n else str(uid), "level": lv[uid], "by": set()}
    return {"cfg": cfg_get(), "people": [p | {"by": len(p["by"])} for p in people.values()],
            "reports": [{k: r[k] for k in ("id", "uid", "who", "byname", "kind", "why", "text", "status", "created_at")} for r in rows]}


@admin.put("/cfg")
async def cfg(req: Request, x_admin_key: str | None = Header(default=None)):
    nunu_admin._auth(req, x_admin_key)
    try:
        return cfg_set(await req.json())
    except (TypeError, ValueError):
        raise HTTPException(422, "Пороги: числа 1–100, ступени по возрастанию")


@admin.post("/{rid}/reject")
def reject(rid: int, req: Request, x_admin_key: str | None = Header(default=None)):
    """Жалоба несправедлива — не считается в доле."""
    nunu_admin._auth(req, x_admin_key)
    with closing(nunu._db()) as c, c:
        c.execute("UPDATE reports SET status='rejected' WHERE id=?", (rid,))
    return {"ok": True}


@admin.post("/user/{uid}/clear")
async def clear(uid: int, req: Request, x_admin_key: str | None = Header(default=None)):
    """Снять ограничения и не считать прежние жалобы."""
    nunu_admin._auth(req, x_admin_key)
    with closing(nunu._db()) as c, c:
        c.execute("DELETE FROM sanctions WHERE uid=?", (uid,))
        c.execute("UPDATE reports SET status='rejected' WHERE uid=?", (uid,))
    await nunu._notify(uid, "Ограничения в «Ну-ну» сняты. Рады, что ты снова с нами.", nunu.link())
    return {"ok": True}
