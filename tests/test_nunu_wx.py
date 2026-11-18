"""Погодное семя: условия, текст, одно сообщение на одно погодное окно, без повторов строчек."""
import json
import os
import random
from datetime import date

os.environ.setdefault("BOT_TOKEN", "1:test")

from app import nunu, nunu_wx  # noqa: E402

GOOD = {"tmax": 18.4, "dry": True, "sun": True, "calm": True, "rain_before": False}
BAD = GOOD | {"dry": False}
SEED = {"r": "план", "pid": "p1", "life": None,
        "wx": {"dry": True, "sun": False, "calm": False, "t": 15, "ll": "48.44, 22.72", "what": "Испечь пиццу в печи",
               "where": "во дворе"}}


def put(uid, seeds, prefs=None):
    with nunu._db() as c:
        c.execute("INSERT OR REPLACE INTO state(tg_id, data) VALUES(?, ?)",
                  (uid, json.dumps({"nunu-seeds": seeds, "nunu-prefs": prefs or {}})))


def test_fits_and_text():
    assert nunu_wx.fits(SEED["wx"], GOOD) and not nunu_wx.fits(SEED["wx"], BAD)
    assert not nunu_wx.fits(SEED["wx"], GOOD | {"tmax": 12})
    text, line = nunu_wx.message(SEED, GOOD, date(2026, 11, 19), [], random.Random(1))
    assert text.startswith("🌱 По просьбе. Завтра +18 и сухо 😎\n«Испечь пиццу в печи» · 📍 во дворе\n")
    assert line in nunu_wx.lines_for("Испечь пиццу в печи", date(2026, 11, 19), GOOD)
    assert nunu_wx.headline({"sun": True, "dry": True, "calm": True}, GOOD) == "Завтра +18, солнечно и без ветра 😎"
    assert "Тот самый дождичек в четверг" in " ".join(nunu_wx.seasonal(date(2026, 11, 19), GOOD | {"rain_before": True}))


def test_no_repeat():
    pool, last = ["a", "b", "c"], ["a", "b"]
    assert all(nunu_wx.pick(pool, last, random.Random(i)) == "c" for i in range(5))
    assert nunu_wx.pick(pool, pool) in pool  # всё было — любая


def test_one_message_per_window(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    put(1, [SEED, SEED | {"pid": "p2", "life": "done"}])  # сделанное не напоминаем
    put(2, [SEED], {"remind": False})  # напоминания выключены
    days = {date(2026, 11, 18): GOOD, date(2026, 11, 19): GOOD, date(2026, 11, 20): BAD, date(2026, 11, 21): GOOD}
    sent = {d: nunu_wx.due(d, get=lambda ll, f=f: f) for d, f in days.items()}
    assert [u for u, _, _ in sent[date(2026, 11, 18)]] == [1]
    assert sent[date(2026, 11, 19)] == []  # то же окно: завтра снова хорошо — второй раз не пишем
    assert sent[date(2026, 11, 20)] == []  # не подходит
    assert [p for _, _, p in sent[date(2026, 11, 21)]] == ["p1"]  # новое окно — новое сообщение
    assert nunu_wx.answer("wx:n:p1") in nunu_wx.NO and nunu_wx.answer("wx:y:p1") in nunu_wx.YES


def test_goal_weather(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    with nunu._db() as c:
        c.execute("INSERT INTO state(tg_id, data) VALUES(3, ?)", (json.dumps({"nunu-goals": [
            {"id": "g1", "t": "Построить печь", "wx": SEED["wx"] | {"what": "Построить печь"}},
            {"id": "g2", "t": "Старая", "done": "2026-11-01", "wx": SEED["wx"]}]}),))
    out = nunu_wx.due(date(2026, 11, 18), get=lambda ll: GOOD)
    assert [(u, p) for u, _, p in out] == [(3, "g1")] and "«Построить печь»" in out[0][1]


def test_alert_boiler(tmp_path, monkeypatch):
    """«Затопить котёл, если ночью до +5»: режим «Предупредить», один раз на холодную полосу."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    boiler = {"r": "план", "pid": "k1", "wx": {"tn": 5, "ll": "48.44, 22.72", "what": "Затопить котёл"}}
    cold, warm = GOOD | {"tnight": 3.2, "dry": False}, GOOD | {"tnight": 8}
    assert nunu_wx.fits(boiler["wx"], cold) and not nunu_wx.fits(boiler["wx"], warm)
    text, line = nunu_wx.message(boiler, cold, date(2026, 11, 19), [], random.Random(1))
    assert text.startswith("🔔 По просьбе. Сегодня ночью до +3 🌡\n«Затопить котёл»") and line in nunu_wx.WARM + sum(
        (xs for _, xs in nunu_wx.WARN_WORD), [])
    snow = {"wet": True, "windy": True, "ll": "x", "what": "Укрыть рассаду"}
    assert nunu_wx.alert_head(snow, GOOD | {"wet": True, "snow": True, "windy": True, "wind": 52.4}) == \
        "Завтра снег, завтра ветер до 52 км/ч 🌨💨"
    put(5, [boiler])
    days = [(date(2026, 11, 18), cold), (date(2026, 11, 19), cold), (date(2026, 11, 20), warm), (date(2026, 11, 21), cold)]
    assert [len(nunu_wx.due(d, get=lambda ll, f=f: f)) for d, f in days] == [1, 0, 0, 1]
