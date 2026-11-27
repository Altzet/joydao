"""Вторая часть админки: ключ обязателен; настройки обмена, страниц и напоминаний действуют без перезапуска."""
import os
from datetime import date

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import admin_more, config, nunu, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402

K = {"X-Admin-Key": "k" * 20}


def test_admin_more(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "k" * 20)
    sent = []

    async def note(uid, text, url, **kw):
        sent.append(uid)
    monkeypatch.setattr(nunu, "_notify", note)
    c = TestClient(pause.app)
    a, b = init_data(1, "Антон"), init_data(2, "Боря")
    with nunu._db() as db:
        db.execute("INSERT INTO friends(a, b) VALUES(1, 2)")
    offer = {"kind": "give", "type": "thing", "title": "Кабачки", "how": "hours", "circle": "all"}
    assert c.post("/obmen/api/offer", json=offer, headers=a).status_code == 200
    assert sent == [2]  # Боря на мосту — узнал о предложении
    sent.clear()

    assert c.get("/nunu/admin/api/x/funnel").status_code == 401
    f = c.get("/nunu/admin/api/x/funnel", headers=K).json()
    assert dict(f["steps"])["Есть мост с другом"] == 2 and f["goal"]["want"] == 20 and len(f["weeks"]) == 8

    # обмен: выключили «Часами» и лимит 1 — форма и сервер слушаются сразу
    cfg = c.get("/nunu/admin/api/x/obmen", headers=K).json()["cfg"]
    r = c.put("/nunu/admin/api/x/obmen/cfg", json=cfg | {"hows": ["gift"], "max_open": 1, "h1": "Излишки"}, headers=K)
    assert r.json()["hows"] == ["gift"]
    assert c.post("/obmen/api/offer", json=offer | {"how": "gift"}, headers=a).status_code == 429  # лимит 1
    assert c.post("/obmen/api/offer", json=offer, headers=b).status_code == 422  # «Часами» выключено
    assert '"h1": "Излишки"' in c.get("/obmen").text
    oid = c.get("/nunu/admin/api/x/obmen", headers=K).json()["offers"][0]["id"]
    c.post(f"/nunu/admin/api/x/obmen/{oid}/hide", headers=K)
    assert c.get("/obmen/api/list", headers=b).json()["offers"] == []

    # люди и сообщение
    p = c.get("/nunu/admin/api/x/people", headers=K).json()["people"]
    assert {x["uid"]: x["bridges"] for x in p} == {1: 1, 2: 1}
    assert c.post("/nunu/admin/api/x/people/2/msg", json={"text": "Привет"}, headers=K).json()["ok"] and sent == [2]

    # рассылка: пробное без ID — нельзя; с ID — одно сообщение
    assert c.post("/nunu/admin/api/x/bc", json={"text": "x", "test": True}, headers=K).status_code == 422
    assert c.post("/nunu/admin/api/x/bc", json={"text": "x", "test": True, "uid": 1}, headers=K).json()["count"] == 1
    assert c.get("/nunu/admin/api/x/bc", headers=K).json()["counts"]["bridged"] == 2

    # страницы: правка поверх кода, пустое — назад к коду
    c.put("/nunu/admin/api/x/pages/uchastok", json={"h1": "Мой участок онлайн"}, headers=K)
    assert "Мой участок онлайн" in c.get("/uchastok").text
    c.put("/nunu/admin/api/x/pages/uchastok", json={}, headers=K)
    assert "Мой участок онлайн" not in c.get("/uchastok").text

    # напоминания: частота сада идёт в reminders_due
    assert c.put("/nunu/admin/api/x/remind", json={"hour": 9, "garden_every": 3, "on": True}, headers=K).json()["garden_every"] == 3
    assert admin_more.get("remind")["hour"] == 9
    nunu.reminders_due(date.today(), garden_every=3)

    # копия базы — настоящий SQLite
    r = c.get("/nunu/admin/api/x/backup", headers=K)
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
    assert "db_mb" in c.get("/nunu/admin/api/x/sys", headers=K).json()
