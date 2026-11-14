"""Ситуации участников: право только у прошедшего всё, проверка ИИ, честный подсчёт, принятая — в игре."""
import asyncio
import json
import os
from types import SimpleNamespace as NS

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, nunu, nunu_admin, nunu_subs, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402

SC = ["Соседи", "Сосед просит дрель, а прошлую вернул сломанной.", ["Дам и предложу помочь", "Позову чинить вместе"],
      ["Скажу, что подумаю", "Отвечу завтра"], ["Откажу прямо", "Дам под залог"]]


class FakeBot:
    sent = []

    async def send_message(self, chat, text, **k):
        FakeBot.sent.append(text)
        return NS(message_id=100 + len(FakeBot.sent))

    async def send_poll(self, chat, q, opts, **k):
        return NS(message_id=200, poll=NS(id="P1"))

    async def stop_poll(self, chat, mid):
        raise RuntimeError("нет сети")  # итог берём из сохранённого счёта

    async def delete_message(self, chat, mid):
        pass

    session = NS(close=lambda: asyncio.sleep(0))


def test_verdict():
    r = {"min": 10, "pct": 60, "extend": True}
    assert nunu_subs.verdict(6, 4, r) == "accept"
    assert nunu_subs.verdict(5, 5, r) == "decline"
    assert nunu_subs.verdict(3, 0, r) == "extend"
    assert nunu_subs.verdict(3, 0, r | {"extend": False}) == "decline"


def test_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    monkeypatch.setattr(config, "CHANNEL_ID", "@joydao")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "k" * 20)
    nunu_admin._cache.pop("cfg", None)
    notes = []

    async def note(uid, text, url):
        notes.append(text)
    monkeypatch.setattr(nunu, "_notify", note)
    monkeypatch.setattr(nunu, "_bg", lambda coro: coro.close())
    monkeypatch.setattr(nunu_subs, "_bot", lambda: FakeBot())
    c, me = TestClient(pause.app), init_data(7, "Аня")

    assert c.post("/nunu/api/subs", json={"scene": SC}, headers=me).status_code == 403  # ещё не всё пройдено
    c.put("/nunu/api/state", json={"data": {"nunu-seen": nunu_subs.pool()}}, headers=me)
    assert c.get("/nunu/api/subs/me", headers=me).json()["eligible"]
    assert c.post("/nunu/api/subs", json={"scene": SC[:3]}, headers=me).status_code == 422

    async def bad(sc):
        return {"ok": False, "reason": "Уберите грубость"}
    monkeypatch.setattr(nunu_subs, "moderate", bad)
    sid = c.post("/nunu/api/subs", json={"scene": SC}, headers=me).json()["id"]
    asyncio.run(nunu_subs.process(sid))
    assert "Уберите грубость" in notes[-1]
    sid = c.post("/nunu/api/subs", json={"scene": SC, "anon": True}, headers=me).json()["id"]  # отказ ИИ право не тратит

    async def good(sc):
        return {"ok": True, "reason": ""}
    monkeypatch.setattr(nunu_subs, "moderate", good)
    asyncio.run(nunu_subs.process(sid))
    assert "Ситуации участников" in FakeBot.sent[0] and "не меньше 10" in FakeBot.sent[0] and "Участник" in FakeBot.sent[0]
    assert c.post("/nunu/api/subs", json={"scene": SC}, headers=me).status_code == 429  # одна в неделю

    nunu_subs.on_poll("P1", [7, 3])
    with nunu_subs._c() as db:
        db.execute("UPDATE subs SET ends='2000-01-01 00:00:00'")
        db.commit()
    asyncio.run(nunu_subs.finish_due())
    mine = c.get("/nunu/api/subs/me", headers=me).json()["mine"]
    assert mine[0]["status"] == "accepted" and mine[0]["yes"] == 7 and "Принята" in FakeBot.sent[-1]
    page = c.get("/nunu").text
    assert "Сосед просит дрель" in page and '"AU": {}' in page  # анонимно — без подписи
    d = c.get("/nunu/admin/api/subs", headers={"X-Admin-Key": "k" * 20}).json()
    assert d["subs"][0]["status"] == "accepted"
