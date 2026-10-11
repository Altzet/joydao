# JoyDao ✨

**Сообщество людей, которым интересно, что скрывает дата рождения.**
Telegram-бот [@JOYDAO_bot](https://t.me/JOYDAO_bot) + Mini App · канал [@joydao](https://t.me/joydao) · чат [@joydao_club](https://t.me/joydao_club)

*English below.*

- 🎂 Портрет по дате рождения и баланс энергий: что есть, чего не хватает
- ☀️ Энергия дня: каждое утро новый текст, без повторов в течение месяца
- 💞 🤝 💼 Совместимость в любви, дружбе и бизнесе, а также для групп и команд
- 👥 Знакомства по созвучию: лента анкет с объяснением «почему вы совпали»
- 🔍 Проверка любой даты: короткий разбор, которым удобно поделиться с другом
- ⚡ JOY energy: энергия сообщества за вклад, а не за деньги ([подробнее](#joy-energy))
- 🌐 3 языка: русский, українська, English

> Нумерология здесь — игра и повод для рефлексии, а не научный факт.

## Почему код открыт

Мы работаем с личными данными: датой рождения, фото, симпатиями. Поэтому код открыт, и каждый может проверить, что с ними происходит:

- **Дату рождения не видит никто.** Другим людям показываются только число и процент совместимости, в том числе в платном глубоком разборе пары (тесты `test_pair_result_notifies_both_without_leaking_birth_date`, `test_deep_report_hides_birth_date_and_needs_joy`).
- **Фото без метаданных.** Его пережимает Telegram или наш сервер, геометка из файла удаляется. Чужое фото можно получить только по анкете, которую тебе уже показали в ленте (`/api/photo`).
- **Вход только через Telegram.** Каждый запрос Mini App проверяется подписью `initData` (`_auth` в [app/api.py](app/api.py)).
- **Удаление аккаунта** стирает дату, анкету, лайки и взаимные симпатии (`/api/profile/delete`). Повторная регистрация не даёт заново получить JOY (`test_deleting_account_does_not_refarm_joy`).
- **Никакой рекламы и продажи данных.** Проект живёт на добровольные донаты.

## JOY energy

Внутренняя энергия сообщества. Логика вся в [app/joy.py](app/joy.py), баланс — это сумма записей журнала `joy_ledger`, одна награда за одно событие гарантирована ограничением в базе.

| Как получить | Сколько |
|---|---|
| Заполнить анкету и пройти тест принципов сообщества (7 из 9) | 1 JOY, через 3 дня или после первой взаимной симпатии |
| Верный ответ на «Число дня» | 0,05 JOY |
| 7 дней подряд | 0,3 JOY |
| Квиз пары после взаимной симпатии | 0,2 JOY каждому |
| Еженедельный турнир (взнос 0,5 JOY) | 50 / 30 / 20 % банка за 1–3 место, 10 % сжигается |

| На что потратить | Сколько |
|---|---|
| «Искра»: усиленный лайк, ты первый в ленте получателя | 0,1 JOY |
| Буст анкеты на 24 часа | 0,3 JOY |
| Глубокий разбор пары | 0,5 JOY |

Сейчас JOY живёт только внутри приложения. Вывод в джеттон на TON запланирован отдельной фазой.

## Запуск

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # впишите BOT_TOKEN и WEBAPP_URL
.venv/bin/python -m app.main
```

Бот, API и планировщики работают в одном процессе. `ANTHROPIC_API_KEY` необязателен: он нужен для постов в канал, глубокого разбора пары и перевода длинных текстов портрета.

Тесты: `pip install pytest httpx && BOT_TOKEN=1:t python -m pytest -q tests`.
Сервер: systemd-юнит и скрипт установки (nginx + certbot) лежат в [deploy/](deploy/): `bash deploy/server-setup.sh ваш.домен`.

## Устройство

```
app/
  main.py      точка входа: бот (polling) + FastAPI + планировщики
  bot.py       Telegram-бот: онбординг и переход в Mini App
  api.py       API для Mini App, проверка initData
  suisai.py    расчёты по дате рождения
  compat.py    совместимость 0–100, веса в data/compat_rules.json
  joy.py       JOY energy: награды, траты, квизы, турниры
  mailing.py   утренняя рассылка «Энергия дня»
  teaser.py    короткий разбор любой даты для пересылки
  agent.py     ежедневные посты канала с премодерацией
  card.py      картинки к постам (Pillow)
  db.py        SQLite
  i18n.py      переводы
static/index.html   Mini App (один файл, vanilla JS)
tests/              pytest
tools/              сборка переводов и отладка ленты
deploy/             systemd + nginx
```

## Переводы

Ключ перевода — сама русская фраза. Строки собраны в [tools/strings.json](tools/strings.json), переводы лежат в `tools/tr/<uk|en>_N.txt`.
Нашли неточность? Поправьте строку и запустите `python tools/build_i18n.py`. Скрипт проверит плейсхолдеры и HTML-теги и соберёт `app/i18n/<lang>.json`. PR приветствуются.

---

## English

JoyDao is a community for people curious about what a birth date hides. It offers a numerology portrait, a daily "energy of the day", and compatibility for love, friendship and business, including whole teams. It also has gentle matchmaking that explains *why* two people resonate. Everything runs as a Telegram bot plus a Mini App, in Ukrainian, Russian and English.

The code is open so anyone can verify how personal data is handled:

- Birth dates are never shown to other users, not even in the paid pair report.
- Photos are re-encoded with metadata stripped.
- Every request is authenticated with Telegram `initData`.
- Deleting the account wipes the profile and likes.

**JOY energy** is the community's in-app currency. You earn it for joining (completed profile plus a principles test), daily quizzes, streaks, pair quizzes and weekly tournaments. You spend it on a Spark (super-like), a 24h profile boost or a deep pair report. Withdrawal to a TON jetton is planned for a later phase.

Stack: Python, aiogram 3, FastAPI, SQLite, Pillow, and a single-file vanilla JS Mini App.

## License

[AGPL-3.0](LICENSE). You may use, study and modify the code. If you run a modified version as a service, you must publish your changes too.
