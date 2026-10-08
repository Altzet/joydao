"""Агент-контентщик канала: генерирует ежедневный пост через Anthropic API.

Ротация форматов по дню года: энергия дня → разбор Числа Сознания → совместимость
пары чисел. К каждому посту рендерится фирменная карточка-изображение (card.py).
Черновик уходит в админ-чат на премодерацию (✅/🔄/❌); если админ-чат не задан —
публикуется сразу. Термин «Сюцай» в публикациях не используется.
"""
from __future__ import annotations

import json as _json
import random
from datetime import date, datetime

import aiohttp

from . import card, config, db, suisai

API_URL = "https://api.anthropic.com/v1/messages"

# Все неупорядоченные пары чисел 1..9, включая одинаковые (45 комбинаций)
_PAIRS = [(a, b) for a in range(1, 10) for b in range(a, 10)]

_STYLE = (
    "Ты — редактор телеграм-канала «Код твоей души | JoyDao» о цифровой нумерологии "
    "и отношениях. Пиши радостно, весело и с озорством — как друг, который знает про числа всё "
    "и любит пошутить: лёгкий юмор, живые бытовые примеры, игривые эмодзи. "
    "Без пафоса, эзотерического жаргона и сарказма в адрес читателя. "
    "Объём поста: 600–1100 знаков.\n\n"
    "Структура (для визуальной привлекательности в Telegram):\n"
    "1. Цепляющий первый абзац 1–2 предложения — вопрос или наблюдение (без подзаголовка).\n"
    "2. 2–3 смысловых блока. Каждый блок начинается с новой строки в формате: "
    "эмодзи-маркер + <b>короткий подзаголовок.</b> + 2–3 предложения. "
    "Эмодзи-маркеры подбирай по смыслу (⚡ 💛 🗝 🌊 🎯 ✨ и т.п.), не повторяй один и тот же.\n"
    "3. Финальная мысль одной строкой, можно курсивом через <i>…</i>.\n\n"
    "Разметка: только теги <b>…</b> и <i>…</i>; символы < и > вне тегов не используй; "
    "выдели жирным 2–4 ключевые фразы внутри блоков. Между абзацами — пустая строка. "
    "Без хэштегов, без заголовка в кавычках.\n"
    "НИКОГДА не используй слово «Сюцай»; не обещай гарантий и не ставь «диагнозов» — "
    "подавай как повод для рефлексии.\n"
    "Не добавляй в конце ссылок, упоминаний бота и призывов — "
    "строку-приглашение с меченой ссылкой добавит система."
)

KINDS = ("day", "chs", "pair")
EXPLORE = 0.2        # доля дней, когда пробуем не лучший формат (чтобы не застрять)
MIN_OPENS = 20       # пока переходов меньше — просто ротация, данных мало


def cta(kind: str) -> str:
    """Строка-приглашение с меткой src_ch_<формат>: по ней считаем, какой формат приводит людей."""
    return (f'\n\n🔮 <a href="https://t.me/{config.BOT_USERNAME}?start=src_ch_{kind}">'
            f"Узнай свой код души бесплатно</a>")


def pick_kind(d: date, conv: dict | None = None, rnd: float | None = None) -> str:
    """Самонастройка контента (epsilon-greedy): чаще публикуем формат, который
    лучше конвертирует переходы из канала в пользователей, иногда — пробуем другие."""
    rotation = KINDS[d.timetuple().tm_yday % 3]
    conv = db.source_conversion("ch_") if conv is None else conv
    if sum(o for o, _ in conv.values()) < MIN_OPENS:
        return rotation
    if (random.random() if rnd is None else rnd) < EXPLORE:
        return rotation

    def rate(k: str) -> float:  # сглаживание: (дошли+1)/(перешли+2)
        opened, done = conv.get(f"ch_{k}", (0, 0))
        return (done + 1) / (opened + 2)
    return max(KINDS, key=rate)


def _today_topic(d: date | None = None, kind: str | None = None) -> dict:
    """Тема дня: формат выбирает pick_kind, число/пара — детерминированно по дню года."""
    d = d or date.today()
    doy = d.timetuple().tm_yday
    kind = kind or pick_kind(d)
    if kind == "day":
        digits = f"{d.day:02d}{d.month:02d}{d.year}"
        day_num = suisai.reduce_number(sum(int(x) for x in digits))
        return {"kind": "day", "num": day_num, "date": d.strftime("%d.%m.%Y")}
    if kind == "chs":
        n = doy % 9 + 1  # по дню, а не по циклу: формат может повторяться подряд
        return {"kind": "chs", "num": n}
    a, b = _PAIRS[doy % len(_PAIRS)]
    return {"kind": "pair", "a": a, "b": b}


def balance(d: date) -> str:
    """Баланс энергий даты: каждая цифра — энергия, повтор её усиливает,
    отсутствующие — то, что день просит добрать осознанно. Нули не трактуются."""
    st, on, ms = suisai.energy_groups(d)
    nm = lambda e: f"{e} ({suisai.ENERGY_NAMES[e]})"  # noqa: E731
    strong = ", ".join(f"{nm(e)} ×{c}" for e, c in st) or "нет"
    one = ", ".join(nm(e) for e in on) or "нет"
    miss = ", ".join(nm(e) for e in ms) or "нет — все энергии на месте"
    return f"усилены: {strong}; присутствуют: {one}; отсутствуют: {miss}"


def _prompt_for(topic: dict) -> str:
    if topic["kind"] == "day":
        n = topic["num"]
        d = datetime.strptime(topic["date"], "%d.%m.%Y").date()
        hidden = ("" if suisai.matrix(d)[n] else
                  f" Числа {n} нет среди цифр даты — оно появляется только как итог: "
                  f"обыграй это как энергию, которая приходит к концу дня.")
        return (
            f"Напиши пост «Энергия дня» на {topic['date']}. Число дня (сумма всех цифр) — {n} "
            f"({suisai.CHS_SHORT[n]}).{hidden}\n"
            f"Каждая цифра даты — отдельная энергия. Баланс даты: {balance(d)}.\n"
            f"Структура: крючок → блок про число дня → блок «Баланс дня»: что в избытке "
            f"(этим сегодня легко пользоваться, но не перегни) и чего нет (добери осознанно "
            f"маленьким конкретным действием) → 1–2 совета для общения и отношений. "
            f"Нули не трактуй."
        )
    if topic["kind"] == "chs":
        n = topic["num"]
        days = ", ".join(str(x) for x in (n, n + 9, n + 18, n + 27) if x <= 31)
        source = suisai.TEXTS["chsDescription"][n - 1][:1500]
        return (
            f"Напиши пост-разбор «Число Сознания {n}» (у тех, кто родился {days} числа "
            f"любого месяца). Краткая суть: {suisai.CHS_SHORT[n]}. "
            f"Опорный материал (перескажи бережно и позитивно, резкие формулировки смягчи, "
            f"термин «Сюцай» игнорируй):\n{source}\n\n"
            f"Структура: крючок → сильные стороны → что даёт энергию, а что разрушает → "
            f"совет для отношений."
        )
    a, b = topic["a"], topic["b"]
    return (
        f"Напиши пост о совместимости чисел сознания {a} и {b} в паре. "
        f"{a} — {suisai.CHS_SHORT[a]}. {b} — {suisai.CHS_SHORT[b]}. "
        f"Расскажи, чем эти энергии притягиваются, где возможны трения и один совет, "
        f"как паре {a}+{b} беречь отношения. Тон — «оба хороши, вопрос в осознанности», "
        f"никаких «вам не суждено»."
    )


async def _ask(system: str, prompt: str, max_tokens: int = 1024) -> str:
    """Один запрос к Anthropic Messages API."""
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(API_URL, json=payload, headers=headers,
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(f"Anthropic API {r.status}: {data}")
            return data["content"][0]["text"].strip()


async def generate_post(topic: dict | None = None) -> str:
    """Текст поста + меченая строка-приглашение."""
    topic = topic or _today_topic()
    text = await _ask(_STYLE, _prompt_for(topic))
    return text[:3400] + cta(topic.get("kind", "day"))  # запас под лимит 4096


def card_url(topic: dict) -> str:
    """Публичный URL карточки (эндпоинт /card.png в api.py)."""
    base = f"{config.WEBAPP_URL}/card.png"
    k = topic.get("kind", "day")
    if k == "chs":
        return f"{base}?kind=chs&num={topic['num']}"
    if k == "pair":
        return f"{base}?kind=pair&a={topic['a']}&b={topic['b']}"
    return f"{base}?kind=day&num={topic['num']}&d={topic.get('date', '')}"


async def send_post(bot, chat_id, text: str, topic: dict, reply_markup=None, prefix: str = ""):
    """Пост = текст (лимит 4096) с крупной карточкой-превью сверху.

    Так текст никогда не обрезается, а выглядит как фото с подписью.
    Если WEBAPP_URL не задан — фолбэк на фото с подписью (лимит 1024).
    """
    body = f"{prefix}{text}"
    if config.WEBAPP_URL:
        from aiogram.types import LinkPreviewOptions
        opts = LinkPreviewOptions(url=card_url(topic), prefer_large_media=True,
                                  show_above_text=True)
        try:
            return await bot.send_message(chat_id, body, parse_mode="HTML",
                                          link_preview_options=opts, reply_markup=reply_markup)
        except Exception:
            return await bot.send_message(chat_id, body, link_preview_options=opts,
                                          reply_markup=reply_markup)
    from aiogram.types import BufferedInputFile
    photo = BufferedInputFile(card.render(topic), filename="joydao.png")
    try:
        return await bot.send_photo(chat_id, photo, caption=body[:1024],
                                    parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        return await bot.send_photo(chat_id, photo, caption=body[:1024], reply_markup=reply_markup)


# --- Черновики ---

def _drafts_conn():
    c = db.connect()
    c.execute("""CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        topic TEXT DEFAULT '{}',
        status TEXT DEFAULT 'pending',   -- pending | published | skipped
        created_at TEXT DEFAULT (datetime('now')))""")
    try:  # миграция, если таблица создана старой версией без topic
        c.execute("ALTER TABLE drafts ADD COLUMN topic TEXT DEFAULT '{}'")
    except Exception:
        pass
    return c


def save_draft(text: str, topic: dict) -> int:
    with _drafts_conn() as c:
        cur = c.execute("INSERT INTO drafts (text, topic) VALUES (?, ?)",
                        (text, _json.dumps(topic)))
        return cur.lastrowid


def get_draft(draft_id: int) -> dict | None:
    with _drafts_conn() as c:
        row = c.execute("SELECT text, topic FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            return None
        return {"text": row["text"], "topic": _json.loads(row["topic"] or "{}")}


def update_draft(draft_id: int, text: str | None = None, status: str | None = None) -> None:
    with _drafts_conn() as c:
        if text is not None:
            c.execute("UPDATE drafts SET text=? WHERE id=?", (text, draft_id))
        if status is not None:
            c.execute("UPDATE drafts SET status=? WHERE id=?", (status, draft_id))


def moderation_kb(draft_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"agent:pub:{draft_id}"),
        InlineKeyboardButton(text="🔄 Переделать", callback_data=f"agent:redo:{draft_id}"),
        InlineKeyboardButton(text="❌ Пропустить", callback_data=f"agent:skip:{draft_id}"),
    ]])


async def daily_run(bot) -> None:
    """Ежедневный запуск: черновик в админ-чат или сразу в канал."""
    if not (config.ANTHROPIC_API_KEY and config.CHANNEL_ID):
        return
    topic = _today_topic()
    text = await generate_post(topic)
    if config.ADMIN_CHAT_ID:
        draft_id = save_draft(text, topic)
        await send_post(bot, config.ADMIN_CHAT_ID, text, topic,
                        reply_markup=moderation_kb(draft_id),
                        prefix=f"📝 Черновик для {config.CHANNEL_ID}:\n\n")
    else:
        await send_post(bot, config.CHANNEL_ID, text, topic)


# --- Еженедельный growth-отчёт: агент смотрит на воронку и предлагает гипотезы ---

_ANALYST = (
    "Ты — growth-аналитик телеграм-сервиса знакомств JoyDao (портрет и совместимость "
    "по дате рождения, лента пар, парные ссылки «проверь нашу совместимость»). "
    "По цифрам недели дай: 1) одну фразу — где главная потеря; 2) три конкретные "
    "гипотезы, что изменить на следующей неделе, у каждой — метрика успеха. "
    "Опирайся только на цифры, не выдумывай данные. Только теги <b> и <i>. До 900 знаков."
)


def report_numbers() -> str:
    f = db.funnel(7)
    lines = ["Воронка за 7 дней (уникальные люди): "
             + ", ".join(f"{k}={v}" for k, v in f.items())]
    conv = db.source_conversion("", days=7)
    if conv:
        lines.append("Источники (перешли→дошли до даты): "
                     + ", ".join(f"{k}: {o}→{d}" for k, (o, d) in conv.items()))
    with db.connect() as c:
        total = c.execute("SELECT COUNT(*), SUM(active) FROM users").fetchone()
    lines.append(f"Всего пользователей: {total[0]}, с анкетой знакомств: {total[1] or 0}")
    return "\n".join(lines)


async def weekly_report(bot) -> None:
    """Раз в неделю — цифры + гипотезы в админ-чат. Решение о внедрении — за человеком."""
    if not config.ADMIN_CHAT_ID:
        return
    numbers = report_numbers()
    advice = ""
    if config.ANTHROPIC_API_KEY:
        try:
            advice = "\n\n" + await _ask(_ANALYST, numbers, max_tokens=700)
        except Exception as e:
            advice = f"\n\n(анализ недоступен: {e})"
    text = f"📊 <b>Недельный отчёт JoyDao</b>\n\n{numbers}{advice}"
    try:
        await bot.send_message(config.ADMIN_CHAT_ID, text, parse_mode="HTML")
    except Exception:  # модель могла вернуть битую разметку
        await bot.send_message(config.ADMIN_CHAT_ID, text)
