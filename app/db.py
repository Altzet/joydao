"""Слой БД. SQLite для MVP; интерфейс изолирован — миграция на PostgreSQL
сводится к замене этого модуля."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "suisai.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    birth_date TEXT NOT NULL,           -- ISO YYYY-MM-DD
    gender     TEXT NOT NULL,           -- m | f
    seek       TEXT NOT NULL,           -- m | f | any
    city       TEXT DEFAULT '',
    bio        TEXT DEFAULT '',
    photo_id   TEXT DEFAULT '',         -- Telegram file_id
    active     INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS likes (
    from_id INTEGER NOT NULL,
    to_id   INTEGER NOT NULL,
    is_like INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id)
);
CREATE TABLE IF NOT EXISTS shown (
    user_id      INTEGER NOT NULL,
    candidate_id INTEGER NOT NULL,
    day          TEXT NOT NULL,         -- ISO date показа
    PRIMARY KEY (user_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS thanks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount  INTEGER NOT NULL,           -- Telegram Stars
    message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id  INTEGER NOT NULL,
    referred_id  INTEGER NOT NULL UNIQUE,
    status       TEXT DEFAULT 'pending',   -- pending | confirmed
    created_at   TEXT DEFAULT (datetime('now')),
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS referral_bonus (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    bonus   INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    event      TEXT NOT NULL,             -- start | birth | pair_open | pair_done | profile_done | like | match
    meta       TEXT DEFAULT '',           -- источник, id пригласившего и т.п.
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS events_ev_ts ON events(event, created_at);
CREATE TABLE IF NOT EXISTS signups_source (
    tg_id      INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,             -- метка из /start src_<источник>
    created_at TEXT DEFAULT (datetime('now'))
);
"""

REFERRAL_DAILY_CAP = 5

# Колонки, добавленные после первого релиза (ALTER идемпотентен через try)
_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN daily_opt INTEGER DEFAULT 1",
    # Цели в сообществе: love,friend,business (через запятую). Старые анкеты — знакомства.
    "ALTER TABLE users ADD COLUMN goals TEXT DEFAULT 'love'",
    "ALTER TABLE shown ADD COLUMN goal TEXT DEFAULT 'love'",
    "ALTER TABLE users ADD COLUMN lang TEXT DEFAULT ''",  # язык интерфейса (пусто — как в Telegram)
]

GOALS = ("love", "friend", "business")


def goals_of(user) -> list[str]:
    return [g for g in (user["goals"] or "love").split(",") if g in GOALS] if user else []
_migrated = False


def connect() -> sqlite3.Connection:
    global _migrated
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not _migrated:
        conn.executescript(SCHEMA)
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        _migrated = True
    return conn


def is_dating(user) -> bool:
    """Анкета в сообществе заполнена (пол, фото; «кого ищу» — только для цели «любовь»).
    До этого человек — «лёгкий» пользователь: портрет и проверки доступны, ленты «Люди» — нет."""
    if not (user and user["gender"] and user["photo_id"]):
        return False
    return bool(user["seek"]) or "love" not in goals_of(user)


def upsert_user(tg_id: int, **fields) -> None:
    with connect() as c:
        cols = ", ".join(fields)
        ph = ", ".join("?" * len(fields))
        upd = ", ".join(f"{k}=excluded.{k}" for k in fields)
        c.execute(
            f"INSERT INTO users (tg_id, {cols}) VALUES (?, {ph}) "
            f"ON CONFLICT(tg_id) DO UPDATE SET {upd}",
            (tg_id, *fields.values()),
        )


def update_user(tg_id: int, **fields) -> None:
    """Обновить поля существующего пользователя (upsert с неполным набором полей
    в SQLite падает на NOT NULL ещё до ON CONFLICT)."""
    with connect() as c:
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE users SET {sets} WHERE tg_id=?", (*fields.values(), tg_id))


def delete_user(tg_id: int) -> None:
    """Удалить аккаунт и все следы в ленте/лайках/рефералах. Платежи-благодарности
    остаются для учёта, но обезличиваются."""
    with connect() as c:
        c.execute("DELETE FROM users WHERE tg_id=?", (tg_id,))
        c.execute("DELETE FROM likes WHERE from_id=? OR to_id=?", (tg_id, tg_id))
        c.execute("DELETE FROM shown WHERE user_id=? OR candidate_id=?", (tg_id, tg_id))
        c.execute("DELETE FROM referrals WHERE referrer_id=? OR referred_id=?", (tg_id, tg_id))
        c.execute("DELETE FROM referral_bonus WHERE user_id=?", (tg_id,))
        c.execute("DELETE FROM signups_source WHERE tg_id=?", (tg_id,))
        c.execute("DELETE FROM events WHERE user_id=?", (tg_id,))
        c.execute("UPDATE thanks SET user_id=0, message='' WHERE user_id=?", (tg_id,))


def get_user(tg_id: int) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()


def daily_candidates(tg_id: int, limit: int = 5, rank=None, goal: str = "love") -> list[sqlite3.Row]:
    """Кандидаты дня: показанные сегодня + добор новых до лимита (+ реферальный бонус).
    Реферальные пары (пригласивший/приглашённый) друг другу не показываются.
    rank(row) -> число: добор берёт лучших по нему (по умолчанию — случайно)."""
    me = get_user(tg_id)
    if not me:
        return []
    limit += bonus_today(tg_id)
    today = date.today().isoformat()
    excluded = referral_partners(tg_id)
    excl_cond = f"AND u.tg_id NOT IN ({','.join('?' * len(excluded))})" if excluded else ""
    # Лента по цели: человек попадает в ленту «дружба», только если сам её выбрал.
    # Пол и «кого ищу» учитываются только для любви.
    params: list = [tg_id, tg_id, tg_id, f"%,{goal},%"]
    if goal == "love":
        seek = me["seek"]
        gender_cond = ("AND (u.seek = 'any' OR u.seek = (SELECT gender FROM users WHERE tg_id=?))"
                       + ("" if seek == "any" else " AND u.gender = ?"))
        params.append(tg_id)
        if seek != "any":
            params.append(seek)
    else:
        gender_cond = ""
    params += excluded

    with connect() as c:
        shown_today = c.execute(
            f"""SELECT u.* FROM shown s JOIN users u ON u.tg_id = s.candidate_id
                WHERE s.user_id=? AND s.day=? AND s.goal=? AND u.active=1
                AND u.tg_id NOT IN (SELECT to_id FROM likes WHERE from_id=?)""",
            (tg_id, today, goal, tg_id),
        ).fetchall()
        need = limit - len(shown_today)
        fresh: list[sqlite3.Row] = []
        if need > 0:
            fresh = c.execute(
                f"""SELECT u.* FROM users u
                    WHERE u.tg_id != ? AND u.active = 1
                    AND u.tg_id NOT IN (SELECT candidate_id FROM shown WHERE user_id=?)
                    AND u.tg_id NOT IN (SELECT to_id FROM likes WHERE from_id=?)
                    AND (',' || COALESCE(u.goals, 'love') || ',') LIKE ?
                    {gender_cond}
                    {excl_cond}
                    ORDER BY RANDOM()""",
                params,
            ).fetchall()
            # ponytail: ранжируем всех подходящих в Python — O(N) на запрос,
            # хватит до ~10k анкет; дальше — предрасчёт ЧС/миссии в колонки и SQL-фильтр.
            if rank:
                fresh.sort(key=rank, reverse=True)
            fresh = fresh[:need]
            c.executemany(
                "INSERT OR IGNORE INTO shown (user_id, candidate_id, day, goal) VALUES (?,?,?,?)",
                [(tg_id, r["tg_id"], today, goal) for r in fresh],
            )
        return list(shown_today) + fresh


def was_shown(user_id: int, candidate_id: int) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM shown WHERE user_id=? AND candidate_id=?",
                         (user_id, candidate_id)).fetchone() is not None


def set_like(from_id: int, to_id: int, is_like: bool) -> bool:
    """Сохранить симпатию. Возвращает True, если это взаимный матч.

    Защита от случайного разрыва: «пропустить» не перезаписывает уже
    поставленный лайк — иначе один ошибочный тап уничтожал бы матч.
    Отменить осознанно можно кнопкой «Вернуть» (undo_last_like)."""
    with connect() as c:
        existing = c.execute(
            "SELECT is_like FROM likes WHERE from_id=? AND to_id=?", (from_id, to_id)
        ).fetchone()
        if not is_like and existing and existing["is_like"] == 1:
            # уже лайкнут — игнорируем случайный «пропуск», сохраняем симпатию
            mutual = c.execute(
                "SELECT 1 FROM likes WHERE from_id=? AND to_id=? AND is_like=1",
                (to_id, from_id),
            ).fetchone()
            return mutual is not None
        c.execute(
            "INSERT OR REPLACE INTO likes (from_id, to_id, is_like) VALUES (?,?,?)",
            (from_id, to_id, int(is_like)),
        )
        if not is_like:
            return False
        row = c.execute(
            "SELECT 1 FROM likes WHERE from_id=? AND to_id=? AND is_like=1",
            (to_id, from_id),
        ).fetchone()
        return row is not None


def undo_last_like(tg_id: int) -> int | None:
    """Отменить последнее действие (лайк/пропуск): удаляет запись из likes и shown,
    чтобы кандидат снова появился в ленте. Возвращает tg_id восстановленного."""
    with connect() as c:
        row = c.execute(
            "SELECT to_id FROM likes WHERE from_id=? ORDER BY rowid DESC LIMIT 1", (tg_id,)
        ).fetchone()
        if not row:
            return None
        to_id = row["to_id"]
        c.execute("DELETE FROM likes WHERE from_id=? AND to_id=?", (tg_id, to_id))
        c.execute("DELETE FROM shown WHERE user_id=? AND candidate_id=?", (tg_id, to_id))
        return to_id


def get_matches(tg_id: int) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute(
            """SELECT u.* FROM users u WHERE u.tg_id IN (
                 SELECT l1.to_id FROM likes l1
                 JOIN likes l2 ON l2.from_id = l1.to_id AND l2.to_id = l1.from_id
                 WHERE l1.from_id=? AND l1.is_like=1 AND l2.is_like=1)""",
            (tg_id,),
        ).fetchall()


def add_thanks(user_id: int, amount: int, message: str = "") -> None:
    with connect() as c:
        c.execute("INSERT INTO thanks (user_id, amount, message) VALUES (?,?,?)",
                  (user_id, amount, message))


def thanks_wall(limit: int = 20) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute(
            """SELECT t.amount, t.message, t.created_at, u.name FROM thanks t
               JOIN users u ON u.tg_id = t.user_id
               ORDER BY t.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


# --- Реферальная программа ---

def create_referral(referrer_id: int, referred_id: int) -> None:
    """Зафиксировать переход по реферальной ссылке (до завершения анкеты)."""
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?,?)",
            (referrer_id, referred_id),
        )


def confirm_referral(referred_id: int) -> int | None:
    """Подтвердить реферал при завершении анкеты приглашённым и начислить бонус
    обоим на сегодня (капается REFERRAL_DAILY_CAP). Возвращает id пригласившего, если реферал был."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM referrals WHERE referred_id=? AND status='pending'", (referred_id,)
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE referrals SET status='confirmed', confirmed_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        today = date.today().isoformat()
        for uid in (row["referrer_id"], referred_id):
            c.execute(
                """INSERT INTO referral_bonus (user_id, day, bonus) VALUES (?,?,1)
                   ON CONFLICT(user_id, day) DO UPDATE SET bonus = MIN(bonus + 1, ?)""",
                (uid, today, REFERRAL_DAILY_CAP),
            )
        return row["referrer_id"]


def bonus_today(tg_id: int) -> int:
    """Сколько дополнительных кандидатов начислено пользователю сегодня."""
    with connect() as c:
        r = c.execute(
            "SELECT bonus FROM referral_bonus WHERE user_id=? AND day=?",
            (tg_id, date.today().isoformat()),
        ).fetchone()
        return r["bonus"] if r else 0


def referral_partners(tg_id: int) -> list[int]:
    """Люди, связанные с этим пользователем реферальной программой — исключаются из подбора."""
    with connect() as c:
        rows = c.execute(
            "SELECT referrer_id, referred_id FROM referrals WHERE referrer_id=? OR referred_id=?",
            (tg_id, tg_id),
        ).fetchall()
        out = {r["referrer_id"] for r in rows} | {r["referred_id"] for r in rows}
        out.discard(tg_id)
        return list(out)


def referral_count(tg_id: int) -> int:
    """Сколько друзей по ссылке этого пользователя подтвердили анкету."""
    with connect() as c:
        r = c.execute(
            "SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='confirmed'",
            (tg_id,),
        ).fetchone()
        return r["c"]


# --- Метки источника (UTM-подобная атрибуция каналов продвижения) ---

def record_source(tg_id: int, source: str) -> None:
    """Зафиксировать, из какого канала пришёл новый пользователь (/start src_<источник>)."""
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO signups_source (tg_id, source) VALUES (?,?)",
            (tg_id, source[:50]),
        )


def source_stats() -> list[sqlite3.Row]:
    """Сколько переходов и сколько завершённых анкет дал каждый источник."""
    with connect() as c:
        return c.execute(
            """SELECT s.source AS source, COUNT(*) AS opened,
                      SUM(CASE WHEN u.tg_id IS NOT NULL THEN 1 ELSE 0 END) AS completed
               FROM signups_source s LEFT JOIN users u ON u.tg_id = s.tg_id
               GROUP BY s.source ORDER BY opened DESC"""
        ).fetchall()


# --- События воронки (основа самообучения и недельного отчёта) ---

def log_event(user_id: int, event: str, meta: str = "") -> None:
    with connect() as c:
        c.execute("INSERT INTO events (user_id, event, meta) VALUES (?,?,?)",
                  (user_id, event, str(meta)[:100]))


FUNNEL = ["start", "birth", "pair_open", "pair_done", "profile_done", "like", "match"]


def funnel(days: int = 7) -> dict[str, int]:
    """Уникальные пользователи на каждом шаге воронки за N дней."""
    with connect() as c:
        rows = c.execute(
            """SELECT event, COUNT(DISTINCT user_id) n FROM events
               WHERE created_at >= datetime('now', ?) GROUP BY event""",
            (f"-{days} days",),
        ).fetchall()
    got = {r["event"]: r["n"] for r in rows}
    return {e: got.get(e, 0) for e in FUNNEL}


def source_conversion(prefix: str = "", days: int = 60) -> dict[str, tuple[int, int]]:
    """Источник → (переходов, дошли до даты рождения). Для выбора тем постов."""
    with connect() as c:
        rows = c.execute(
            """SELECT s.source, COUNT(*) opened,
                      SUM(CASE WHEN u.tg_id IS NOT NULL THEN 1 ELSE 0 END) done
               FROM signups_source s LEFT JOIN users u ON u.tg_id = s.tg_id
               WHERE s.source LIKE ? AND s.created_at >= datetime('now', ?)
               GROUP BY s.source""",
            (prefix + "%", f"-{days} days"),
        ).fetchall()
    return {r["source"]: (r["opened"], r["done"] or 0) for r in rows}
