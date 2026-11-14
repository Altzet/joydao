"""Комната администратора: только по ключу, правки через черновик, старые партии держат свои карточки."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, nunu, nunu_admin, pause  # noqa: E402

K = {"X-Admin-Key": "k" * 20}


def test_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    nunu_admin._cache.pop("cfg", None)
    nunu_admin._fails.clear()
    c = TestClient(pause.app)
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "")
    assert c.get("/nunu/admin").status_code == 404  # без ключа в .env комнаты нет
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "k" * 20)
    assert c.get("/nunu/admin").status_code == 200
    assert c.post("/nunu/admin/api/login", headers={"X-Admin-Key": "x"}).status_code == 401
    assert c.get("/nunu/admin/api/content").status_code == 401
    d = c.get("/nunu/admin/api/content", headers=K).json()
    b = d["base"]
    assert len(b["S"]) >= 50 and len(b["C0"]) >= 3 and len(b["SP"]) == len(b["SW"]) and d["v"] == 0
    assert "window.NUNU_CFG=" not in c.get("/nunu").text  # правок нет — страница как есть

    w = {**b, "S": b["S"][:-1]}  # удалять нельзя
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    e = c.post("/nunu/admin/api/publish", json={}, headers=K)
    assert e.status_code == 422 and "удалять" in e.json()["detail"]["errors"][0]

    w = {**b, "S": [*b["S"], ["Работа", "Хвалят при всех", ["a", "b"], ["c", "d"], ["e", ""]]]}
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    assert c.post("/nunu/admin/api/publish", json={}, headers=K).status_code == 422  # пустой ответ
    w["S"][-1][4][1] = "f"
    w["T"] = {"nav.week": "Удиви"}
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    r = c.post("/nunu/admin/api/publish", json={"note": "новая"}, headers=K).json()
    assert r == {"ok": True, "v": 1, "deck": "2"}
    page = c.get("/nunu").text
    assert "window.NUNU_CFG=" in page and "Хвалят при всех" in page and '"nav.week": "Удиви"' in page

    w["T"] = {"nav.week": "Удиви себя!"}  # только текст — колода та же
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    assert c.post("/nunu/admin/api/publish", json={}, headers=K).json()["deck"] is None
    v = c.post("/nunu/admin/api/restore/1", headers=K).json()["draft"]
    assert v["T"] == {"nav.week": "Удиви"} and len(v["S"]) == len(b["S"]) + 1
    assert len(c.get("/nunu/admin/api/versions", headers=K).json()) == 2
    assert c.get("/nunu/admin/api/stats", headers=K).json()["games"] == 0

    for _ in range(10):
        c.post("/nunu/admin/api/login", headers={"X-Admin-Key": "x"})
    assert c.post("/nunu/admin/api/login", headers=K).status_code == 429  # перебор ключа тормозим


def test_landing(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "k" * 20)
    for k in ("cfg", "lp"):
        nunu_admin._cache.pop(k, None)
    nunu_admin._fails.clear()
    c = TestClient(pause.app)
    home = c.get("/").text
    assert "Знаешь ли ты своих друзей?" in home and "грандиозное" not in home  # вместо старого объявления
    d = c.get("/nunu/admin/api/content", headers=K).json()
    w = d["live"]
    w["LP"]["links"]["play"] = "javascript:alert(1)"
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    assert c.post("/nunu/admin/api/publish", json={}, headers=K).status_code == 422  # чужие схемы ссылок не пускаем
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert c.post("/nunu/admin/api/upload", content=b"<svg>", headers=K).status_code == 415
    url = c.post("/nunu/admin/api/upload", content=png, headers=K).json()["url"]
    assert c.get(url).content == png
    w["LP"]["links"]["play"] = "https://t.me/x/y"
    w["LP"]["hero"]["title"] = "Привет <b>"
    w["LP"]["features"]["items"][0]["img"] = url
    w["LP"]["off"] = ["faq"]
    c.put("/nunu/admin/api/draft", json=w, headers=K)
    assert c.post("/nunu/admin/api/publish", json={}, headers=K).status_code == 200
    home = c.get("/").text
    assert "Привет &lt;b&gt;" in home and url in home and 'id="faq"' not in home and "https://t.me/x/y" in home
