"""В гости на участок: только друзья по мосту, без анкеты; комментарии к объектам; дело у объекта друга."""
import json
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import nunu, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402

PLOT = {"site": {"n": "4", "face": "юг", "map": "48.4,22.7", "area": "12"}, "sides": {"1": {"l": "30"}},
        "build": [{"what": "Дом"}], "ext": {"x": 1}, "sat": {"on": 1},
        "map": {"objs": [{"id": "pech", "k": "oven", "n": "Печь", "x": 4, "y": 6, "w": 2, "h": 2}]}}


def test_visit(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    def note(uid, text, url):  # пишем сразу, сама отправка не нужна
        sent.append((uid, text))
        return __import__("asyncio").sleep(0)
    monkeypatch.setattr(nunu, "_notify", note)
    monkeypatch.setattr(nunu, "_bg", lambda coro: coro.close())
    c, anton, evgen, chuzhoy = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген"), init_data(3, "Чужой")
    c.put("/nunu/api/state", json={"data": {"nunu-plot": PLOT}}, headers=evgen)
    assert c.get("/nunu/api/visit/2", headers=anton).status_code == 404  # моста нет — в гости нельзя
    with nunu._db() as db:
        db.execute("INSERT INTO friends(a, b) VALUES(1, 2)")
        db.commit()

    v = c.get("/nunu/api/visit/2", headers=anton).json()
    assert v["name"] is None and v["plot"]["map"]["objs"][0]["id"] == "pech"
    assert "build" not in v["plot"] and "ext" not in v["plot"] and "map" not in v["plot"]["site"] and "sat" not in v["plot"]
    assert sent[-1] == (2, "👋 Антон заглянул к тебе на участок")
    c.get("/nunu/api/visit/2", headers=anton)
    assert len(sent) == 1  # второй заход за 12 ч — без сообщения
    assert c.get("/nunu/api/visit/2", headers=chuzhoy).status_code == 404

    assert c.post("/nunu/api/visit/2/comment", json={"obj": "nope", "text": "?"}, headers=anton).status_code == 404
    cm = c.post("/nunu/api/visit/2/comment", json={"obj": "pech", "text": "Классная печь!"}, headers=anton).json()
    assert cm[0]["name"] == "Антон" and "Печь" in sent[-1][1]
    assert c.get("/nunu/api/plot/comments", headers=evgen).json()[0]["text"] == "Классная печь!"

    c.put("/nunu/api/state", json={"data": {"nunu-plot": PLOT, "nunu-prefs": {"commOff": 1}}, "base": 1}, headers=evgen)
    n = len(sent)
    c.post("/nunu/api/visit/2/comment", json={"obj": "pech", "text": "Ещё"}, headers=anton)
    assert len(sent) == n  # хозяин выключил сообщения о комментариях

    cid = cm[0]["id"]
    assert c.delete(f"/nunu/api/comment/{cid}", headers=chuzhoy).status_code == 404
    assert c.delete(f"/nunu/api/comment/{cid}", headers=evgen).json()["ok"]  # хозяин может убрать

    plan = {"pid": "p1", "text": "Испечь хлеб", "obj": "pech"}
    assert c.post("/nunu/api/friend/2/plan", json=plan, headers=anton).json()["ok"]
    seed = c.get("/nunu/api/state", headers=evgen).json()["inbox"][0]
    seed = seed if isinstance(seed, dict) else json.loads(seed)
    assert seed["loc"]["o"] == "pech" and seed["loc"]["x"] == 5 and "у «Печь»" in sent[-1][1]

    # хозяин закрыл участок — ни зайти, ни написать, дело у объекта не привяжется
    c.put("/nunu/api/state", json={"data": {"nunu-plot": PLOT, "nunu-prefs": {"visitClosed": 1}}, "base": 2}, headers=evgen)
    assert c.get("/nunu/api/visit/2", headers=anton).status_code == 403
    assert c.post("/nunu/api/visit/2/comment", json={"obj": "pech", "text": "?"}, headers=anton).status_code == 403
    c.post("/nunu/api/friend/2/plan", json=plan | {"pid": "p2"}, headers=anton)
    inbox = c.get("/nunu/api/state", headers=evgen).json()["inbox"]
    last = inbox[-1] if isinstance(inbox[-1], dict) else json.loads(inbox[-1])
    assert "loc" not in last
