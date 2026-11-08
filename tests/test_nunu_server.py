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
    assert own["plays"] == [{"name": "Евген", "s": [2] * 10, "g": [0] * 10}]
    assert c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Я", "s": [0] * 10, "g": [0] * 10},
                  headers=anton).status_code == 409
    assert c.post("/nunu/api/game", json={"name": "А", "seed": 5, "mode": "duo", "s": [9] * 10},
                  headers=anton).status_code == 422
