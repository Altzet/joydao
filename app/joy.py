"""JOY energy — внутренняя «энергия» сообщества. Фаза 1: начисления.

Баланс — сумма записей журнала joy_ledger (отдельной таблицы балансов нет).
Суммы в mJOY (1 JOY = 1000), чтобы не ловить ошибки округления float.
Одна награда за одно событие — UNIQUE(user_id, reason) в базе.
Вывод в джеттон на TON — фаза 3 (см. документ «JOY energy — токеномика»).
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from . import db, suisai
from .i18n import T

UNIT = 1000           # 1 JOY в mJOY
ONBOARD = 1 * UNIT    # вход в сообщество
QUIZ = 50             # верный ответ «Числа дня» — 0,05 JOY
STREAK = 300          # 7 дней подряд — 0,3 JOY, раз в неделю
HOLD_DAYS = 3         # антифарм: 1 JOY через 3 дня после регистрации или после первой взаимной симпатии
PASS = 7              # верных ответов из 9 в тесте принципов

# Траты (фаза 2). 20% каждой траты считаются сожжёнными, 80% — вернувшимися в пул наград:
# это учёт для on-chain фазы, в базе трата — просто отрицательная запись журнала.
SPARK = 100           # «Искра» — 0,1 JOY: усиленный лайк, ты первый в ленте получателя
BOOST = 300           # буст анкеты — 0,3 JOY на 24 часа
REPORT = 500          # глубокий разбор пары — 0,5 JOY, один раз на человека
CELEB = 200           # разбор звезды + совместимость с ней — 0,2 JOY, один раз на звезду
BOOST_HOURS = 24
BOOST_RANK = 15       # сколько «процентов созвучия» добавляет буст при ранжировании ленты

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


# --- траты ---

class NotEnough(Exception):
    """Не хватает JOY на трату."""


def spend(uid: int, amount: int, reason: str) -> bool:
    """Списать JOY. True — списано сейчас, False — уже было куплено (тот же reason).
    NotEnough — баланса не хватает. Проверка и списание — в одной транзакции."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")  # параллельные траты одного человека не уведут баланс в минус
        if c.execute("SELECT 1 FROM joy_ledger WHERE user_id=? AND reason=?", (uid, reason)).fetchone():
            return False
        bal = c.execute("SELECT COALESCE(SUM(delta),0) FROM joy_ledger WHERE user_id=?", (uid,)).fetchone()[0]
        if bal < amount:
            raise NotEnough
        return _award(c, uid, -amount, reason)


def sparked_me(uid: int) -> set[int]:
    """Кто отправил этому человеку «Искру»."""
    with db.connect() as c:
        return {r[0] for r in c.execute("SELECT user_id FROM joy_ledger WHERE reason=?", (f"spend:spark:{uid}",))}


def boosted() -> set[int]:
    """Чьи анкеты сейчас под бустом."""
    with db.connect() as c:
        return {r[0] for r in c.execute(
            "SELECT DISTINCT user_id FROM joy_ledger WHERE reason LIKE 'spend:boost:%' "
            "AND created_at > datetime('now', ?)", (f"-{BOOST_HOURS} hours",))}


def boost_until(uid: int) -> str | None:
    """До какого времени (UTC, «ГГГГ-ММ-ДД ЧЧ:ММ:СС») действует буст, или None."""
    with db.connect() as c:
        r = c.execute("SELECT datetime(MAX(created_at), ?) u FROM joy_ledger WHERE user_id=? "
                      "AND reason LIKE 'spend:boost:%' HAVING u > datetime('now')",
                      (f"+{BOOST_HOURS} hours", uid)).fetchone()
    return r[0] if r else None


def boost(uid: int) -> str:
    """Включить буст на 24 часа. Повторно, пока действует, — не списываем."""
    if boost_until(uid):
        return boost_until(uid)
    with db.connect() as c:
        stamp = c.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S','now')").fetchone()[0]
    spend(uid, BOOST, f"spend:boost:{stamp}")
    return boost_until(uid)


def has_report(uid: int, other_id: int) -> bool:
    with db.connect() as c:
        return c.execute("SELECT 1 FROM joy_ledger WHERE user_id=? AND reason=?",
                         (uid, f"spend:report:{other_id}")).fetchone() is not None


def cached_report_text(uid: int, other_id: int) -> str | None:
    with db.connect() as c:
        r = c.execute("SELECT text FROM joy_reports WHERE user_id=? AND other_id=?", (uid, other_id)).fetchone()
    return r[0] if r else None


def save_report_text(uid: int, other_id: int, text: str) -> None:
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO joy_reports (user_id, other_id, text) VALUES (?,?,?)",
                  (uid, other_id, text))


REPORT_SYSTEM = (
    "Ты — тёплый и честный консультант сообщества JoyDao по цифровой нумерологии. "
    "Нумерология у нас — игра и повод для рефлексии, а не вердикт: никаких приговоров. "
    "Без эзотерического пафоса, без медицинских и финансовых советов. Пиши на языке: {lang}. "
    "Обращайся к читателю на «ты», второго человека называй по имени."
)


async def deep_report(uid: int, other_id: int, lang: str = "ru") -> dict:
    """Глубокий разбор пары: совместимость в трёх сферах + ИИ-текст (если есть ключ Anthropic).
    ИИ-текст кэшируется — повторное открытие бесплатно и мгновенно.
    Дату рождения второго человека не отдаём: в JoyDao даты друг другу не показываются."""
    from . import agent, config, teaser
    from .i18n import NAMES
    me, other = db.get_user(uid), db.get_user(other_id)
    b1, b2 = date.fromisoformat(me["birth_date"]), date.fromisoformat(other["birth_date"])
    kinds = []
    for k in ("love", "friend", "business"):
        t = teaser.build_compat(b1, b2, k, lang)
        t.pop("date", None)
        kinds.append(t)
    text = cached_report_text(uid, other_id)
    if text is None and config.ANTHROPIC_API_KEY:
        facts = "\n".join(f"{t['kind_label']}: {t['score']}% — {t['verdict']}. "
                          f"Плюсы: {'; '.join(t['pros']) or '—'}. Риски: {'; '.join(t['cons']) or '—'}."
                          for t in kinds)
        prompt = (f"Пара: {me['name']} (Число Сознания {suisai.consciousness_number(b1)}) и "
                  f"{other['name']} (Число Сознания {suisai.consciousness_number(b2)}).\n{facts}\n\n"
                  "Напиши разбор пары на 120–160 слов: в чём их сила вместе, где может искрить "
                  "и один конкретный совет для первого разговора или встречи.")
        try:
            text = await agent._ask(REPORT_SYSTEM.format(lang=NAMES.get(lang, lang)), prompt, 700)
            save_report_text(uid, other_id, text)
        except Exception:
            import logging
            logging.getLogger("joydao.joy").exception("ИИ-разбор пары не удался")
            text = ""  # разбор по трём сферам всё равно есть — JOY потрачен не зря
    return {"name": other["name"], "kinds": kinds, "ai": text or ""}


# --- квиз пары: ледокол после взаимной симпатии ---

PAIR_QUIZ = 200          # +0,2 JOY каждому, когда ответили оба
PAIR_QUIZ_DAILY = 3      # наград за квиз пары в день на человека
PAIR_QUIZ_LEN = 5
# «То или это»: легко ответить, интересно сравнить, есть о чём спросить потом
PAIR_QUESTIONS = [
    ("Идеальное утро", "Кофе в тишине", "Пробежка и музыка"),
    ("Отпуск", "Горы", "Море"),
    ("Вечер пятницы", "Дома с фильмом", "Выйти в люди"),
    ("Планы", "Всё расписано", "Как пойдёт"),
    ("Подарок", "Сюрприз", "То, что просили"),
    ("Спор", "Сразу выяснить", "Остыть и вернуться"),
    ("Кухня", "Готовлю сам(а)", "Знаю лучшие места"),
    ("Новое", "Пробую первым(ой)", "Сначала почитаю отзывы"),
    ("Сообщения", "Голосовые", "Только текст"),
    ("Выходные", "Активно", "Восстанавливаюсь"),
    ("Решения", "Сердцем", "Головой"),
    ("Город мечты", "Шумный мегаполис", "Тихий городок у природы"),
    ("Праздник", "Большая компания", "Самые близкие"),
    ("Сила", "Слова", "Поступки"),
    ("Время суток", "Жаворонок", "Сова"),
]


def pair_questions(a: int, b: int, lang: str = "ru") -> list[dict]:
    """5 вопросов, одинаковых для обоих в паре (зерно — пара id)."""
    lo, hi = sorted((a, b))
    picks = random.Random(lo * 1_000_003 + hi).sample(range(len(PAIR_QUESTIONS)), PAIR_QUIZ_LEN)
    return [{"q": T(PAIR_QUESTIONS[i][0], lang), "a": [T(PAIR_QUESTIONS[i][1], lang), T(PAIR_QUESTIONS[i][2], lang)]}
            for i in picks]


def _pq_row(uid: int, other_id: int):
    with db.connect() as c:
        return c.execute("SELECT self, guess FROM pair_quiz WHERE user_id=? AND other_id=?", (uid, other_id)).fetchone()


def pair_quiz_status(uid: int, other_id: int) -> str:
    """none — никто не ответил, mine — ответил я (ждём второго), theirs — ответил второй, done — оба."""
    mine, theirs = _pq_row(uid, other_id), _pq_row(other_id, uid)
    return "done" if mine and theirs else "mine" if mine else "theirs" if theirs else "none"


def pair_quiz_result(uid: int, other_id: int, lang: str = "ru") -> dict | None:
    """Сравнение ответов, когда ответили оба. Ответы второго видны только после своих — честная игра."""
    mine, theirs = _pq_row(uid, other_id), _pq_row(other_id, uid)
    if not (mine and theirs):
        return None
    ms, mg = [int(x) for x in mine["self"]], [int(x) for x in mine["guess"]]
    ts, tg = [int(x) for x in theirs["self"]], [int(x) for x in theirs["guess"]]
    qs = pair_questions(uid, other_id, lang)
    return {"same": sum(a == b for a, b in zip(ms, ts)),
            "i_guessed": sum(g == t for g, t in zip(mg, ts)),
            "they_guessed": sum(g == m for g, m in zip(tg, ms)),
            "total": PAIR_QUIZ_LEN,
            "items": [{"q": q["q"], "me": q["a"][m], "them": q["a"][t], "same": m == t}
                      for q, m, t in zip(qs, ms, ts)]}


def answer_pair_quiz(uid: int, other_id: int, mine: list[int], guess: list[int]) -> dict:
    """Сохранить ответы (один раз). Когда ответили оба — награда участникам клуба.
    Возвращает {"done": bool, "awarded": [кто получил JOY]}."""
    code = lambda xs: "".join(str(int(bool(x))) for x in xs)  # noqa: E731  ответы 0/1 → строка «01101»
    with db.connect() as c:
        if c.execute("INSERT OR IGNORE INTO pair_quiz (user_id, other_id, self, guess) VALUES (?,?,?,?)",
                     (uid, other_id, code(mine), code(guess))).rowcount == 0:
            return {"done": pair_quiz_status(uid, other_id) == "done", "awarded": [], "again": True}
    if pair_quiz_status(uid, other_id) != "done":
        return {"done": False, "awarded": []}
    lo, hi = sorted((uid, other_id))
    awarded = []
    with db.connect() as c:
        for who in (uid, other_id):
            today = c.execute("SELECT COUNT(*) FROM joy_ledger WHERE user_id=? AND reason LIKE 'pairquiz:%' "
                              "AND date(created_at) = date('now')", (who,)).fetchone()[0]
            if is_member(db.get_user(who)) and today < PAIR_QUIZ_DAILY and \
                    _award(c, who, PAIR_QUIZ, f"pairquiz:{lo}:{hi}"):
                awarded.append(who)
    return {"done": True, "awarded": awarded}


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
        q = T("Какой энергии нет в дате {date}?", lang, date=day.strftime("%d.%m.%Y"))
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


# --- клубный турнир: раз в неделю, на знание, банк — троим лучшим ---

TOUR_ENTRY = 500           # взнос 0,5 JOY
TOUR_LEN = 9               # 9 цифр — 9 вопросов
TOUR_MINUTES = 10          # на прохождение после входа
TOUR_SPLIT = (50, 30, 20)  # % призового фонда за 1–3 место
TOUR_BURN = 10             # % банка сжигается
TOUR_MIN = 3               # меньше финишировавших — всем возврат
TOUR_CLOSE_HOUR = 18       # закрытие: воскресенье 18:00 UTC (21:00 по Киеву)


def tour_week(now: datetime | None = None) -> str:
    """Неделя турнира, например «2026-W41»."""
    y, w, _ = (now or datetime.utcnow()).isocalendar()
    return f"{y}-W{w:02d}"


def tour_closes(week: str) -> datetime:
    y, w = week.split("-W")
    return datetime.fromisocalendar(int(y), int(w), 7).replace(hour=TOUR_CLOSE_HOUR)


def tour_open(week: str, now: datetime | None = None) -> bool:
    return (now or datetime.utcnow()) < tour_closes(week)


def tour_questions(week: str, lang: str = "ru") -> list[dict]:
    """9 вопросов недели, одинаковых для всех: вопросы «Числа дня» для 9 случайных дат."""
    rnd = random.Random(week)
    out, seen = [], set()
    while len(out) < TOUR_LEN:  # без повторов: «какую энергию несёт цифра 3» — только один раз
        q = daily_question(date(1970, 1, 1) + timedelta(days=rnd.randrange(365 * 60)), lang)
        if q["q"] not in seen:
            seen.add(q["q"]); out.append(q)
    return out


def tour_join(uid: int, week: str | None = None) -> bool:
    """Оплатить вход и засечь старт. False — уже участвует."""
    week = week or tour_week()
    if not tour_open(week):
        raise ValueError("closed")
    fresh = spend(uid, TOUR_ENTRY, f"spend:tour:{week}")
    if fresh:
        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO tournament (week, user_id) VALUES (?,?)", (week, uid))
    return fresh


def tour_answer(uid: int, answers: list[int], week: str | None = None) -> dict:
    """Принять ответы (один раз). Опоздал больше чем на 10 минут — 0 баллов."""
    week = week or tour_week()
    qs = tour_questions(week)
    with db.connect() as c:
        r = c.execute("SELECT (julianday('now') - julianday(started_at)) * 86400 sec, score "
                      "FROM tournament WHERE week=? AND user_id=?", (week, uid)).fetchone()
        if not r:
            raise LookupError("not joined")
        if r["score"] is not None:
            return {"score": r["score"], "again": True}
        sec = int(r["sec"])
        score = sum(a == q["answer"] for a, q in zip(answers, qs)) if sec <= TOUR_MINUTES * 60 else 0
        c.execute("UPDATE tournament SET score=?, seconds=? WHERE week=? AND user_id=? AND score IS NULL",
                  (score, sec, week, uid))
    return {"score": score, "seconds": sec}


def tour_board(week: str, limit: int | None = None) -> list[dict]:
    """Таблица финишировавших: больше верных — выше, при равенстве — кто быстрее."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT t.user_id, u.name, t.score, t.seconds FROM tournament t LEFT JOIN users u ON u.tg_id = t.user_id "
            "WHERE t.week=? AND t.score IS NOT NULL ORDER BY t.score DESC, t.seconds ASC, t.user_id"
            + (f" LIMIT {int(limit)}" if limit else ""), (week,)).fetchall()
    return [dict(r) for r in rows]


def tour_info(week: str) -> dict:
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM tournament WHERE week=?", (week,)).fetchone()[0]
    return {"entries": n, "pool": n * TOUR_ENTRY / UNIT}


def settle_tournament(week: str) -> dict:
    """Итоги недели (идемпотентно — повторный запуск ничего не начислит дважды).
    Возвращает {"places": [(uid, приз mJOY)], "refunded": [uid], "board": [...], "entrants": [uid]}."""
    board = tour_board(week)
    with db.connect() as c:
        entrants = [r[0] for r in c.execute("SELECT user_id FROM tournament WHERE week=?", (week,))]
        if len(board) < TOUR_MIN:
            refunded = [u for u in entrants if _award(c, u, TOUR_ENTRY, f"tour:{week}:refund")]
            return {"places": [], "refunded": refunded, "board": board, "entrants": entrants}
        prize = len(entrants) * TOUR_ENTRY * (100 - TOUR_BURN) // 100
        places = []
        for row, pct in zip(board, TOUR_SPLIT):
            amount = prize * pct // 100
            if _award(c, row["user_id"], amount, f"tour:{week}:place"):
                places.append((row["user_id"], amount))
    return {"places": places, "refunded": [], "board": board, "entrants": entrants}


def tour_state(uid: int, lang: str = "ru") -> dict:
    """Турнир для экрана: открыт ли, банк, моё участие, топ-5."""
    week = tour_week()
    with db.connect() as c:
        me = c.execute("SELECT started_at, score, seconds, datetime(started_at, ?) deadline FROM tournament "
                       "WHERE week=? AND user_id=?", (f"+{TOUR_MINUTES} minutes", week, uid)).fetchone()
    board = tour_board(week)
    out = {"week": week, "open": tour_open(week), "closes": tour_closes(week).strftime("%Y-%m-%d %H:%M:%S"),
           "entry": TOUR_ENTRY / UNIT, "len": TOUR_LEN, "minutes": TOUR_MINUTES, **tour_info(week),
           "joined": bool(me), "score": me["score"] if me else None,
           "place": next((i + 1 for i, r in enumerate(board) if r["user_id"] == uid), None),
           "top": [{"name": r["name"] or "—", "score": r["score"], "seconds": r["seconds"]} for r in board[:5]]}
    if me and me["score"] is None:  # вошёл, но ещё не ответил — продолжить с того же места
        out["questions"] = [{"q": q["q"], "options": q["options"]} for q in tour_questions(week, lang)]
        out["deadline"] = me["deadline"]
    return out


# --- рейтинг клуба: по ЗАРАБОТАННОМУ JOY (траты не опускают, купить место нельзя) ---

def rating(uid: int, period: str = "week", limit: int = 10) -> dict:
    """Топ участников клуба по заработанному JOY за неделю (с понедельника, UTC) или за всё время.
    Возвраты взносов турнира — не заработок."""
    since = "date('now', 'weekday 0', '-6 days')" if period == "week" else "'0000-01-01'"
    with db.connect() as c:
        rows = c.execute(
            f"""SELECT l.user_id, u.name, SUM(l.delta) earned FROM joy_ledger l JOIN users u ON u.tg_id = l.user_id
                WHERE l.delta > 0 AND l.reason NOT LIKE '%:refund' AND l.created_at >= {since}
                  AND COALESCE(u.member_at, '') != ''
                GROUP BY l.user_id ORDER BY earned DESC, MIN(l.created_at)""").fetchall()
    board = [{"place": i + 1, "name": r["name"], "joy": r["earned"] / UNIT, "me": r["user_id"] == uid}
             for i, r in enumerate(rows)]
    return {"period": period, "top": board[:limit], "me": next((b for b in board if b["me"]), None),
            "total": len(board)}


# --- всё для экрана ---

def state(uid: int, lang: str = "ru") -> dict:
    """Состояние JOY для Mini App. Заодно выдаёт первый JOY, если пришло время."""
    new = try_onboard(uid)
    user = db.get_user(uid)
    bal, earned = totals(uid)
    name, nxt = level(earned)
    status = onboard_status(user)
    out = {"balance": bal / UNIT, "earned": earned / UNIT, "level": T(name, lang), "next": nxt,
           "status": status, "new_award": ONBOARD / UNIT if new else 0, "streak": streak(uid),
           "dating": db.is_dating(user), "boost_until": boost_until(uid),
           "prices": {"spark": SPARK / UNIT, "boost": BOOST / UNIT, "report": REPORT / UNIT,
                      "celeb": CELEB / UNIT}}
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
