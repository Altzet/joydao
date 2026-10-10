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
