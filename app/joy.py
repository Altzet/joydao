"""JOY energy — внутренняя «энергия» сообщества. Фаза 1: начисления.

Баланс — сумма записей журнала joy_ledger (отдельной таблицы балансов нет).
Суммы в mJOY (1 JOY = 1000), чтобы не ловить ошибки округления float.
Одна награда за одно событие — UNIQUE(user_id, reason) в базе.
Вывод в джеттон на TON — фаза 3 (см. документ «JOY energy — токеномика»).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from . import db, suisai
from .i18n import T

UNIT = 1000           # 1 JOY в mJOY
ONBOARD = 1 * UNIT    # вход в сообщество
QUIZ = 50             # верный ответ «Числа дня» — 0,05 JOY
STREAK = 300          # 7 дней подряд — 0,3 JOY, раз в неделю
HOLD_DAYS = 3         # антифарм: 1 JOY через 3 дня после регистрации или после первой взаимной симпатии
PASS = 7              # верных ответов из 9 в тесте принципов

# Уровень — по ЗАРАБОТАННОМУ (траты его не уменьшают, купить статус нельзя)
LEVELS = [(0, "Новичок"), (1, "Искра"), (10, "Пламя"), (50, "Сияние"), (200, "Солнце")]

# 9 цифр — 9 энергий — 9 принципов клуба. Тест учит, а не экзаменует: после ответа
# показываем «почему». Формат: (энергия, вопрос, варианты, индекс верного, почему)
PRINCIPLES = [
    (1, "Ты лайкнул(а) человека, а взаимности нет. Что дальше?",
     ["Найти его в общем чате и написать там", "Отпустить: в ленте каждый день новые люди",
      "Попросить общего знакомого замолвить слово"], 1,
     "Сила — во власти над собой, а не над другим. Симпатия в JoyDao только взаимная."),
    (2, "Ты пишешь первым после взаимной симпатии. С чего начать?",
     ["Одно «Привет» — как всем", "Зацепиться за что-то из его анкеты или портрета",
      "Сразу комплимент внешности"], 1,
     "Психология — это внимание к человеку. Вопрос о его мире открывает больше, чем дежурное «привет»."),
    (3, "У человека низкий % совместимости с тобой. Как к этому относиться?",
     ["Пропустить: цифры не врут", "Это повод для интереса, а не приговор",
      "Рассказать ему, какие у него «слабые» энергии"], 1,
     "Анализ — инструмент, а не судья. Нумерология у нас — игра и повод для рефлексии."),
    (4, "Друг дал тебе свою дату рождения для разбора. Можно выложить его в общий чат?",
     ["Да, это же просто цифры", "Только с его согласия", "Да, если убрать имя"], 1,
     "Знаниями делятся бережно. Дата рождения — личные данные, по ней человека легко узнать."),
    (5, "В чате с тобой резко не согласились. Как ответить?",
     ["Спорить о мнении, а не о человеке", "Доказать свою правоту любой ценой",
      "Промолчать, а потом обсудить его в личке с другими"], 0,
     "Борьба хороша за идеи. Человека уважаем, даже когда не согласны."),
    (6, "Какое фото поставить в анкету?",
     ["Самое удачное, пусть ему и пять лет", "Своё недавнее, где хорошо видно лицо",
      "Красивое с фильтрами — главное, чтобы цепляло"], 1,
     "Творчество — быть собой в лучшем виде, а не маской. На встрече тебя ждут настоящего."),
    (7, "Собеседник пишет то, от чего тебе некомфортно. Что делать?",
     ["Терпеть, чтобы не показаться грубым", "Объяснять ему, почему он неправ, пока не поймёт",
      "Спокойно обозначить границу или заблокировать — объяснять ты не обязан(а)"], 2,
     "Йога — внутренний баланс. Твои границы важнее чужого удобства."),
    (8, "Новый знакомый предлагает купить у него JOY или «вложиться в проект». Что делать?",
     ["Купить немного, если человек приятный", "Сначала узнать подробности",
      "Отказаться и сообщить модератору"], 2,
     "JOY — это труд: его зарабатывают, а не покупают. Деньги и «инвестиции» в знакомствах — признак мошенничества."),
    (9, "Новичок задаёт в чате «наивный» вопрос. Как поступить?",
     ["Подсказать или дать ссылку на ответ", "Промолчать — пусть сам разбирается",
      "Пошутить над ним, чтобы было веселее"], 0,
     "Служение — сила сообщества. Каждый из нас когда-то был новичком."),
]


# --- журнал ---

def _award(c, uid: int, amount: int, reason: str) -> bool:
    """Начислить один раз за событие. True — если начислено сейчас."""
    return c.execute("INSERT OR IGNORE INTO joy_ledger (user_id, delta, reason) VALUES (?,?,?)",
                     (uid, amount, reason)).rowcount == 1


def totals(uid: int) -> tuple[int, int]:
    """(баланс, всего заработано) в mJOY."""
    with db.connect() as c:
        r = c.execute("SELECT COALESCE(SUM(delta),0) b, COALESCE(SUM(MAX(delta,0)),0) e "
                      "FROM joy_ledger WHERE user_id=?", (uid,)).fetchone()
    return r["b"], r["e"]


def level(earned: int) -> tuple[str, int | None]:
    """Название уровня и порог следующего (в JOY) — или None на максимуме."""
    joy = earned / UNIT
    name, nxt = LEVELS[0][1], None
    for i, (need, title) in enumerate(LEVELS):
        if joy >= need:
            name = title
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return name, nxt


# --- членство и первый JOY ---

def is_member(user) -> bool:
    return bool(user and "member_at" in user.keys() and user["member_at"])


def pass_test(uid: int, answers: list[int]) -> tuple[bool, int]:
    """Проверить тест принципов. Возвращает (сдан, верных ответов)."""
    right = sum(1 for (_, _, _, ok, _), a in zip(PRINCIPLES, answers) if a == ok)
    passed = right >= PASS
    if passed:
        with db.connect() as c:
            c.execute("UPDATE users SET member_at=datetime('now') WHERE tg_id=? AND COALESCE(member_at,'')=''",
                      (uid,))
    return passed, right


def review(answers: list[int], lang: str = "ru") -> list[dict]:
    """Разбор теста: по каждому вопросу — верно ли и «почему» (тест учит, ответы не секрет)."""
    return [{"ok": a == ok, "why": T(why, lang)} for (_, _, _, ok, why), a in zip(PRINCIPLES, answers)]


def onboard_status(user) -> str:
    """need_test | need_profile | pending | done — где человек на пути к первому JOY."""
    with db.connect() as c:
        if c.execute("SELECT 1 FROM joy_ledger WHERE user_id=? AND reason='onboarding'",
                     (user["tg_id"],)).fetchone():
            return "done"
    if not is_member(user):
        return "need_test"
    if not db.is_dating(user):  # полная анкета с фото (фото проходит премодерацию)
        return "need_profile"
    return "pending"


def try_onboard(uid: int) -> bool:
    """Выдать 1 JOY, если условия выполнены. Идемпотентно; True — начислено сейчас."""
    user = db.get_user(uid)
    if not user or onboard_status(user) != "pending":
        return False
    with db.connect() as c:
        old = c.execute("SELECT 1 FROM users WHERE tg_id=? AND created_at <= datetime('now', ?)",
                        (uid, f"-{HOLD_DAYS} days")).fetchone()
    if not (old or db.get_matches(uid)):
        return False
    with db.connect() as c:
        return _award(c, uid, ONBOARD, "onboarding")


def hold_until(user) -> str:
    """Дата, когда придёт первый JOY без взаимной симпатии (ДД.ММ.ГГГГ)."""
    return (date.fromisoformat(user["created_at"][:10]) + timedelta(days=HOLD_DAYS)).strftime("%d.%m.%Y")


# --- «Число дня» ---

def daily_question(day: date, lang: str = "ru") -> dict:
    """Вопрос дня, одинаковый для всех. Ответ считается из нумерологии — писать контент не нужно."""
    rnd = random.Random(day.toordinal())
    names = suisai.ENERGY_NAMES
    missing = suisai.missing_energies(day)
    kind = day.toordinal() % 3
    if kind == 2 and (not missing or len(names) - len(missing) < 2):
        kind = 1  # в дате нет «дыр» или слишком мало энергий для неверных вариантов
    if kind == 0:  # Число Сознания случайной даты
        d = date(1960, 1, 1) + timedelta(days=rnd.randrange(365 * 45))
        right = suisai.consciousness_number(d)
        q = T("Какое Число Сознания у человека, родившегося {date}?", lang, date=d.strftime("%d.%m.%Y"))
        wrong = rnd.sample([n for n in range(1, 10) if n != right], 2)
        opts = [str(right)] + [str(n) for n in wrong]
    elif kind == 1:  # какая энергия у цифры
        n = rnd.randrange(1, 10)
        q = T("Какую энергию несёт цифра {n}?", lang, n=n)
        wrong = rnd.sample([e for e in names if e != n], 2)
        opts = [T(names[n], lang)] + [T(names[e], lang) for e in wrong]
    else:  # чего нет в сегодняшней дате
        right = rnd.choice(missing)
        present = [e for e in names if e not in missing]
        q = T("Какой энергии нет в сегодняшней дате {date}?", lang, date=day.strftime("%d.%m.%Y"))
        wrong = rnd.sample(present, 2)
        opts = [T(names[right], lang)] + [T(names[e], lang) for e in wrong]
    order = [0, 1, 2]
    rnd.shuffle(order)
    return {"q": q, "options": [opts[i] for i in order], "answer": order.index(0)}


def quiz_answer(uid: int, day: date) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT choice, correct FROM joy_answers WHERE user_id=? AND day=?",
                      (uid, day.isoformat())).fetchone()
    return dict(r) if r else None


def answer_quiz(uid: int, choice: int, day: date | None = None) -> dict | None:
    """Ответ на вопрос дня. None — уже отвечал сегодня. Иначе {correct, answer, joy (mJOY начислено)}."""
    day = day or date.today()
    q = daily_question(day)
    correct = choice == q["answer"]
    with db.connect() as c:
        if c.execute("INSERT OR IGNORE INTO joy_answers (user_id, day, choice, correct) VALUES (?,?,?,?)",
                     (uid, day.isoformat(), choice, int(correct))).rowcount == 0:
            return None
        got = 0
        if correct and _award(c, uid, QUIZ, f"quiz:{day.isoformat()}"):
            got += QUIZ
        # серия: отвечал (верно или нет) 7 дней подряд, включая сегодня
        days = {r[0] for r in c.execute(
            "SELECT day FROM joy_answers WHERE user_id=? AND day > ?",
            (uid, (day - timedelta(days=7)).isoformat()))}
        if all((day - timedelta(days=i)).isoformat() in days for i in range(7)):
            y, w, _ = day.isocalendar()
            if _award(c, uid, STREAK, f"streak:{y}-W{w}"):
                got += STREAK
    return {"correct": correct, "answer": q["answer"], "joy": got}


def streak(uid: int, day: date | None = None) -> int:
    """Сколько дней подряд человек отвечает на вопрос дня (сегодня можно ещё не ответить)."""
    day = day or date.today()
    with db.connect() as c:
        days = {r[0] for r in c.execute("SELECT day FROM joy_answers WHERE user_id=?", (uid,))}
    d = day if day.isoformat() in days else day - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


# --- всё для экрана ---

def state(uid: int, lang: str = "ru") -> dict:
    """Состояние JOY для Mini App. Заодно выдаёт первый JOY, если пришло время."""
    new = try_onboard(uid)
    user = db.get_user(uid)
    bal, earned = totals(uid)
    name, nxt = level(earned)
    status = onboard_status(user)
    out = {"balance": bal / UNIT, "earned": earned / UNIT, "level": T(name, lang), "next": nxt,
           "status": status, "new_award": ONBOARD / UNIT if new else 0, "streak": streak(uid)}
    if status == "pending":
        out["hold_until"] = hold_until(user)
    if not is_member(user):
        out["test"] = [{"e": e, "q": T(q, lang), "options": [T(o, lang) for o in opts]}
                       for e, q, opts, _, _ in PRINCIPLES]
    else:
        today = date.today()
        q = daily_question(today, lang)
        ans = quiz_answer(uid, today)
        out["quiz"] = {"q": q["q"], "options": q["options"], "reward": QUIZ / UNIT,
                       "answered": {**ans, "answer": q["answer"]} if ans else None}
    return out


if __name__ == "__main__":  # самопроверка без базы: вопросы дня корректны весь год
    for i in range(400):
        d = date(2026, 1, 1) + timedelta(days=i)
        q = daily_question(d)
        assert len(set(q["options"])) == 3 and 0 <= q["answer"] < 3, (d, q)
    print("ok")
