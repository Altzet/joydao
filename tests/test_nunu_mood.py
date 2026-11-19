"""Опыт ABAB «Как ты сейчас?»: расписание недель, кого спрашивать, кнопки в чате, вывод по правилу, записанному заранее."""
import os
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import nunu, nunu_mood as m, pause  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402


def test_phases():
    assert m.phase(date(2026, 11, 22)) is None
    assert [m.phase(date(2026, 11, 23) + timedelta(days=7 * k)) for k in range(4)] == list("ABAB")
    assert m.phase(date(2026, 11, 29)) == "A" and m.phase(date(2026, 11, 30)) == "B"
    assert m.phase(date(2026, 12, 21)) is None


def test_ask_mark_and_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c = TestClient(pause.app)
    for uid, name in ((1, "Антон"), (2, "Евген"), (3, "Боря")):
        c.put("/nunu/api/state", json={"data": {"nunu-seeds": []}}, headers=init_data(uid, name))
    c.put("/nunu/api/state", json={"data": {"nunu-prefs": {"moodAsk": False}}}, headers=init_data(3, "Боря"))
    b_day = date(2026, 11, 30)
    assert m.due(date(2026, 11, 24)) == []                # неделя A — молчим
    assert sorted(m.due(b_day)) == [1, 2]                 # Боря отключил
    assert m.due(b_day) == []                             # второй раз в тот же день — нет
    m.mark(1, 1, datetime(2026, 12, 1, 9, tzinfo=timezone.utc))
    assert m.due(date(2026, 12, 1)) == [2]                # Антон уже отметился сегодня
    st = c.get("/nunu/api/state", headers=init_data(1, "Антон")).json()
    assert st["data"]["nunu-mood"][-1]["q"] == 1 and st["data"]["nunu-mood"][-1]["src"] == "bot" and st["rev"] == 2
    assert "больше не спрошу" in m.answer("mood:no", 2)
    assert c.get("/nunu/api/state", headers=init_data(2, "Евген")).json()["data"]["nunu-prefs"]["moodAsk"] is False


def test_verdict_rule():
    end = date(2026, 12, 21)
    assert m.verdict([1, 5, 2, 6], 0, date(2026, 12, 1)) == "идёт опыт"
    assert m.verdict([1, 5, 2, 6], 0, end).startswith("оставить")
    assert m.verdict([1, 5, 2, 6], 1, end).startswith("убрать: вопрос отключали")
    assert m.verdict([3, 3, 3, 2], 0, end).startswith("убрать: разницы нет")
    assert m.verdict([1, 5, 2, 3], 0, end).startswith("неоднозначно")


def test_report(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c = TestClient(pause.app)
    c.put("/nunu/api/state", json={"data": {"nunu-mood": [
        {"id": "a", "t": "2026-11-24T10:00:00+00:00", "q": 1}, {"id": "b", "t": "2026-12-01T10:00:00+00:00", "q": 0},
        {"id": "c", "t": "2026-12-02T10:00:00+00:00", "q": 0}]}}, headers=init_data(1, "Антон"))
    r = m.report(date(2026, 12, 2))
    assert r["now"] == "B" and [w["marked"] for w in r["people"][0]["weeks"]] == [1, 2, 0, 0] and r["verdict"] == "идёт опыт"
