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
    assert c.get("/nunu/api/state", headers=anton).json() == {"data": {"seeds": [1]}, "name": "Антон", "inbox": []}

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
    c.put("/nunu/api/state", json={"data": {"nunu-seeds": [{"z": 0, "life": {"same": True}}, {"z": 0}, {"z": 2},
                                                           {"z": 2, "life": {"same": False}}, {"z": -1}]}}, headers=evgen)
    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 7, "mode": "duo", "s": [0] * 10, "g": [1] * 10,
                                       "to": 2}, headers=anton).json()
    assert quiet.sent[0] == 2  # Евгену сразу сообщение
    w = c.get("/nunu/api/world", headers=evgen).json()
    assert w["friends"][0] | {} == {"id": 1, "name": "Антон", "boards": 0, "turn": "me", "gid": g["id"],
                                    "last": None, "mark": None, "island": [0, 0, 0], "bloom": [0, 0, 0]}
    c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Евген", "s": [2] * 10, "g": [0] * 10}, headers=evgen)
    assert c.post(f"/nunu/api/game/{g['id']}/boards", json={"friend": 1, "n": 5.5}, headers=evgen).json()["total"] == 5.5
    r = c.post(f"/nunu/api/game/{g['id']}/boards", json={"friend": 2, "n": 9}, headers=anton)
    assert r.json()["total"] == 5.5  # второе не пишется, и партия не считается дважды
    c.post("/nunu/api/friend/2/deed", headers=anton)
    f = c.get("/nunu/api/world", headers=anton).json()["friends"][0]
    assert (f["name"], f["boards"], f["turn"], f["last"], f["island"], f["bloom"]) == ("Евген", 8.5, None, g["id"], [2, 0, 2], [1, 0, 0])


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
    assert v["story"] == {"q": story["q"], "real": True, "only": False}  # ответа автора ещё не видно
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


def test_bottle(tmp_path, monkeypatch):
    """Бутылка: ловит незнакомец, на карте его нет, пока оба не захотели мост; жалоба прячет бутылку."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    async def quiet(*a):
        sent.append(a)
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, olya, boris = TestClient(pause.app), init_data(1, "Антон"), init_data(5, "Оля"), init_data(6, "Боря")
    assert c.post("/nunu/api/game", json={"name": "А", "seed": 1, "mode": "duo", "s": [0] * 10, "g": [0] * 10,
                                          "bottle": True}, headers=anton).status_code == 422
    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 9, "mode": "hero", "s": [1] * 10, "bottle": True},
               headers=anton).json()
    assert c.get("/nunu/api/bottle", headers=anton).json() == {"gid": None}  # свою не ловим
    assert c.get("/nunu/api/bottle", headers=olya).json() == {"gid": g["id"]}
    v = c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Оля", "g": [1] * 10}, headers=olya).json()
    assert v["bottle"] and v["my"]["wish"] == {"me": False, "them": False, "friends": False}
    assert "бутылку выловил Оля" in sent[-1][1]
    assert c.get("/nunu/api/bottle", headers=olya).json() == {"gid": None}  # второй раз ту же — нет
    assert c.get("/nunu/api/world", headers=anton).json()["friends"] == []  # незнакомка не на карте
    assert c.post(f"/nunu/api/bottle/{g['id']}/wish", json={"other": 1}, headers=olya).json()["friends"] is False
    assert c.get(f"/nunu/api/game/{g['id']}", headers=anton).json()["plays"][0]["wish"]["them"] is True
    assert c.post(f"/nunu/api/bottle/{g['id']}/wish", json={"other": 6}, headers=anton).status_code == 404
    assert c.post(f"/nunu/api/bottle/{g['id']}/wish", json={"other": 5}, headers=anton).json()["friends"] is True
    assert [f["name"] for f in c.get("/nunu/api/world", headers=olya).json()["friends"]] == ["Антон"]
    c.post(f"/nunu/api/bottle/{g['id']}/report", headers=boris)
    assert c.get("/nunu/api/bottle", headers=boris).json() == {"gid": None}


def test_story_only_with_options(tmp_path, monkeypatch):
    """Только своя ситуация с вариантами: верный не виден до ответа, выбор варианта отмечается сам."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")

    async def quiet(*a):
        quiet.sent = a
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen, boris = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген"), init_data(3, "Боря")
    story = {"q": "Сосед просит дрель", "a": "Дал до вечера", "real": True, "o": ["Не дал", "Дал до вечера", ""],
             "only": True}
    g = c.post("/nunu/api/game", json={"name": "Антон", "seed": 3, "mode": "hero", "s": [6] * 10, "story": story},
               headers=anton).json()
    v = c.get(f"/nunu/api/game/{g['id']}", headers=evgen).json()["story"]
    assert v["only"] and sorted(v["c"]) == ["Дал до вечера", "Не дал"] and "a" not in v  # повтор и пустой убраны
    v = c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Евген", "g": [6] * 10, "guess": "Дал до вечера"},
               headers=evgen).json()
    assert v["my"]["mark"] == 2 and "угадал" in quiet.sent[1]
    v = c.post(f"/nunu/api/game/{g['id']}/play", json={"name": "Боря", "g": [6] * 10, "guess": "Не дал"},
               headers=boris).json()
    assert v["my"]["mark"] == 0 and "не угадал" in quiet.sent[1]
    fr = {f["id"]: f["boards"] for f in c.get("/nunu/api/world", headers=anton).json()["friends"]}
    assert fr == {2: 1.0, 3: 0.0}


def test_joint_deed(tmp_path, monkeypatch):
    """Общее дело: другу сообщение и семя в сад; отметили оба — доски один раз."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    async def quiet(*a):
        sent.append(a)
    monkeypatch.setattr(nunu, "_notify", quiet)
    c, anton, evgen, boris = TestClient(pause.app), init_data(1, "Антон"), init_data(2, "Евген"), init_data(3, "Боря")
    c.post("/nunu/api/game", json={"name": "Антон", "seed": 3, "mode": "duo", "s": [0] * 10, "g": [0] * 10, "to": 2},
           headers=anton)
    plan = {"pid": "abc12345", "text": "Сходим в баню", "due": "2026-11-20"}
    assert c.post("/nunu/api/friend/3/plan", json=plan, headers=anton).status_code == 404  # не друг
    assert c.post("/nunu/api/friend/2/plan", json=plan, headers=anton).json()["ok"]
    assert "общее дело" in sent[-1][1] and "Сходим в баню" in sent[-1][1]
    inbox = c.get("/nunu/api/state", headers=evgen).json()["inbox"]
    assert inbox[0]["pid"] == "abc12345" and inbox[0]["fid"] == 1 and inbox[0]["due"] == "2026-11-20"
    assert c.get("/nunu/api/state", headers=evgen).json()["inbox"] == []  # отдаётся один раз
    c.post("/nunu/api/friend/2/deed", json={"pid": "abc12345"}, headers=anton)
    c.post("/nunu/api/friend/1/deed", json={"pid": "abc12345"}, headers=evgen)
    assert c.get("/nunu/api/world", headers=anton).json()["friends"][0]["boards"] == 3.0


def test_reminders(tmp_path, monkeypatch):
    """День дела — одно напоминание; про сад не чаще раза в неделю; выключено — молчим."""
    from datetime import date
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    seeds = [{"r": "Позвоню маме", "d": "2026-11-01", "life": None, "due": "2026-11-11", "pid": "a1"},
             {"r": "x", "d": "2026-11-10", "life": None}]
    with nunu.closing(nunu._db()) as c, c:
        c.execute("INSERT INTO state(tg_id,data) VALUES(1,?)", (json.dumps({"nunu-seeds": seeds}),))
        c.execute("INSERT INTO state(tg_id,data) VALUES(2,?)",
                  (json.dumps({"nunu-seeds": seeds, "nunu-prefs": {"remind": False}}),))
        c.execute("INSERT INTO state(tg_id,data) VALUES(3,?)",
                  (json.dumps({"nunu-seeds": [{"r": "y", "d": "2026-10-01", "life": {"same": 1}}]}),))
    r = nunu.reminders_due(date(2026, 11, 11))
    assert [u for u, _ in r] == [1, 1, 3]
    assert "Сегодня" in r[0][1] and "полить" in r[1][1] and "новые" in r[2][1]
    assert nunu.reminders_due(date(2026, 11, 11)) == []
    assert nunu.reminders_due(date(2026, 11, 15)) == []
    assert len(nunu.reminders_due(date(2026, 11, 18))) == 2


def test_meet_friends(tmp_path, monkeypatch):
    """Позвать друзей друг к другу: мост только когда открыли оба; чужие связи не раскрываются."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    sent = []

    async def quiet(*a):
        sent.append(a)
    monkeypatch.setattr(nunu, "_notify", quiet)
    c = TestClient(pause.app)
    anton, evgen, olya, max_ = (init_data(k, n) for k, n in ((1, "Антон"), (2, "Евген"), (3, "Оля"), (4, "Макс")))
    i = c.post("/nunu/api/invite", headers=anton).json()["id"]
    for h in (evgen, olya):
        c.post(f"/nunu/api/invite/{i}/accept", headers=h)
    sent.clear()
    assert c.post("/nunu/api/friend/2/meet", json={"other": 4}, headers=anton).status_code == 404  # Макс не друг
    assert c.post("/nunu/api/friend/2/meet", json={"other": 3}, headers=anton).json() == {"ok": True}
    assert sorted(a[0] for a in sent) == [2, 3] and "ты и Оля" in [a[1] for a in sent if a[0] == 2][0]
    mid = sent[0][2].split("startapp=")[1]
    assert c.post("/nunu/api/friend/3/meet", json={"other": 2}, headers=anton).json() == {"ok": True}
    assert len(sent) == 2  # повтор никому не пишет
    assert c.post(f"/nunu/api/meet/{mid}/accept", headers=max_).status_code == 404
    assert c.post(f"/nunu/api/meet/{mid}/accept", headers=evgen).json() == {"friend": {"id": 3, "name": "Оля"}, "bridge": False}
    assert [f["id"] for f in c.get("/nunu/api/world", headers=evgen).json()["friends"]] == [1]
    assert c.post(f"/nunu/api/meet/{mid}/accept", headers=olya).json() == {"friend": {"id": 2, "name": "Евген"}, "bridge": True}
    assert sorted(f["id"] for f in c.get("/nunu/api/world", headers=evgen).json()["friends"]) == [1, 3]


def test_timed_reminder(tmp_path, monkeypatch):
    """Дело с часом: в свою минуту по времени игрока, один раз; утренний проход его не трогает."""
    from datetime import date, datetime
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    # 18:30 в Киеве (UTC+2, getTimezoneOffset = -120) = 16:30 UTC
    seeds = [{"r": "Пицца в печи", "d": "2026-11-11", "life": None, "due": "2026-11-11", "at": "18:30", "tz": -120, "pid": "t1"}]
    with nunu.closing(nunu._db()) as c, c:
        c.execute("INSERT INTO state(tg_id,data) VALUES(1,?)", (json.dumps({"nunu-seeds": seeds}),))
    d = date(2026, 11, 11)
    assert nunu.reminders_due(d) == []  # утром — рано
    assert nunu.reminders_due(d, datetime(2026, 11, 11, 16, 29), daily=False) == []
    r = nunu.reminders_due(d, datetime(2026, 11, 11, 16, 30), daily=False)
    assert len(r) == 1 and "Пора" in r[0][1]
    assert nunu.reminders_due(d, datetime(2026, 11, 11, 16, 31), daily=False) == []


def test_slope(monkeypatch):
    """Уклон участка: плоскость по сетке высот; короткая ссылка Google Карт раскрывается в координаты."""
    pct, az, rms = nunu.slope_fit([(e, n, 100 + 0.02 * e - 0.01 * n) for e in (-60, 0, 60) for n in (-60, 0, 60)])
    assert abs(pct - 2.236) < 0.01 and abs(az - 296.6) < 0.2 and rms < 1e-9  # растёт на восток → вниз на запад-северо-запад
    assert nunu._coords("48.439200, 22.717800") == (48.4392, 22.7178)
    assert nunu._coords("просто текст") is None
    calls = []

    def fake(url, timeout=8):
        calls.append(url)
        if "goo.gl" in url:
            return "https://www.google.com/maps/place/X/@48.4392,22.7178,17z/data=!3d48.4391!4d22.7177", b""
        if "eudem25m" in url:  # Украины нет в EU-DEM → берём SRTM
            return url, json.dumps({"results": [{"elevation": None}] * 25}).encode()
        locs = [tuple(map(float, p.split(","))) for p in url.split("locations=")[1].split("&")[0].split("|")]
        return url, json.dumps({"results": [{"elevation": 120 - (la - 48.4391) * 111320 * 0.03} for la, lo in locs]}).encode()

    async def no_sleep(*a):
        pass
    monkeypatch.setattr(nunu, "_get", fake)
    monkeypatch.setattr(nunu.asyncio, "sleep", no_sleep)
    nunu._slope_cache.clear()
    c = TestClient(pause.app)
    assert c.post("/nunu/api/slope", json={"q": "x"}).status_code == 401
    r = c.post("/nunu/api/slope", json={"q": "https://maps.app.goo.gl/cz1cRvGeCRHZYRr48"}, headers=init_data(1, "А")).json()
    assert r["lat"] == 48.4391 and r["src"] == "SRTM 30 м" and abs(r["pct"] - 3) < 0.1 and abs(r["az"]) % 360 < 1  # вниз на север
    assert c.post("/nunu/api/slope", json={"q": "нет"}, headers=init_data(1, "А")).status_code == 422
