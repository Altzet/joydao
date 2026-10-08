"""Точка входа: бот (polling), FastAPI и планировщики в одном процессе.

Запуск:  python -m app.main
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import uvicorn
from aiogram import Bot, Dispatcher

from . import agent, api, config, mailing
from .bot import router

log = logging.getLogger("joydao")


async def every_day_at(hour: int, job, bot: Bot, weekday: int | None = None) -> None:
    """Запускать job(bot) раз в сутки в hour (время сервера, UTC);
    weekday (0 = понедельник) — только в этот день недели."""
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        if weekday is not None and datetime.now().weekday() != weekday:
            continue
        try:
            await job(bot)
        except Exception:
            log.exception("Ошибка задачи %s", job.__name__)


BOT_DESCRIPTION = (  # исходник для переводов; русское описание задаётся вручную в @BotFather
    "👥 JoyDao — сообщество людей, которым интересно, что скрывает дата рождения.\n\n"
    "🎂 Твоё число и баланс энергий: в чём сила, что прокачать\n"
    "☀️ Каждое утро — энергия дня (ни разу одинаково!)\n"
    "💞 Совместимость в любви, 🤝 дружбе и 💼 бизнесе\n"
    "👨‍👩‍👧 Кто с кем сработается в команде или семье\n"
    "🔍 Разгадай дату друга, коллеги или бывшего 👀\n"
    "✨ Люди на одной волне: пара, друзья, партнёры\n\n"
    "Жми /start, вводи дату — магия за 5 секунд 🪄\n"
    "Бесплатно и навсегда."
)
BOT_SHORT_DESCRIPTION = ("Сообщество по коду даты рождения: энергия дня и совместимость "
                         "в любви, дружбе и бизнесе 👥✨ Бесплатно")


async def set_translated_descriptions(bot: Bot) -> None:
    """Описание бота для пользователей с другим языком Telegram. Русское (по умолчанию)
    не трогаем — оно задаётся в @BotFather."""
    from .i18n import SUPPORTED, T
    for lang in SUPPORTED:
        if lang != "ru":
            await bot.set_my_description(T(BOT_DESCRIPTION, lang)[:512], language_code=lang)
            await bot.set_my_short_description(T(BOT_SHORT_DESCRIPTION, lang)[:120], language_code=lang)


async def setup_menu(bot: Bot) -> None:
    """Кнопка «JoyDao» слева от поля ввода открывает приложение в один тап
    (с initData — в отличие от reply-клавиатуры). Список команд по «/» убираем.
    Описание бота задаётся вручную в @BotFather, код его не трогает."""
    from aiogram.types import MenuButtonWebApp, WebAppInfo
    try:
        await bot.delete_my_commands()  # меню команд не нужно — всё в приложении
        await set_translated_descriptions(bot)
        if config.WEBAPP_URL:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                text="JoyDao", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    except Exception:
        log.exception("Не удалось настроить меню бота")  # не мешаем запуску


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    api.set_bot(bot)
    await setup_menu(bot)

    server = uvicorn.Server(uvicorn.Config(api.app, host=config.HOST, port=config.PORT, log_level="info"))
    jobs = [
        dp.start_polling(bot),
        server.serve(),
        every_day_at(config.MAILING_HOUR, mailing.daily_broadcast, bot),
        every_day_at((config.POST_HOUR + 1) % 24, agent.weekly_report, bot, weekday=0),
    ]
    if config.ANTHROPIC_API_KEY and config.CHANNEL_ID:
        jobs.append(every_day_at(config.POST_HOUR, agent.daily_run, bot))
    else:
        log.info("Агент-контентщик выключен (нет ANTHROPIC_API_KEY или CHANNEL_ID)")
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено.")
