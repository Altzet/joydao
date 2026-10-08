"""Сценарии обновления: лёгкий онбординг, парная ссылка, ранжирование, воронка,
самонастройка контента. Запуск: python -m pytest tests/ -v (нужен BOT_TOKEN в env или .env)."""
import asyncio
import os
from datetime import date

os.environ.setdefault("BOT_TOKEN", "1:test")

import pytest  # noqa: E402

from app import agent, bot, compat, db  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_migrated", False)


def _light(uid, birth="1992-03-21", name="A"):
    db.upsert_user(uid, name=name, birth_date=birth, gender="", seek="", active=0, username="")


def _dating(uid, birth, gender, seek, username=""):
    db.upsert_user(uid, name=f"u{uid}", birth_date=birth, gender=gender, seek=seek,
                   photo_id="p", active=1, username=username)


def test_light_user_is_not_in_feed_until_profile():
    _light(1)
    _dating(2, "1988-07-06", "m", "f")
    assert not db.is_dating(db.get_user(1))
    assert db.daily_candidates(2, 5) == []          # лёгкий пользователь не кандидат
    db.update_user(1, gender="f", seek="m", photo_id="p", active=1)
    assert db.is_dating(db.get_user(1))
    assert [r["tg_id"] for r in db.daily_candidates(2, 5)] == [1]


def test_candidates_ranked_by_compat_and_like_requires_shown():
    me = "1992-03-21"
    _dating(10, me, "m", "f")
    births = {11: "1990-01-08", 12: "1985-11-29", 13: "1999-06-14", 14: "1994-02-02"}
    for uid, b in births.items():
        _dating(uid, b, "f", "m")
    score = lambda uid: compat.compatibility(date.fromisoformat(me), date.fromisoformat(births[uid]))["score"]
    rank = lambda r: compat.compatibility(date.fromisoformat(me), date.fromisoformat(r["birth_date"]))["score"]
    got = [r["tg_id"] for r in db.daily_candidates(10, 2, rank=rank)]
    best = sorted(births, key=score, reverse=True)[:2]
    assert sorted(got) == sorted(best)
    assert db.was_shown(10, got[0])
    assert not db.was_shown(10, next(u for u in births if u not in got))


def test_funnel_and_source_conversion():
    db.log_event(1, "start"); db.log_event(1, "birth"); db.log_event(2, "start")
    f = db.funnel(7)
    assert f["start"] == 2 and f["birth"] == 1 and f["match"] == 0
    db.record_source(1, "ch_day"); db.record_source(2, "ch_day"); db.record_source(3, "ch_pair")
    _light(1)
    assert db.source_conversion("ch_") == {"ch_day": (2, 1), "ch_pair": (1, 0)}


def test_pick_kind_rotation_then_exploit():
    d = date(2026, 10, 2)
    assert agent.pick_kind(d, conv={}) == agent.KINDS[d.timetuple().tm_yday % 3]  # мало данных
    conv = {"ch_day": (30, 2), "ch_chs": (30, 12), "ch_pair": (30, 5)}
    assert agent.pick_kind(d, conv=conv, rnd=0.99) == "chs"                       # лучший
    assert agent.pick_kind(d, conv=conv, rnd=0.01) == agent.KINDS[d.timetuple().tm_yday % 3]
    assert "start=src_ch_chs" in agent.cta("chs")


def test_birth_parsing_and_exact_adult_check():
    assert bot._parse_birth_text("21/3/1992") == date(1992, 3, 21)
    assert bot._parse_birth_text("21 03 1992") == date(1992, 3, 21)
    assert bot._parse_birth_text("завтра") is None
    today = date(2026, 10, 2)
    assert bot._is_adult(date(2008, 10, 2), today)        # 18 сегодня
    assert not bot._is_adult(date(2008, 10, 3), today)    # 18 завтра — ещё нет


def test_inviter_parsing():
    _light(5)
    assert bot._parse_inviter("pair_5", 7) == 5
    assert bot._parse_inviter("ref_5", 7) == 5            # старые ссылки работают
    assert bot._parse_inviter("pair_5", 5) is None        # сам себя
    assert bot._parse_inviter("pair_999", 7) is None      # нет такого
    assert bot._parse_inviter("src_ch_day", 7) is None


class _Sink:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append(text)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def test_pair_result_notifies_both_without_leaking_birth_date():
    _light(1, "1992-03-21", "Оля")
    _light(2, "1988-07-06", "Иван")
    target, fake_bot = _Sink(), _Sink()
    asyncio.run(bot._pair_result(fake_bot, 2, 1, target))
    mine = target.sent[0]
    assert "Оля" in mine and "21.03.1992" not in mine
    chat_id, theirs = fake_bot.sent[0]
    assert chat_id == 1 and "Иван" in theirs and "06.07.1988" not in theirs
    assert db.funnel(7)["pair_done"] == 1


def test_date_balance_in_day_prompt():
    # 10.10.2026: цифры 1,0,1,0,2,0,2,6 → 1 и 2 удвоены, 6 одна, тройки нет, а сумма = 3
    assert agent.balance(date(2026, 10, 10)).startswith(
        "усилены: 1 (власть) ×2, 2 (творческая психология) ×2; присутствуют: 6 (творчество)")
    p = agent._prompt_for({"kind": "day", "num": 3, "date": "10.10.2026"})
    assert "Числа 3 нет среди цифр даты" in p and "Баланс дня" in p


def test_balance_in_portrait_and_mailing():
    from app import mailing, suisai
    # 21.03.1992: цифры 2,1,0,3,1,9,9,2 → 1,2,9 ×2; 3 одна; нет 4,5,6,7,8
    assert suisai.energy_groups(date(1992, 3, 21)) == ([(1, 2), (2, 2), (9, 2)], [3], [4, 5, 6, 7, 8])
    # 10.10.2026 несёт 6 (творчество) — её нет в дате рождения → подарок дня
    assert "творчество" in mailing.message_for(date(1992, 3, 21), date(2026, 10, 10))
    # 12.12.2021 — только 1 и 2, а они в дате рождения уже есть → без подарка
    assert "🎁" not in mailing.message_for(date(1992, 3, 21), date(2021, 12, 12))


def test_daily_energy_never_repeats_within_month():
    """У одного человека в пределах месяца: ни одно сообщение не повторяется,
    а при одинаковом числе дня отличаются и заголовок, и совет, и акцент для отношений."""
    from datetime import timedelta
    from app import mailing
    for birth in (date(1992, 3, 21), date(1985, 12, 31), date(2000, 1, 9), date(1977, 7, 7)):
        day = date(2026, 1, 1)
        msgs = [(day + timedelta(k)) for k in range(400)]
        texts = {d: mailing.message_for(birth, d) for d in msgs}
        for k in range(len(msgs) - 31):  # любое окно в 31 день — без повторов целиком
            win = [texts[d] for d in msgs[k:k + 31]]
            assert len(set(win)) == len(win)
        for d in msgs:  # внутри календарного месяца одинаковое число → все строки разные
            same = [x for x in msgs if (x.year, x.month) == (d.year, d.month)
                    and mailing.day_parts(birth, x)["n"] == mailing.day_parts(birth, d)["n"]]
            parts = [mailing.day_parts(birth, x) for x in same]
            for key in ("title", "advice", "love"):
                assert len({p[key] for p in parts}) == len(parts), (birth, d, key)


def test_check_date_shows_balance():
    from app import teaser
    t = teaser.build(date(1992, 3, 21))
    assert t["strong"] == ["власть ×2", "творческая психология ×2", "служение людям ×2"]
    assert t["missing"][0] == "передача знаний"


def test_bot_sends_everything_to_app():
    from types import SimpleNamespace
    _light(9, "1992-03-21", "Оля")
    sink = _Sink()
    msg = SimpleNamespace(text="💞 Пары", from_user=SimpleNamespace(id=9), answer=sink.answer)
    asyncio.run(bot.old_keyboard(msg))  # старая клавиатура убирается, дальше — приложение
    assert "переехало" in sink.sent[0]
    assert bot.OLD_COMMANDS["me"] == "portrait" and bot.OLD_CALLBACKS["checkdate"] == "check"
    assert bot.app_url("portrait").endswith("?tab=portrait")


def test_profile_edit_and_delete(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    cl = TestClient(api.app)
    _light(1, "1992-03-21", "Оля")
    assert cl.post("/api/profile", json={"name": " Оленька ", "birth": "20.03.1992", "daily": False}).json()["ok"]
    u = db.get_user(1)
    assert u["name"] == "Оленька" and u["birth_date"] == "1992-03-20" and u["daily_opt"] == 0
    assert cl.post("/api/profile", json={"visible": True}).status_code == 409   # без анкеты знакомств
    assert cl.post("/api/profile", json={"birth": "01.01.2015"}).status_code == 400  # 18+
    assert cl.post("/api/profile", json={"seek": "x"}).status_code == 422
    _dating(2, "1988-07-06", "m", "f")
    db.set_like(1, 2, True)
    assert cl.post("/api/profile/delete").json()["ok"]
    assert db.get_user(1) is None and db.get_matches(2) == []


def test_compat_by_kind_business_roles():
    from app import teaser
    a, b = date(1992, 3, 21), date(1988, 7, 6)
    love, biz, fr = (teaser.build_compat(a, b, k) for k in ("love", "business", "friend"))
    assert love["kind"] == "love" and biz["kind_label"] == "в бизнесе"
    assert biz["verdict"] in teaser.TEXTS["business"]["verdict"]
    r = biz["roles"]  # 21.03.1992 (1,2,3,9) vs 06.07.1988 (1,6,7,8,9)
    assert r["you"] == ["переговоры и люди", "аналитика и финансы"]
    assert "маркетинг и дизайн" in r["partner"] and "лидерство и решения" in r["both"]
    assert r["gap"] == ["обучение и продукт", "продажи и напор"]
    assert "roles" not in love and teaser.build_compat(a, b, "nonsense")["kind"] == "love"


def test_feed_by_goal():
    # Я ищу дружбу и бизнес; в ленте «дружба» — только те, кто тоже выбрал дружбу, пол не важен
    _dating(1, "1992-03-21", "f", "")
    db.update_user(1, goals="friend,business")
    _dating(2, "1988-07-06", "f", "")
    db.update_user(2, goals="friend")
    _dating(3, "1990-01-01", "m", "f")          # только любовь (по умолчанию)
    _dating(4, "1985-05-05", "m", "")
    db.update_user(4, goals="business")
    assert db.is_dating(db.get_user(1))        # без «кого ищу» — ок, любви нет в целях
    ids = lambda goal: {r["tg_id"] for r in db.daily_candidates(1, 10, goal=goal)}
    assert ids("friend") == {2} and ids("business") == {4}
    assert ids("friend") == {2}                # повторный запрос — те же показанные


def test_onboarding_goals_skip_seek_without_love():
    from types import SimpleNamespace
    class St:
        def __init__(self): self.d, self.s = {}, None
        async def get_data(self): return self.d
        async def update_data(self, **kw): self.d.update(kw)
        async def set_state(self, s): self.s = s
    st, sink = St(), _Sink()
    msg = SimpleNamespace(edit_text=lambda *a, **k: sink.answer(*a), edit_reply_markup=lambda **k: sink.answer(""))
    cb = lambda data: SimpleNamespace(data=data, message=msg, answer=lambda *a, **k: sink.answer(""))
    asyncio.run(bot.ob_goals(cb("goal:business"), st))
    assert st.d["goals"] == ["business"]
    asyncio.run(bot.ob_goals(cb("goal:done"), st))
    asyncio.run(bot.ob_gender(cb("g:m"), st))
    assert st.s == bot.Onboarding.city          # «кого ищу» пропущен


def test_group_compat(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    _light(1, "1992-03-21", "Оля")
    r = TestClient(api.app).post("/api/group", json={"kind": "business", "people": [
        {"name": "Иван", "date": "06.07.1988"}, {"name": "Аня", "date": "1990-01-01"}]}).json()
    assert [p["name"] for p in r["people"]] == ["Я", "Иван", "Аня"]
    assert r["matrix"][0][1] == r["matrix"][1][0] and r["matrix"][0][0] is None
    assert r["best"]["score"] >= r["hard"]["score"] and "gap" in r and "covered" in r


def test_photo_upload_from_app(monkeypatch):
    import io
    from types import SimpleNamespace as NS
    from fastapi.testclient import TestClient
    from PIL import Image
    from app import api
    sent = []

    class Bot:
        async def send_photo(self, chat, photo, caption=""):
            sent.append(chat)
            return NS(photo=[NS(file_id="NEW")])
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    monkeypatch.setattr(api, "_bot", Bot())
    _light(1, "1992-03-21", "Оля")
    cl = TestClient(api.app)
    buf = io.BytesIO(); Image.new("RGB", (3000, 2000), "red").save(buf, "PNG")
    assert cl.post("/api/photo", content=buf.getvalue()).json()["ok"]
    assert db.get_user(1)["photo_id"] == "NEW" and sent[0] == 1
    assert cl.post("/api/photo", content=b"not an image").status_code == 400


def test_light_users_get_profile_nudge():
    from app import mailing
    mon, tue = date(2026, 10, 5), date(2026, 10, 6)
    assert "уже 4 человек" in mailing.message_for(date(1990, 1, 1), mon, dating=False, people=4)
    assert "уже 4 человек" not in mailing.message_for(date(1990, 1, 1), tue, dating=False, people=4)
    assert "уже 4 человек" not in mailing.message_for(date(1990, 1, 1), mon, dating=True, people=4)
    _light(1, "1992-03-21", "Оля")
    kb = mailing._kb(db.get_user(1))
    assert any(b.callback_data == "dating" for row in kb.inline_keyboard for b in row)
