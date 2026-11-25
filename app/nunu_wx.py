"""Погодное семя: дело «под открытым небом» с условиями погоды и своей точкой.
Раз в день вечером сервер смотрит прогноз на завтра (Open-Meteo, без ключа) и, если условия сошлись,
бот пишет: «🌱 По просьбе. Завтра +18 и сухо 😎» + строчка из банка + [Сделаю] [Не сейчас].

Второй режим — «Предупредить» (18.11): ночью до N°, жара от N°, будет дождь/снег, сильный ветер.
Например «затопить котёл, если ночью до +5». Тоже один раз на «окно» (подряд идущие такие дни).

Наука: погода — «если» в плане «если — то» (Gollwitzer & Sheeran 2006); подсказка в момент возможности
(JITAI, Nahum-Shani et al. 2018). Принципы игры: без счётчиков пропусков, «Не сейчас» без вины,
одно сообщение на одно погодное «окно», шутим про погоду, а не про человека.
Банк фраз: claude/nunu-weather-phrases.md (проект «Интервью»).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from . import nunu

log = logging.getLogger("nunu.wx")
HOUR_UTC = 17  # 19:00 в Киеве зимой: решать про завтра вечером накануне
DRY_MM, DRY_PROB, CALM_KMH = 0.5, 30, 20  # ponytail: пороги «сухо» и «без ветра»; подстроить по отзывам
WET_MM, WET_PROB, WINDY_KMH = 1, 60, 40  # «будет дождь/снег» и «сильный ветер» для предупреждений
WARN = ("tn", "hot", "wet", "windy")  # условия режима «Предупредить»
LAST = 10  # строчка не повторяется в последних N сообщениях игрока

JOKES = [
    "Семечко ворочается в земле: говорит, завтра его день 🌱",
    "Синоптики иногда ошибаются. Но завтра они на твоей стороне 😎",
    "Прогноз: 100% вероятность хорошего настроения ☀",
    "Тучи взяли выходной ☁🏖", "Тучи покинули чат 🚪☁", "Небо своё сделало. Дальше твой ход 🎲",
    "Это знак 👀", "Звёзды сошлись. И тучи разошлись ✨", "Погода дала зелёный свет 🟢",
    "Завтра даже коты пойдут гулять 🐈", "Пчёлы уже на работе. Говорят, на улице отлично 🐝",
    "Солнце забронировало для тебя денёк ☀📅", "Ветер ушёл в отпуск 🪁", "Никто: … Погода завтра: ☀☀☀",
    "Ожидание: дождь. Реальность: солнце 😎", "POV: завтра ты на улице, и всё получается ☀",
    "Когда прогноз наконец за тебя 🤝", "Погода завтра — 10/10, рекомендую ⭐",
    "Это не баг, это фича: завтра сухо 🐞➡☀", "Загрузка хорошего дня… ██████████ 100%",
    "Ачивка разблокирована: идеальная погода 🏆", "Сохраняйте спокойствие и идите на улицу 👑",
    "Солнце: «я тебя услышало» ☀", "Кто-то: а погода будет? Завтра: держи ☀",
    # пословицы и классика
    "Нет плохой погоды — есть неподходящая одежда 🧥",
    "Лучшее время посадить дерево было 20 лет назад. Следующее — сейчас 🌳",
    "Суши сено, пока светит солнце 🌾", "Лови момент ☀", "После ненастья — вёдро ☀",
    "Тепло и солнце — день чудесный! ☀",
]
BY_WORD = [  # строчки «под дело»: если в названии есть одно из слов
    (("пицц", "печ", "тест"), ["Тесто в тепле поднимается быстрее — наука за тебя 🍕", "Печь соскучилась 🔥",
                               "Печь: *разминает кирпичи* 🔥"]),
    (("пицц",), ["Синоптики: +18. Пицца: я готова 🍕", "POV: запах пиццы долетел до соседей 🍕👃"]),
    (("сад", "грядк", "посад", "посе", "огород"), ["Земля прогрелась — настоящие семена тоже рады 🌱",
                                                  "Червяки уже вышли на смену 🪱"]),
    (("друг", "вмест", "позв"), ["Хорошая погода — лучший повод позвать кого-то 🤝",
                                 "Скриншот для друга: «завтра солнце, ты в деле?» 📸"]),
    (("прогул", "лес", "гор", "поход", "гриб"), ["Тропинки сухие, виды бесплатные 🥾"]),
    (("велос", "бег", "пробеж"), ["Ветер попутный. Ну, почти 🚲"]),
]
WARM = [  # строчки для предупреждений: уют, а не тревога
    "Тёплый дом — лучший план на вечер 🏠", "Плед уже ждёт 🧣", "Чайник тоже готов 🫖", "Береги тепло — и себя ❄🏠",
    "Зима не спрашивает. А ты уже готов 😎", "Предупреждён — значит, в тепле 🔥", "Погода своё, а у тебя план 📋",
]
WARN_WORD = [
    (("котел", "котёл", "топ", "печ", "камин"), ["Котёл: *разминает трубы* 🔥", "Дрова уже шепчутся 🪵🔥",
                                                  "Пора, печка соскучилась 🔥"]),
    (("полив", "поли"), ["Небо польёт за тебя 🌧", "Лейка может отдохнуть 🌧"]),
    (("укры", "плён", "плен", "рассад", "теплиц"), ["Растения скажут спасибо 🌱🧣", "Укутаем зелёных 🌱"]),
    (("машин", "авто", "двор", "бельё", "белье"), ["Лучше убрать заранее, чем бегать потом ☔"]),
]
YES = ["Делу время 🙌", "Договорились. Солнце в курсе ☀", "Записано в саду 🌱",
       "Принято! Тучам передам, чтоб не мешали ☁🚫"]
NO = ["Ждём у моря погоды 🏝", "Всему своё время 🌤", "Семечко не торопится. И ты не торопись 🌱",
      "Прогноз переменчив — хороший день ещё будет 🌦", "Тише едешь — дальше будешь 🐢",
      "Окей, семечко досыпает 😴🌱"]


def seasonal(day: date, f: dict) -> list[str]:
    """Сезонные строчки для завтрашнего дня day по прогнозу f."""
    out, m = [], day.month
    if f.get("tmax", 99) <= 0 and f.get("sun"):
        out.append("Мороз и солнце; день чудесный! ❄☀")
    if m in (3, 4, 5) and f.get("tmax", 0) >= 15:
        out += ["Весна идёт, весна идёт! 🌷", "Весна пришла без предупреждения 🌷"]
    if m in (9, 10, 11) and f.get("tmax", 0) >= 15:
        out.append("Бабье лето заглянуло 🍂")
    if day.weekday() == 3 and f.get("rain_before"):
        out.append("Тот самый дождичек в четверг случился! 😄")
    return out


def pick(pool: list[str], last: list[str], rnd=random) -> str:
    """Случайная строчка, которой не было в последних LAST сообщениях (если всё было — любая)."""
    fresh = [x for x in pool if x not in last[-LAST:]] or pool
    return rnd.choice(fresh)


def warn(wx: dict) -> bool:
    return any(wx.get(k) not in (None, "", False) for k in WARN)


def lines_for(what: str, day: date, f: dict, alert: bool = False) -> list[str]:
    w = what.lower()
    if alert:
        return WARM + [x for keys, xs in WARN_WORD if any(k in w for k in keys) for x in xs]
    return JOKES + [x for keys, xs in BY_WORD if any(k in w for k in keys) for x in xs] + seasonal(day, f)


def fits(wx: dict, f: dict) -> bool:
    """Сошлись ли все выбранные условия с прогнозом f на завтра."""
    if warn(wx):  # «Предупредить»: все выбранные угрозы сошлись
        return ((wx.get("tn") in (None, "") or f.get("tnight", 99) <= float(wx["tn"]))
                and (wx.get("hot") in (None, "") or f["tmax"] >= float(wx["hot"]))
                and (not wx.get("wet") or f.get("wet", False)) and (not wx.get("windy") or f.get("windy", False)))
    return ((not wx.get("dry") or f["dry"]) and (not wx.get("sun") or f["sun"]) and (not wx.get("calm") or f["calm"])
            and (wx.get("t") in (None, "") or f["tmax"] >= float(wx["t"])))


def sg(t: float) -> str:
    t = round(t)
    return f"{'+' if t > 0 else ''}{t}"


def alert_head(wx: dict, f: dict) -> str:
    """«Сегодня ночью до +3, завтра снег ❄🌨»."""
    p, e = [], ""
    if wx.get("tn") not in (None, ""):
        p.append(f"сегодня ночью до {sg(f['tnight'])}")
        e += "❄" if f["tnight"] <= 0 else "🌡"
    if wx.get("hot") not in (None, ""):
        p.append(f"завтра жара {sg(f['tmax'])}")
        e += "🔥"
    if wx.get("wet"):
        p.append("завтра снег" if f.get("snow") else "завтра дождь")
        e += "🌨" if f.get("snow") else "🌧"
    if wx.get("windy"):
        p.append(f"завтра ветер до {round(f.get('wind', 0))} км/ч")
        e += "💨"
    t = ", ".join(p)
    return t[:1].upper() + t[1:] + " " + e


def headline(wx: dict, f: dict) -> str:
    """«Завтра +18 и сухо 😎»: только то, что игрок заказал, и эмодзи по погоде."""
    t = round(f["tmax"])
    words = [w for k, w in (("sun", "солнечно"), ("dry", "сухо"), ("calm", "без ветра")) if wx.get(k)]
    if wx.get("sun") and wx.get("dry"):
        words.remove("сухо")  # «солнечно и сухо» — масло масляное
    e = "😎" if f["sun"] else "🪁" if wx.get("calm") and not wx.get("dry") else "🙌"
    tail = (", ".join(words[:-1]) + " и " + words[-1]) if len(words) > 1 else (words[0] if words else "")
    sep = " и " if len(words) == 1 else ", "  # «+18 и сухо», «+18, солнечно и без ветра»
    return f"Завтра {'+' if t > 0 else ''}{t}{sep + tail if tail else ''} {e}"


def wx_pic(wx: dict, f: dict, uid: int, pid: str):
    """Картинка к погодному сообщению: своё фото игрока к делу, иначе по прогнозу (холст «Картинки бота»)."""
    own = nunu.seed_pic(uid, pid)
    if own:
        return own
    if warn(wx):
        return "frost"
    return "snow" if f.get("snow") else "rain" if f.get("wet") else "sun"


def message(seed: dict, f: dict, day: date, last: list[str], rnd=random) -> tuple[str, str]:
    wx = seed["wx"]
    a = warn(wx)
    line = pick(lines_for(wx.get("what") or seed.get("r", ""), day, f, a), last, rnd)
    where = f" · 📍 {wx['where']}" if wx.get("where") else ""
    return f"{'🔔' if a else '🌱'} По просьбе. {alert_head(wx, f) if a else headline(wx, f)}\n«{wx.get('what') or seed.get('r', '')}»{where}\n{line}", line


def forecast(ll: str) -> dict | None:
    """Прогноз по точке «lat, lon» (или ссылке Google Карт): на завтра tmax, dry, sun, calm, wet, snow, windy, wind;
    tnight — минимум этой ночи (20:00 сегодня — 08:00 завтра по местному времени); rain_before — дождь сегодня."""
    xy = nunu._coords(ll)
    if not xy:
        return None
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={xy[0]:.2f}&longitude={xy[1]:.2f}&timezone=auto"
           "&forecast_days=2&daily=temperature_2m_max,precipitation_sum,precipitation_probability_max,"
           "wind_speed_10m_max,weather_code&hourly=temperature_2m")
    j = json.loads(nunu._get(url)[1])
    d, hours = j["daily"], [x for x in (j.get("hourly") or {}).get("temperature_2m", [])[20:33] if x is not None]
    v = lambda k, i=1: d[k][i] if d.get(k) and d[k][i] is not None else 0  # noqa: E731
    return {"tmax": v("temperature_2m_max"), "dry": v("precipitation_sum") < DRY_MM
            and v("precipitation_probability_max") <= DRY_PROB, "sun": v("weather_code") <= 2,
            "calm": v("wind_speed_10m_max") <= CALM_KMH, "rain_before": v("precipitation_sum", 0) >= DRY_MM,
            "wet": v("precipitation_sum") >= WET_MM or v("precipitation_probability_max") >= WET_PROB,
            "snow": v("weather_code") in (71, 73, 75, 77, 85, 86), "windy": v("wind_speed_10m_max") >= WINDY_KMH,
            "wind": v("wind_speed_10m_max"), "tnight": min(hours) if hours else v("temperature_2m_max")}


def due(today: date, get=forecast) -> list[tuple[int, str, str]]:
    """Кому написать сегодня вечером про завтра: (tg_id, текст, pid). Окно — подряд идущие подходящие дни:
    про одно окно пишем один раз; отметки в таблице reminders (kind wx:<pid> = день, про который написали)."""
    out, tomorrow, cache = [], today + timedelta(days=1), {}
    with closing(nunu._db()) as c, c:
        for r in c.execute("SELECT tg_id, data FROM state").fetchall():
            try:
                d = json.loads(r["data"])
                seeds = [s for s in (d.get("nunu-seeds") or []) if isinstance(s, dict) and s.get("wx") and not s.get("life")]
                seeds += [{"pid": g.get("id"), "r": g.get("t", ""), "wx": g["wx"]} for g in (d.get("nunu-goals") or [])
                          if isinstance(g, dict) and g.get("wx") and not g.get("done") and not g.get("gone")]  # «Моя цель» тоже
            except Exception:
                continue
            if (d.get("nunu-prefs") or {}).get("remind") is False:
                continue
            uid = r["tg_id"]
            lr = c.execute("SELECT at FROM reminders WHERE tg_id=? AND kind='wxlast'", (uid,)).fetchone()
            last = json.loads(lr["at"]) if lr else []
            for s in seeds:
                ll, kind = str(s["wx"].get("ll") or ""), "wx:" + str(s.get("pid") or "")[:40]
                if not ll or not s.get("pid"):
                    continue
                if ll not in cache:
                    try:
                        cache[ll] = get(ll)
                    except Exception as e:
                        log.info("Прогноз %s: %s", ll, e)
                        cache[ll] = None
                f = cache[ll]
                if not f or not fits(s["wx"], f):
                    continue
                prev = c.execute("SELECT at FROM reminders WHERE tg_id=? AND kind=?", (uid, kind)).fetchone()
                c.execute("INSERT INTO reminders VALUES(?,?,?) ON CONFLICT(tg_id, kind) DO UPDATE SET at=excluded.at",
                          (uid, kind, tomorrow.isoformat()))
                if prev and prev["at"] in (today.isoformat(), tomorrow.isoformat()):
                    continue  # то же окно: вчера уже писали про сегодня
                text, line = message(s, f, tomorrow, last)
                last = (last + [line])[-LAST:]
                out.append((uid, text, s["pid"], wx_pic(s["wx"], f, uid, s["pid"])))
            c.execute("INSERT INTO reminders VALUES(?, 'wxlast', ?) ON CONFLICT(tg_id, kind) DO UPDATE SET at=excluded.at",
                      (uid, json.dumps(last, ensure_ascii=False)))
    return out


async def send(bot, uid: int, text: str, pid: str, pic="sun") -> None:
    from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Сделаю", callback_data=f"wx:y:{pid}"),
        InlineKeyboardButton(text="Не сейчас", callback_data=f"wx:n:{pid}")]])
    f = pic if isinstance(pic, nunu.Path) else nunu.PICS / f"{pic}.png"
    try:
        if f.exists() and len(text) <= 1024:
            await bot.send_photo(uid, FSInputFile(f), caption=text, reply_markup=kb)
        else:
            await bot.send_message(uid, text, reply_markup=kb)
    except Exception as e:
        log.info("Не отправил погоду %s: %s", uid, e)


def answer(data: str, rnd=random) -> str:
    """Ответ на кнопку: «Сделаю» или «Не сейчас» — без вины и без счётчиков."""
    return pick(YES if data.startswith("wx:y:") else NO, [], rnd)


async def loop(bot) -> None:
    """Раз в сутки в HOUR_UTC. Прогноз тянется в потоке, чтобы не держать бота."""
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=HOUR_UTC, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            for uid, text, pid, pic in await asyncio.to_thread(due, datetime.now(timezone.utc).date()):
                await send(bot, uid, text, pid, pic)
                await asyncio.sleep(0.05)
        except Exception as e:
            log.warning("Погода: %s", e)
