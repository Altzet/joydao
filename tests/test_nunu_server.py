"""«Ну-ну» на сервере: вход по initData, игра по ссылке, чужие ответы скрыты до своего хода."""
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, nunu, pause  # noqa: E402


def init_data(uid, name):
    d = {"auth_date": "1700000000", "user": json.dumps({"id": uid, "first_name": name})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(key, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Init-Data": urlencode(d)}


def test_game_by_link(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")

    async def quiet(*a):
        quiet.sent = a
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген")

    assert c.get("/nunu/api/state").status_code == 401  # без Telegram не пускаем
    assert c.put("/nunu/api/state", json={"data": {"seeds": [1]}}, headers=anton).status_code == 200
    assert c.get("/nunu/api/state", headers=anton).json() == {"data": {"seeds": [1]}, "name": "Антон"}

    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 5, "mode": "duo", "s": [0] * 10, "g": [1] * 10},
               headers=anton).json()
    assert g["link"].endswith("startapp=" + g["id"])
    v = c.get(f"/nunu/api/game/{g['id']}", headers=evgen).json()
    assert v["seed"] == 5 and "s" not in v  # до своего хода ответы Антона не видны

    v = c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Евген", "s": [2] * 10, "g": [0] * 10},
               headers=evgen).json()
    assert v["s"] == [0] * 10 and quiet.sent[0] == 1  # Евген видит Антона, Антону ушло сообщение
    own = c.get(f"/nunu/api/game/{g['id']}", headers=anton).json()
    assert own["plays"] == [{"id": 2, "name": "Евген", "s": [2] * 10, "g": [0] * 10, "guess": None, "mark": None}]
    assert c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Я", "s": [0] * 10, "g": [0] * 10},
                  headers=anton).status_code == 409
    assert c.post("/nunu/api/game", json={"name": "А", "seed": 5, "mode": "duo", "s": [9] * 10},
                  headers=anton).status_code == 422


def test_world_map(tmp_path, monkeypatch):
    """Карта: партия другу с карты → у него «твой ход», после ответа мост копится по аккаунтам."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")

    async def quiet(*a):
        quiet.sent = a
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген")
    c.put("/nunu/api/state", json={"data": {"nunu-seeds": [{"z": 0}, {"z": 0}, {"z": 2}, {"z": -1}]}}, headers=evgen)
    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 7, "mode": "duo", "s": [0] * 10, "g": [1] * 10,
                                       "to": 2}, headers=anton).json()
    assert quiet.sent[0] == 2  # Евгену сразу сообщение
    w = c.get("/nunu/api/world", headers=evgen).json()
    assert w["friends"][0] | {} == {"id": 1, "name": "Антон", "boards": 0, "turn": "me", "gid": g["id"],
                                    "last": None, "mark": None, "island": [0, 0, 0]}
    c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Евген", "s": [2] * 10, "g": [0] * 10}, headers=evgen)
    c.post(f"/nunu/api/game/{g['id']}/boards", json={"friend": 1, "n": 5.5}, headers=evgen)
    c.post(f"/nunu/api/game/{g['id']}/boards", json={"friend": 2, "n": 9}, headers=anton)  # второе не пишется
    c.post("/nunu/api/friend/2/deed", headers=anton)
    f = c.get("/nunu/api/world", headers=anton).json()["friends"][0]
    assert (f["name"], f["boards"], f["turn"], f["last"], f["island"]) == ("Евген", 8.5, None, g["id"], [2, 0, 1])


def test_two_answers(tmp_path, monkeypatch):
    """В «Кто я, по-твоему?» можно два образа; повтор и третий — нельзя."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c, anton = TestClient(pause.app), init_data(1, "Антон")
    s = [[0, 2], 1] + [3] * 8
    ok = c.post("/nunu/api/game", json={"name": "А", "seed": 1, "mode": "duo", "s": s, "g": s}, headers=anton)
    assert ok.status_code == 200
    assert c.get(f"/nunu/api/game/{ok.json()['id']}", headers=anton).json()["s"][0] == [0, 2]
    for bad in ([1, 1], [0, 1, 2], [6, 0]):
        r = c.post("/nunu/api/game", json={"name": "А", "seed": 1, "mode": "duo", "s": [bad] + [0] * 9, "g": s},
                   headers=anton)
        assert r.status_code == 422


def test_invite_first(tmp_path, monkeypatch):
    """Позвать: друг сначала приходит на карту (мост без досок), позвавшему — сообщение."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")

    async def quiet(*a):
        quiet.sent = a
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген")
    i = c.post("/nunu/api/invite", headers=anton).json()
    assert i["link"].endswith("startapp=" + i["id"]) and i["id"].startswith("i")
    assert c.post(f"/nunu/api/invite/{i['id']}/accept", headers=anton).json() == {"friend": None}
    assert c.post(f"/nunu/api/invite/{i['id']}/accept", headers=evgen).json() == {"friend": {"id": 1, "name": "Антон"}}
    assert quiet.sent[0] == 1
    f = c.get("/nunu/api/world", headers=anton).json()["friends"]
    assert [(x["name"], x["boards"], x["last"]) for x in f] == [("Евген", 0, None)]


def test_own_story(tmp_path, monkeypatch):
    """Своя ситуация: друг угадывает своими словами, ответ автора виден только после догадки,
    отметка «угадал» — доска мосту, отметить можно один раз."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")

    async def quiet(*a):
        quiet.sent = a
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген")
    story = {"q": "Сосед просит дрель, прошлую вернул сломанной", "a": "Дал, но до вечера", "real": True}
    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 3, "mode": "duo", "s": [0] * 10, "g": [0] * 10,
                                       "to": 2, "story": story}, headers=anton).json()
    v = c.get(f"/nunu/api/game/{g['id']}", headers=evgen).json()
    assert v["story"] == {"q": story["q"], "real": True}  # ответа автора ещё не видно
    v = c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Евген", "s": [1] * 10, "g": [0] * 10,
                                                        "guess": "Дал бы, куда деваться"}, headers=evgen).json()
    assert v["story"]["a"] == "Дал, но до вечера" and v["my"]["guess"] == "Дал бы, куда деваться"
    assert "угадывал твою ситуацию" in quiet.sent[1]
    w = c.get("/nunu/api/world", headers=anton).json()["friends"][0]
    assert w["mark"] == g["id"]
    assert c.post(f"/nunu/api/game/{g['id']}/mark", json={"player": 2, "mark": 2}, headers=evgen).status_code == 404
    assert c.post(f"/nunu/api/game/{g['id']}/mark", json={"player": 2, "mark": 2}, headers=anton).json() == {"ok": True}
    assert c.post(f"/nunu/api/game/{g['id']}/mark", json={"player": 2, "mark": 0}, headers=anton).json() == {"ok": False}
    w = c.get("/nunu/api/world", headers=anton).json()["friends"][0]
    assert (w["mark"], w["boards"]) == (None, 1.0)
    assert c.get(f"/nunu/api/game/{g['id']}", headers=evgen).json()["my"]["mark"] == 2
