"""Почему человек не видит других в ленте «Люди». Только чтение базы.
Запуск на сервере:  .venv/bin/python tools/why_not_in_feed.py [@username или tg_id] [love|friend|business]\nБез аргументов — список всех пользователей с id."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402


def reasons(me, u, goal, liked, skipped):
    """Причины, по которым u не попадёт в ленту me (пусто — попадёт). Правила — как в db.daily_candidates."""
    r = []
    if not u["active"]:
        r.append("анкета не закончена или скрыта (пауза)")
    if goal != "friend" and goal not in db.goals_of(u):
        r.append(f"не выбрал цель «{goal}» (его цели: {','.join(db.goals_of(u))})")
    if goal == "love":
        if me["seek"] != "any" and u["gender"] != me["seek"]:
            r.append(f"пол {u['gender'] or '—'}, а вы ищете {me['seek']}")
        if u["seek"] not in ("any", me["gender"]):
            r.append(f"он(а) ищет {u['seek'] or '—'}, а ваш пол {me['gender']}")
    if u["tg_id"] in liked:
        r.append("вы уже поставили лайк (смотрите во «Взаимно»)")
    if u["tg_id"] in skipped:
        r.append(f"вы пропустили {skipped[u['tg_id']][:10]} — вернётся через {db.SKIP_DAYS} дней")
    return r


def main():
    who = sys.argv[1] if len(sys.argv) > 1 else ""
    goal = sys.argv[2] if len(sys.argv) > 2 else "love"
    with db.connect() as c:
        if not who:  # без аргумента — список всех, чтобы найти свой id
            for u in c.execute("SELECT * FROM users ORDER BY created_at DESC"):
                print(f"id={u['tg_id']}  @{u['username'] or '—'}  {u['name']}  {u['created_at'][:16]}  "
                      f"анкета={'да' if db.is_dating(u) else 'нет'}")
            return
        me = (c.execute("SELECT * FROM users WHERE tg_id=?", (int(who),)).fetchone() if who.isdigit()
              else c.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (who.lstrip("@"),)).fetchone())
        if not me:
            sys.exit(f"Нет пользователя {who}. Запустите без аргументов — покажу всех с id.")
        users = c.execute("SELECT * FROM users WHERE tg_id != ? ORDER BY created_at DESC", (me["tg_id"],)).fetchall()
        liked = {r[0] for r in c.execute("SELECT to_id FROM likes WHERE from_id=? AND is_like=1", (me["tg_id"],))}
        skipped = dict(c.execute("SELECT to_id, created_at FROM likes WHERE from_id=? AND is_like=0 "
                                 "AND created_at > datetime('now', ?)", (me["tg_id"], f"-{db.SKIP_DAYS} days")).fetchall())
    print(f"Вы: {me['name']} id={me['tg_id']} пол={me['gender'] or '—'} ищет={me['seek'] or '—'} "
          f"цели={','.join(db.goals_of(me))} active={me['active']} анкета={'да' if db.is_dating(me) else 'НЕТ'}")
    print(f"Лента «{goal}». Всего других людей: {len(users)}\n")
    ok = 0
    for u in users:
        r = reasons(me, u, goal, liked, skipped)
        ok += not r
        print(f"{'✅' if not r else '❌'} {u['created_at'][:16]}  {u['name']} @{u['username'] or '—'}"
              + ("" if not r else "  ← " + "; ".join(r)))
    print(f"\nМогут появиться в ленте: {ok}")


if __name__ == "__main__":
    main()
