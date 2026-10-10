# JoyDao ✨

**Сообщество людей, которым интересно, что скрывает дата рождения.**
Telegram-бот [@JOYDAO_bot](https://t.me/JOYDAO_bot) + Mini App · канал [@joydao](https://t.me/joydao) · чат [@joydao_club](https://t.me/joydao_club)

*English below.*

- 🎂 Портрет по дате рождения и баланс энергий: что есть, чего не хватает
- ☀️ Энергия дня — каждое утро новый текст, без повторов в течение месяца
- 💞 🤝 💼 Совместимость в любви, дружбе и бизнесе, плюс группы и команды
- 👥 Знакомства по созвучию: несколько людей в день, с объяснением «почему вы совпали»
- 🌐 7 языков: русский, українська, English, 中文, हिन्दी, Español, العربية

> Нумерология здесь — игра и повод для рефлексии, а не научный факт.

## Почему код открыт

Мы работаем с личными данными: датой рождения, фото, симпатиями. Поэтому код открыт, и каждый может проверить, что с ними происходит:

- **Дату рождения не видит никто.** Другим людям показываются только число и процент совместимости ([тест](tests/test_growth.py) `test_pair_result_notifies_both_without_leaking_birth_date`).
- **Фото без метаданных.** Его пережимает Telegram или наш сервер, геометка из файла удаляется. Чужое фото можно получить только по анкете, которую тебе уже показали в ленте (`/api/photo`).
- **Вход только через Telegram.** Каждый запрос Mini App проверяется подписью `initData` (`_auth` в [app/api.py](app/api.py)).
- **Удаление аккаунта** стирает дату, анкету, лайки и взаимные симпатии (`/api/profile/delete`).
- **Никакой рекламы и продажи данных.** Проект живёт на добровольные донаты.

## Запуск

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # впишите BOT_TOKEN и WEBAPP_URL
.venv/bin/python -m app.main
```

Тесты: `pip install pytest httpx && BOT_TOKEN=1:t python -m pytest -q tests`.
Сервер: systemd-юнит и скрипт установки (nginx + certbot) лежат в [deploy/](deploy/): `bash deploy/server-setup.sh ваш.домен`.

## Переводы

Ключ перевода — сама русская фраза. Строки собраны в [tools/strings.json](tools/strings.json), переводы лежат в `tools/tr/<lang>_N.txt`.
Нашли неточность? Поправьте строку и запустите `python tools/build_i18n.py`. Скрипт проверит плейсхолдеры и HTML-теги и соберёт `app/i18n/<lang>.json`. PR приветствуются.

---

## English

JoyDao is a community for people curious about what a birth date hides. It offers a numerology portrait, a daily "energy of the day", and compatibility for love, friendship and business, including whole teams. It also has gentle matchmaking that explains *why* two people resonate. Everything runs as a Telegram bot plus a Mini App, in 7 languages.

The code is open so anyone can verify how personal data is handled:

- Birth dates are never shown to other users.
- Photos are re-encoded with metadata stripped.
- Every request is authenticated with Telegram `initData`.
- Deleting the account wipes the profile and likes.

Stack: Python, aiogram 3, FastAPI, SQLite, and a single-file vanilla JS Mini App.

## License

[AGPL-3.0](LICENSE). You may use, study and modify the code. If you run a modified version as a service, you must publish your changes too.
