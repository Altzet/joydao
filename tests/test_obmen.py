"""Обмен: кто видит предложение (мосты / друзья друзей / все), отклик один раз, закрыть может только автор."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import nunu, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402


def test_obmen(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    async def note(uid, text, url, html=False):
        sent.append((uid, text))
    monkeypatch.setattr(nunu, "_notify", note)
    monkeypatch.setattr(nunu, "_bg", lambda coro: coro.close() or sent.append("bg"))
    c = TestClient(pause.app)
    anton, boria, olia, chuzhoy = (init_data(i, n) for i, n in ((1, "Антон"), (2, "Боря"), (3, "Оля"), (4, "Чужой")))
    with nunu._db() as db:  # мосты: Антон — Боря — Оля
        db.executemany("INSERT INTO friends(a, b) VALUES(?, ?)", [(1, 2), (2, 3)])

    offer = {"kind": "give", "type": "thing", "title": "Кабачки", "how": "gift", "circle": "bridge"}
    ids = {cir: c.post("/obmen/api/offer", json=offer | {"circle": cir}, headers=anton).json()["id"]
           for cir in ("bridge", "fof", "all")}
    seen = lambda h: {o["circle"] for o in c.get("/obmen/api/list", headers=h).json()["offers"]}  # noqa: E731
    assert seen(boria) == {"bridge", "fof", "all"}
    assert seen(olia) == {"fof", "all"}
    assert seen(chuzhoy) == {"all"}
    assert c.post("/obmen/api/offer", json=offer | {"circle": "x"}, headers=anton).status_code == 422

    assert c.post(f"/obmen/api/offer/{ids['bridge']}/reply", json={}, headers=olia).status_code == 404  # не её круг
    assert c.post(f"/obmen/api/offer/{ids['fof']}/reply", json={"text": "заберу"}, headers=olia).json() == {"ok": True}
    assert c.post(f"/obmen/api/offer/{ids['fof']}/reply", json={}, headers=olia).json()["again"]  # второй раз — без письма
    assert sent == ["bg"]
    mine = {o["id"]: o for o in c.get("/obmen/api/list", headers=anton).json()["offers"]}
    assert mine[ids["fof"]]["replies"] == 1 and mine[ids["fof"]]["rel"] == "me"

    assert c.post(f"/obmen/api/offer/{ids['all']}/close", headers=chuzhoy).status_code == 404
    assert c.post(f"/obmen/api/offer/{ids['all']}/close", headers=anton).status_code == 200
    assert seen(chuzhoy) == set()
    assert "{{PLAY}}" not in c.get("/obmen").text
    c.put("/nunu/api/state", json={"data": {"nunu-prefs": {"theme": "light", "acc": "coral", "fs": 1}}}, headers=boria)
    assert c.get("/obmen/api/list", headers=boria).json()["prefs"] == {"theme": "light", "acc": "coral", "fs": 1}
