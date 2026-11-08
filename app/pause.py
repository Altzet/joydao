"""Бот «Ну-ну» — обычный режим JoyDAO: в личке зовёт в игру, сам ставит себе имя, «О боте»,
описание и кнопку меню «Играть»; игра — /nunu, её сервер — app/nunu.py.
Старый JoyDao (знакомства, нумерология, рассылки, агент канала, турниры) — LEGACY=1 в .env.
ponytail: файл называется pause.py по истории — переименовать, когда LEGACY уйдёт совсем."""
from __future__ import annotations

import asyncio
import html
import logging
from urllib.parse import urlsplit
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message,
                           WebAppInfo)
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from . import config, db, nunu

TEXT = (
    "🏝 <b>Ну-ну</b> — игра о том, насколько хорошо вы знаете друг друга.\n\n"
    "Твой остров растёт из ответов, а мост к другу крепнет, когда вы точно понимаете друг друга: "
    "от канатного до каменного.\n\n"
    "Жми «Играть» и позови первого друга 👇"
)
GAME_URL = f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}"  # Direct Link: игра откроется с аккаунтом
CHANNEL_URL = f"https://t.me/{config.CHANNEL_ID.lstrip('@')}" if config.CHANNEL_ID.startswith("@") else "https://t.me/joydao"

KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🏝 Играть", url=GAME_URL)],
    [InlineKeyboardButton(text="📢 Канал", url=CHANNEL_URL)],
    [InlineKeyboardButton(text="💬 Общий чат", url=config.COMMUNITY_CHAT_URL)],
])

router = Router()


@router.message(Command("announce"))
async def announce(msg: Message):
    """Админ: разослать анонс всем пользователям один раз.
    Работает из админ-чата (ADMIN_CHAT_ID) или из лички админа, если там его user id."""
    admin = str(config.ADMIN_CHAT_ID)
    if not admin or admin not in (str(msg.chat.id), str(msg.from_user.id)):
        if msg.chat.type == "private":
            await msg.answer(TEXT, parse_mode="HTML", reply_markup=KB)
        return  # в группах молчим
    with db.connect() as c:
        ids = [r["tg_id"] for r in c.execute("SELECT tg_id FROM users").fetchall()]
    sent = 0
    for uid in ids:
        try:
            await msg.bot.send_message(uid, TEXT, parse_mode="HTML", reply_markup=KB)
            sent += 1
        except Exception:
            pass  # закрыл личку боту
        await asyncio.sleep(0.04)  # лимит Telegram ~30 сообщений/с
    await msg.answer(f"Анонс отправлен: {sent} из {len(ids)}")


# Только личка: бот — админ в общем чате и канале, там отвечать нельзя.
@router.message(F.chat.type == "private")
async def any_message(msg: Message):
    await msg.answer(TEXT, parse_mode="HTML", reply_markup=KB)


@router.callback_query()
async def any_callback(cb: CallbackQuery):
    await cb.answer()
    if cb.message and cb.message.chat.type == "private":  # старые кнопки из лички
        await cb.message.answer(TEXT, parse_mode="HTML", reply_markup=KB)


app = FastAPI()
app.include_router(nunu.router)  # до catch-all внизу
CHAT_URL = config.COMMUNITY_CHAT_URL
# Страница Mini App: светлая карточка в стиле промо, не зависит от темы Telegram.
# Ссылки t.me открываем через Telegram.WebApp — иначе WebView грузит их внутри себя.
PAGE = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px 16px;
 font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1A0F1C;
 background:radial-gradient(120% 80% at 10% 0%,#FFE4EC 0%,transparent 60%),
 radial-gradient(100% 70% at 100% 100%,#EFE4FF 0%,transparent 60%),#FFF7FA}}
.card{{width:100%;max-width:420px;background:#fff;border-radius:24px;padding:32px 24px 24px;
 box-shadow:0 12px 40px rgba(123,63,228,.14);text-align:center;animation:up .5s ease-out both}}
.ico{{font-size:56px;line-height:1;animation:fly 2.4s ease-in-out infinite}}
h1{{font-size:22px;line-height:1.25;margin:16px 0 12px;font-weight:800}}
h1 span{{background:linear-gradient(90deg,#7B3FE4,#FF5FA2);-webkit-background-clip:text;background-clip:text;color:transparent}}
p{{font-size:15px;line-height:1.55;color:#4A3F4D;margin:0 0 12px}}
a.btn{{display:block;padding:15px;border-radius:14px;font-weight:700;font-size:16px;text-decoration:none;margin-top:10px;transition:transform .1s}}
a.btn:active{{transform:scale(.97)}}
.main{{background:linear-gradient(90deg,#7B3FE4,#FF5FA2);color:#fff}}
.sec{{background:#F3EEFB;color:#5B2DB5}}
@keyframes up{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:none}}}}
@keyframes fly{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-6px)}}}}
</style></head><body><div class="card">
<div class="ico">🚀</div>
<h1>JoyDAO готовит <span>грандиозное обновление</span></h1>
<p>Мы ненадолго поставили бот на паузу и перестраиваем всё вокруг главного — живой игры.</p>
<p>Подпишитесь на канал — там первыми узнают о запуске 👇</p>
<a class="btn main" href="{html.escape(CHANNEL_URL)}">📢 Подписаться на канал</a>
<a class="btn sec" href="{html.escape(CHAT_URL)}">💬 Общий чат</a>
</div><script>
var tg=window.Telegram&&Telegram.WebApp;
if(tg){{tg.ready();tg.expand();try{{tg.setHeaderColor("#FFF7FA");tg.setBackgroundColor("#FFF7FA")}}catch(e){{}}
 document.querySelectorAll("a.btn").forEach(function(a){{a.onclick=function(e){{e.preventDefault();tg.openTelegramLink(a.href)}}}})}}
</script></body></html>"""


# Маршрут /nunu обязан стоять до catch-all ниже, иначе игру перехватит заглушка.
@app.get("/nunu")
async def nunu():
    """Игра «Ну-ну» работает и на паузе. Файл читается при каждом запросе: обновлять без перезапуска."""
    return FileResponse(Path(__file__).resolve().parent.parent / "static" / "nunu.html")


@app.get("/{path:path}")
async def page(path: str = ""):
    return HTMLResponse(PAGE)


log = logging.getLogger("joydao")
# Профиль бота. Картинки (аватарка, обложка описания) API не ставит — только @BotFather.
NAME = "Ну-ну · JoyDao"
ABOUT = "Игра об островах и мостах: проверь, насколько хорошо вы с друзьями знаете друг друга 🌉"
DESCRIPTION = (
    "🏝 Ну-ну — игра о том, насколько хорошо вы знаете друг друга.\n\n"
    "• Отвечаешь на карточки о себе и о друге\n"
    "• Твой остров растёт из ответов: цветы, деревья, камни\n"
    "• Поняли друг друга — мост крепнет: от канатного до каменного\n"
    "• Своя ситуация: расскажи случай, а друг угадает, как ты поступил\n"
    "• Режим компании: одна ссылка или QR-код на всех\n\n"
    "Жми «Играть» 👇"
)


async def setup_profile(bot: Bot) -> None:
    """Имя, «О боте», описание и кнопка меню — из кода. Меняем, только если отличается:
    у setMyName жёсткий лимит частоты."""
    try:
        if (await bot.get_my_name()).name != NAME:
            await bot.set_my_name(NAME)
        if (await bot.get_my_short_description()).short_description != ABOUT:
            await bot.set_my_short_description(ABOUT)
        if (await bot.get_my_description()).description != DESCRIPTION:
            await bot.set_my_description(DESCRIPTION)
        await bot.delete_my_commands()
        if config.WEBAPP_URL:  # кнопка слева от поля ввода: игра с аккаунтом в один тап
            u = urlsplit(config.WEBAPP_URL)
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                text="Играть", web_app=WebAppInfo(url=f"{u.scheme}://{u.netloc}/nunu")))
    except Exception:
        log.exception("Не удалось обновить профиль бота")  # не мешаем запуску
    await clear_old_descriptions(bot)


async def clear_old_descriptions(bot: Bot) -> None:
    """Старые описания знакомств на uk/en/… перекрывали бы новое из @BotFather у людей с этими языками.
    Пустая строка снимает языковое описание — все видят то, что задано в @BotFather."""
    from .i18n import REMOVED, SUPPORTED
    for lang in (*SUPPORTED, *REMOVED):
        try:
            await bot.set_my_description("", language_code=lang)
            await bot.set_my_short_description("", language_code=lang)
        except Exception:
            pass  # не мешаем запуску


async def main() -> None:
    bot = Bot(token=config.BOT_TOKEN)
    await setup_profile(bot)
    dp = Dispatcher()
    dp.include_router(router)
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, log_level="info"))
    tasks = [asyncio.create_task(dp.start_polling(bot)), asyncio.create_task(server.serve())]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
