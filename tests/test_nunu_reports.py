"""Жалобы: доля разных пожаловавшихся → ступени 1–3; на чужое, не показанное тебе, жаловаться нельзя."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import nunu, nunu_reports, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402

PLOT = {"map": {"objs": [{"id": "pech", "k": "oven", "n": "Печь", "x": 4, "y": 6, "w": 2, "h": 2}]}}


def test_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    async def note(uid, text, url):
        sent.append((uid, text))
    monkeypatch.setattr(nunu, "_notify", note)
    bg = []
    monkeypatch.setattr(nunu, "_bg", bg.append)

    def flush():  # фоновые задачи (сообщения, уборка) — после ответа
        while bg:
            __import__("asyncio").run(bg.pop(0))
    monkeypatch.setattr(nunu_reports, "cfg_get", lambda: nunu_reports.DEF)  # min 2, aud 5, 20/40/60 %
    c = TestClient(pause.app)
    bad, h = init_data(9, "Грубиян"), {i: init_data(i, f"Друг{i}") for i in range(1, 6)}
    with nunu._db() as db:
        db.executemany("INSERT INTO friends(a, b) VALUES(?, 9)", [(i,) for i in range(1, 6)])
        db.commit()
    cid = {}
    for i in range(1, 6):
        c.put("/nunu/api/state", json={"data": {"nunu-plot": PLOT}}, headers=h[i])
        cid[i] = c.post(f"/nunu/api/visit/{i}/comment", json={"obj": "pech", "text": "дурак"}, headers=bad).json()[0]["id"]

    rep = lambda i, **k: c.post("/nunu/api/report", json={"kind": "comment", "ref": str(cid[i])} | k, headers=h[i])  # noqa: E731
    assert c.post("/nunu/api/report", json={"kind": "comment", "ref": str(cid[1])}, headers=h[2]).status_code == 404  # не твой участок
    assert rep(1).json()["ok"]
    assert c.get("/nunu/api/plot/comments", headers=h[1]).json() == []  # хозяин пожаловался — убран сразу
    with nunu._db() as db:
        assert nunu._level(db, 9) == 0  # один голос не решает (min 2)
    rep(2)
    flush()  # 2 из 5 = 40 % → ступень 2: нарушения убраны, писать нельзя
    with nunu._db() as db:
        assert nunu._level(db, 9) == 2
    assert len(c.get("/nunu/api/plot/comments", headers=h[3]).json()) == 1  # убирается только то, на что жаловались
    assert sent[-1][0] == 9 and "нельзя" in sent[-1][1]
    assert c.post("/nunu/api/visit/4/comment", json={"obj": "pech", "text": "ещё"}, headers=bad).status_code == 403
    assert c.get("/nunu/api/state", headers=bad).status_code == 200  # играть ещё можно
    with nunu._db() as db, db:
        db.execute("INSERT INTO reports(by, uid, kind, ref) VALUES(3, 9, 'comment', 'x')")
    with nunu._db() as db:
        old, new, pct = nunu_reports._judge(db, 9)  # 3 из 5 = 60 % → доступ закрыт
    assert (old, new, pct) == (2, 3, 60.0)
    assert c.get("/nunu/api/state", headers=bad).status_code == 403


def test_offer_report(tmp_path, monkeypatch):
    """⚑ на предложение в обмене: жалоба записана; на ступени 1 предложение закрывается."""
    import asyncio
    from contextlib import closing
    from app import nunu, nunu_reports, obmen  # noqa: F401 — obmen создаёт таблицу offers
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    with closing(obmen._db()) as c, c:
        c.execute("INSERT INTO offers(owner, kind, type, title, text, how, circle) VALUES(7,'give','food','Кабачки','продам за 100','gift','all')")
        oid = c.execute("SELECT id FROM offers").fetchone()[0]
    with closing(nunu._db()) as c:
        assert nunu_reports._target(c, 8, "offer", str(oid))[0] == 7
        c.execute("INSERT INTO reports(by, uid, kind, ref, why, text) VALUES(8, 7, 'offer', ?, 'Продажа', 'x')", (str(oid),))
        c.commit()
    asyncio.run(nunu_reports._clean(7))
    with closing(nunu._db()) as c:
        assert c.execute("SELECT closed FROM offers WHERE id=?", (oid,)).fetchone()[0] == 1
