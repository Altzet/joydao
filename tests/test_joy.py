"""JOY energy, фаза 1: тест принципов, антифарм первого JOY, «Число дня» и серия.
Запуск: python -m pytest tests/ -v"""
import os
from datetime import date, timedelta

os.environ.setdefault("BOT_TOKEN", "1:test")

import pytest  # noqa: E402

from app import db, joy  # noqa: E402

RIGHT = [ok for _, _, _, ok, _ in joy.PRINCIPLES]


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_migrated", False)


def _dating(uid, photo="p"):
    db.upsert_user(uid, name=f"u{uid}", birth_date="1990-05-05", gender="f", seek="m",
                   photo_id=photo, active=1, username="")


def _age(uid, days):
    with db.connect() as c:
        c.execute("UPDATE users SET created_at=datetime('now', ?) WHERE tg_id=?", (f"-{days} days", uid))


def test_first_joy_needs_test_profile_and_hold():
    _dating(1, photo="")
    assert joy.state(1)["status"] == "need_test"
    assert joy.pass_test(1, [(a + 1) % 3 for a in RIGHT]) == (False, 0)
    assert joy.pass_test(1, RIGHT[:7] + [(a + 1) % 3 for a in RIGHT[7:]]) == (True, 7)  # порог 7 из 9
    assert len(joy.PRINCIPLES) == 9 and sorted(e for e, *_ in joy.PRINCIPLES) == list(range(1, 10))
    assert joy.state(1)["status"] == "need_profile"      # без фото нет первого JOY
    db.update_user(1, photo_id="p")
    s = joy.state(1)
    assert s["status"] == "pending" and s["balance"] == 0  # антифарм: ждём 3 дня
    _age(1, joy.HOLD_DAYS)
    s = joy.state(1)
    assert s["new_award"] == 1 and s["balance"] == 1 and s["level"] == "Искра"
    assert joy.state(1)["balance"] == 1                    # повторно не начисляется


def test_match_unlocks_first_joy_early():
    _dating(1); _dating(2)
    joy.pass_test(1, RIGHT)
    db.set_like(1, 2, True); db.set_like(2, 1, True)
    assert joy.state(1)["balance"] == 1


def test_deleting_account_does_not_refarm_joy():
    _dating(1); joy.pass_test(1, RIGHT); _age(1, 5)
    assert joy.state(1)["balance"] == 1
    db.delete_user(1)
    _dating(1); joy.pass_test(1, RIGHT); _age(1, 5)
    s = joy.state(1)
    assert s["balance"] == 1 and s["new_award"] == 0


def test_daily_quiz_once_and_streak():
    _dating(1); joy.pass_test(1, RIGHT)
    start = date(2026, 10, 1)
    for i in range(7):
        d = start + timedelta(days=i)
        r = joy.answer_quiz(1, joy.daily_question(d)["answer"], d)
        assert r["correct"]
        assert joy.answer_quiz(1, 0, d) is None            # второй ответ за день не принимается
    bal, _ = joy.totals(1)
    assert bal == 7 * joy.QUIZ + joy.STREAK                 # 7 верных + серия раз в неделю
    assert joy.streak(1, start + timedelta(days=6)) == 7


def test_wrong_answer_gives_nothing():
    _dating(1); joy.pass_test(1, RIGHT)
    d = date(2026, 10, 10)
    r = joy.answer_quiz(1, (joy.daily_question(d)["answer"] + 1) % 3, d)
    assert not r["correct"] and r["joy"] == 0 and joy.totals(1) == (0, 0)


def _rich(uid):
    """Участник клуба с первым JOY на балансе."""
    _dating(uid); joy.pass_test(uid, RIGHT); _age(uid, 5); joy.state(uid)


def test_spend_spark_boost_without_double_charge():
    _rich(1); _dating(2)
    assert joy.spend(1, joy.SPARK, "spend:spark:2") is True
    assert joy.spend(1, joy.SPARK, "spend:spark:2") is False     # повторная «Искра» тому же — бесплатно, без списания
    assert joy.sparked_me(2) == {1}
    joy.boost(1); joy.boost(1)                                    # второй буст, пока идёт первый, не списывается
    assert joy.boosted() == {1} and joy.boost_until(1)
    assert joy.totals(1)[0] == joy.ONBOARD - joy.SPARK - joy.BOOST
    with pytest.raises(joy.NotEnough):
        joy.spend(1, joy.UNIT, "spend:report:2")
    assert joy.totals(1)[0] >= 0                                  # в минус не уходим


def test_spark_puts_sender_first_in_feed(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    _rich(1)
    for uid, birth in ((2, "1990-05-05"), (3, "1991-01-01"), (4, "1992-02-02")):
        db.upsert_user(uid, name=f"u{uid}", birth_date=birth, gender="m", seek="f", photo_id="p", active=1, username="")
    db.upsert_user(1, name="u1", birth_date="1985-12-31", gender="f", seek="m", photo_id="p", active=1, username="")
    db.daily_candidates(1, 5)                                      # 1 видел 2, 3, 4
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    r = TestClient(api.app).post("/api/joy/spark/2")
    assert r.status_code == 200 and r.json()["balance"] == (joy.ONBOARD - joy.SPARK) / joy.UNIT
    monkeypatch.setattr(api, "_auth", lambda _: 2)
    db.update_user(2, seek="f")
    cards = TestClient(api.app).get("/api/candidates?goal=love").json()["candidates"]
    assert cards[0]["id"] == 1 and cards[0]["spark"]


def test_deep_report_hides_birth_date_and_needs_joy(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from app import api, config
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    _dating(1)
    db.upsert_user(2, name="u2", birth_date="1988-07-06", gender="m", seek="f", photo_id="p", active=1, username="")
    assert [r["tg_id"] for r in db.daily_candidates(1, 5)] == [2]
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    assert TestClient(api.app).post("/api/joy/report/2").status_code == 402   # без JOY не купить
    joy.pass_test(1, RIGHT); _age(1, 5); joy.state(1)
    rep = TestClient(api.app).post("/api/joy/report/2").json()
    assert len(rep["kinds"]) == 3 and all("date" not in k for k in rep["kinds"]) and rep["ai"] == ""
    TestClient(api.app).post("/api/joy/report/2")                  # повторное открытие — бесплатно
    assert joy.totals(1)[0] == joy.ONBOARD - joy.REPORT
    assert asyncio.run(joy.deep_report(1, 2))["name"] == "u2"


def test_deep_report_ai_text_cached(monkeypatch):
    import asyncio
    from app import agent, config
    calls = []

    async def fake_ask(system, prompt, max_tokens=1024):
        calls.append(prompt)
        return "Разбор пары"
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(agent, "_ask", fake_ask)
    _dating(1); _dating(2)
    assert asyncio.run(joy.deep_report(1, 2))["ai"] == "Разбор пары"
    assert asyncio.run(joy.deep_report(1, 2))["ai"] == "Разбор пары" and len(calls) == 1  # второй раз — из кэша
    assert "1990-05-05" not in calls[0]                                                   # дата не уходит даже в ИИ


def test_pair_quiz_for_matches_rewards_members_once(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    _rich(1)
    db.upsert_user(2, name="u2", birth_date="1988-07-06", gender="m", seek="f", photo_id="p", active=1, username="")
    joy.pass_test(2, RIGHT)
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    cl = TestClient(api.app)
    assert cl.get("/api/pairquiz/2").status_code == 403              # только взаимная симпатия
    db.daily_candidates(1, 5); db.daily_candidates(2, 5)
    db.set_like(1, 2, True); db.set_like(2, 1, True)
    q = cl.get("/api/pairquiz/2").json()
    assert len(q["questions"]) == 5 and q["status"] == "none"
    assert joy.pair_questions(1, 2) == joy.pair_questions(2, 1)      # вопросы одинаковые у обоих
    r = cl.post("/api/pairquiz/2", json={"mine": [0, 1, 0, 1, 0], "guess": [1, 1, 1, 1, 1]}).json()
    assert r["status"] == "mine" and r["result"] is None             # ответы второго не видны, пока он не ответил
    monkeypatch.setattr(api, "_auth", lambda _: 2)
    r = TestClient(api.app).post("/api/pairquiz/1", json={"mine": [0, 1, 1, 1, 1], "guess": [0, 1, 0, 1, 0]}).json()
    assert r["status"] == "done" and r["reward"] == 0.2
    res = r["result"]
    assert res["same"] == 3 and res["i_guessed"] == 5 and res["they_guessed"] == 4
    before = joy.totals(1)[0]
    TestClient(api.app).post("/api/pairquiz/1", json={"mine": [1] * 5, "guess": [1] * 5})  # повтор не перезаписывает и не награждает
    assert joy.totals(1)[0] == before == joy.ONBOARD + joy.PAIR_QUIZ
    assert joy.pair_quiz_result(2, 1)["same"] == 3


def test_tournament_join_answer_settle(monkeypatch):
    from datetime import datetime
    week = "2026-W41"
    monkeypatch.setattr(joy, "tour_week", lambda now=None: week)
    monkeypatch.setattr(joy, "tour_open", lambda w, now=None: True)
    right = [q["answer"] for q in joy.tour_questions(week)]
    for uid in range(1, 6):
        _rich(uid)
        assert joy.tour_join(uid) and not joy.tour_join(uid)         # второй вход — без списания
    joy.tour_answer(1, right)                                        # 9 из 9
    joy.tour_answer(2, right[:8] + [(right[8] + 1) % 3])             # 8 из 9
    joy.tour_answer(3, right[:8] + [(right[8] + 1) % 3])             # 8 из 9, но позже (по секундам — тот же порядок по id)
    with db.connect() as c:                                          # 4 опоздал: старт 11 минут назад
        c.execute("UPDATE tournament SET started_at=datetime('now','-11 minutes') WHERE user_id=4")
    assert joy.tour_answer(4, right)["score"] == 0
    assert joy.tour_answer(1, [0] * 9)["again"]                      # вторая попытка не принимается
    res = joy.settle_tournament(week)
    pool = 5 * joy.TOUR_ENTRY * 90 // 100                            # 5 входов, 10% сгорает
    assert res["places"] == [(1, pool * 50 // 100), (2, pool * 30 // 100), (3, pool * 20 // 100)]
    assert joy.settle_tournament(week)["places"] == []               # повторный расчёт ничего не начисляет
    assert joy.totals(1)[0] == joy.ONBOARD - joy.TOUR_ENTRY + pool // 2
    assert joy.tour_closes(week) == datetime(2026, 10, 11, 18, 0)


def test_tournament_refund_when_few_finish(monkeypatch):
    week = "2026-W42"
    monkeypatch.setattr(joy, "tour_week", lambda now=None: week)
    monkeypatch.setattr(joy, "tour_open", lambda w, now=None: True)
    _rich(1); _rich(2)
    joy.tour_join(1); joy.tour_join(2); joy.tour_answer(1, [0] * 9)
    res = joy.settle_tournament(week)
    assert sorted(res["refunded"]) == [1, 2] and joy.totals(2)[0] == joy.ONBOARD


def test_tournament_settle_job_notifies(monkeypatch):
    import asyncio
    from app import main
    week = "2026-W43"
    monkeypatch.setattr(joy, "tour_week", lambda now=None: week)
    monkeypatch.setattr(joy, "tour_open", lambda w, now=None: True)
    for uid in (1, 2, 3):
        _rich(uid); joy.tour_join(uid); joy.tour_answer(uid, [0] * 9)
    sent = []

    class Bot:
        async def send_message(self, uid, text, **kw):
            sent.append((uid, text))
    asyncio.run(main.settle_tournament(Bot()))
    assert sorted(u for u, _ in sent) == [1, 2, 3] and "месте" in sent[0][1]


def test_three_languages_and_joy_translated():
    from app.i18n import SUPPORTED, T, user_lang
    assert SUPPORTED == ("ru", "uk", "en")
    _dating(1); db.update_user(1, lang="zh")                         # язык, которого больше нет
    assert user_lang(db.get_user(1)) == "en"
    from app.i18n import table
    texts = [q for _, q, opts, _, why in joy.PRINCIPLES for q in (q, why, *opts)]
    texts += [x for row in joy.PAIR_QUESTIONS for x in row] + [n for _, n in joy.LEVELS]
    for lang in ("uk", "en"):                                        # все тексты JOY есть в словарях
        assert [t for t in texts if t not in table(lang)] == []


def test_rating_by_earned_week_and_all_time(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    for uid in (1, 2, 3):
        _rich(uid)                                                   # у каждого 1 JOY за вход
    _dating(4)                                                       # не член клуба — не в рейтинге
    with db.connect() as c:
        joy._award(c, 2, 2 * joy.UNIT, "tour:2026-W40:place")          # 2-му +2 JOY, но давно
        c.execute("UPDATE joy_ledger SET created_at=datetime('now','-30 days') WHERE reason='tour:2026-W40:place'")
        joy._award(c, 3, joy.QUIZ, "quiz:today")                       # 3-му +0,05 на этой неделе
        joy._award(c, 1, joy.TOUR_ENTRY, "tour:2026-W41:refund")       # возврат взноса — не заработок
    joy.spend(3, joy.SPARK, "spend:spark:1")                           # траты место не меняют
    week, alltime = joy.rating(1, "week"), joy.rating(1, "all")
    assert [b["name"] for b in week["top"]][0] == "u3" and week["total"] == 3
    assert [b["name"] for b in alltime["top"]][0] == "u2" and alltime["top"][0]["joy"] == 3
    assert alltime["me"]["place"] in (2, 3) and alltime["me"]["joy"] == 1
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    assert TestClient(api.app).get("/api/joy/rating?period=all").json()["top"][0]["joy"] == 3


def test_celeb_unlock_once_for_joy(monkeypatch, tmp_path):
    import json
    from fastapi.testclient import TestClient
    from app import api, celebs
    f = tmp_path / "celebs.json"
    f.write_text(json.dumps([{"id": "Q1", "cat": "mind", "name": {"ru": "Эйнштейн", "en": "Einstein"},
                              "birth": "1879-03-14", "wiki": {"en": "https://en.wikipedia.org/wiki/Albert_Einstein"}}]))
    monkeypatch.setattr(celebs, "FILE", f)
    celebs._all.cache_clear()
    _dating(1)
    monkeypatch.setattr(api, "_auth", lambda _: 1)
    cl = TestClient(api.app)
    assert cl.get("/api/celebs").json()["celebs"] == [{"id": "Q1", "cat": "mind", "name": "Эйнштейн", "open": False}]
    assert cl.post("/api/joy/celeb/Q1").status_code == 402          # без JOY не открыть
    assert cl.post("/api/joy/celeb/nope").status_code == 404
    joy.pass_test(1, RIGHT); _age(1, 5); joy.state(1)
    r = cl.post("/api/joy/celeb/Q1").json()
    assert r["card"]["chs"] == 5 and len(r["kinds"]) == 3 and "Эйнштейн" in r["share_text"]  # 14 → 1+4 = 5
    cl.post("/api/joy/celeb/Q1")                                   # повторно — бесплатно
    assert joy.totals(1)[0] == joy.ONBOARD - joy.CELEB
    assert cl.get("/api/celebs").json()["celebs"][0]["open"]
    celebs._all.cache_clear()
