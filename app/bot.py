"""Telegram-бот: онбординг, цифровой портрет, матчи, благодарность, агент канала."""
from __future__ import annotations

from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           LabeledPrice, Message, PreCheckoutQuery,
                           ReplyKeyboardRemove, WebAppInfo)

from . import config, db, suisai
from .i18n import T, norm, user_lang

router = Router()


class EditPhoto(StatesGroup):
    waiting = State()


class Onboarding(StatesGroup):
    birth = State()
    goals = State()
    gender = State()
    seek = State()
    city = State()
    bio = State()
    photo = State()


# Подписи старой постоянной клавиатуры: она ещё висит у людей на телефонах.
# Нажатие любой из них убирает клавиатуру и ведёт в приложение.
OLD_MENU = {"💞 Пары", "🔮 Мой портрет", "🔍 Проверить дату", "💞 Совместимость", "☀️ Энергия дня",
            "⚙️ Ещё", "🔍 Чужая дата", "💌 Знакомства", "✨ Ещё"}


def app_url(tab: str = "") -> str:
    """Ссылка на Mini App с нужной вкладкой (today/portrait/feed/check/matches)."""
    return f"{config.WEBAPP_URL}?tab={tab}" if tab else config.WEBAPP_URL


def lang_of(tg_user) -> str:
    """Язык собеседника: из профиля, иначе из настроек его Telegram."""
    return user_lang(db.get_user(tg_user.id), getattr(tg_user, "language_code", None))


def _app_button(text: str = "", tab: str = "", lang: str = "ru") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text or T("✨ Открыть JoyDao", lang),
                                 web_app=WebAppInfo(url=app_url(tab)))]


def _app_kb(text: str = "", tab: str = "", lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_app_button(text, tab, lang)])


def _parse_inviter(arg: str, uid: int) -> int | None:
    """/start pair_<id> или старое ref_<id> → id пригласившего (если он есть в базе)."""
    if not arg.startswith(("pair_", "ref_")):
        return None
    try:
        inviter = int(arg.split("_", 1)[1])
    except ValueError:
        return None
    return inviter if inviter != uid and db.get_user(inviter) else None


def _parse_birth_text(text: str) -> date | None:
    """Принимает 21.03.1992, 21/3/1992, 21 03 1992, 21-03-1992."""
    import re
    norm = re.sub(r"[\s/\-,]+", ".", text.strip())
    try:
        return datetime.strptime(norm, "%d.%m.%Y").date()
    except ValueError:
        return None


def _is_adult(birth: date, today: date | None = None) -> bool:
    """Точная проверка 18+ (по дню, а не по году рождения)."""
    today = today or date.today()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    return 18 <= age <= 100


def _share_url(uid: int, text: str) -> str:
    from urllib.parse import quote
    from . import teaser
    return ("https://t.me/share/url?url=" + quote(teaser.ref_link(uid))
            + "&text=" + quote(text))


PAIR_INVITE_TEXT = "Давай проверим нашу совместимость по коду души 💞 Введи дату рождения — и мы оба увидим результат:"


def _after_value_kb(user, lang: str = "ru") -> InlineKeyboardMarkup:
    """Что делать после первого «вау»: поделиться, полный портрет, знакомства."""
    return InlineKeyboardMarkup(inline_keyboard=[
        _app_button(T("✨ Открыть мой портрет", lang), "portrait"),
        [InlineKeyboardButton(text=T("💞 Проверить совместимость с кем-то", lang),
                              url=_share_url(user["tg_id"], T(PAIR_INVITE_TEXT, lang)))],
    ])


async def _pair_result(bot: Bot, uid: int, other_id: int, target: Message) -> None:
    """Совместимость по парной ссылке: результат обоим, даты друг друга не раскрываются."""
    from . import teaser
    me, other = db.get_user(uid), db.get_user(other_id)
    lang, lang2 = user_lang(me), user_lang(other)
    b1, b2 = date.fromisoformat(me["birth_date"]), date.fromisoformat(other["birth_date"])
    t = teaser.build_compat(b1, b2, lang=lang)
    db.log_event(uid, "pair_done", other_id)
    await target.answer(teaser.render_compat_html(t, with_name=other["name"], lang=lang),
                        parse_mode="HTML", reply_markup=_after_value_kb(me, lang))
    t2 = teaser.build_compat(b2, b1, lang=lang2)  # второму — на его языке
    try:
        await bot.send_message(
            other_id,
            T("💞 <b>{name}</b> открыл(а) вашу ссылку и проверил(а) совместимость с вами: "
              "<b>{score}%</b>\n<i>{verdict}</i>\n\n"
              "Каждый, кто проверит пару с вами, — ещё и +1 кандидат в вашей ленте.", lang2,
              name=me["name"], score=t2["score"], verdict=t2["verdict"]),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=T("↗️ Отправить ссылку ещё кому-то", lang2),
                url=_share_url(other_id, T(PAIR_INVITE_TEXT, lang2)))]]),
        )
    except Exception:
        pass  # отправитель мог закрыть личку боту


@router.message(CommandStart(ignore_case=True))  # «/Start» из описания тоже
async def cmd_start(msg: Message, state: FSMContext, command: CommandObject):
    uid = msg.from_user.id
    arg = (command.args or "")[:64]
    inviter = _parse_inviter(arg, uid)
    user = db.get_user(uid)
    lang = user_lang(user, msg.from_user.language_code)
    if user:
        db.update_user(uid, username=msg.from_user.username or "")
        if inviter:  # уже с нами, но пришёл по чьей-то ссылке — сразу результат пары
            await _pair_result(msg.bot, uid, inviter, msg)
            return
        if arg == "dating" and not db.is_dating(user):  # из приложения: «хочу знакомиться»
            await _start_dating(msg, state)
            return
        if arg == "photo" and db.is_dating(user):  # из приложения: «сменить фото»
            await state.set_state(EditPhoto.waiting)
            await msg.answer(T("📷 Пришлите новое фото — одно, где хорошо видно лицо.", lang),
                             reply_markup=ReplyKeyboardRemove())
            return
        await msg.answer(T("С возвращением! 🌟", lang), reply_markup=ReplyKeyboardRemove())
        await _to_app(msg, T("Всё самое интересное — в приложении 👇", lang), lang=lang)
        return

    db.log_event(uid, "start", arg)
    if inviter:
        db.create_referral(inviter, uid)
        db.log_event(uid, "pair_open", inviter)
        await state.update_data(pair_from=inviter)
        intro = T("💞 <b>{name}</b> хочет узнать вашу совместимость "
                  "по коду души.\n\nОдин шаг — и вы оба увидите результат.", lang,
                  name=db.get_user(inviter)["name"])
    else:
        if arg.startswith("src_"):
            db.record_source(uid, arg[4:])
        intro = T("Добро пожаловать в <b>JoyDao</b> 🌟 — сообщество людей, которым интересно, "
                  "что скрывает дата рождения.\n\n"
                  "Посчитаю ваш код души и покажу портрет. А дальше — совместимость в любви, "
                  "дружбе и бизнесе и люди, с которыми вы на одной волне.", lang)
    await state.set_state(Onboarding.birth)
    await msg.answer(
        intro + "\n\n" + T("<b>Ваша дата рождения?</b>\nНапример: 21.03.1992", lang),
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Onboarding.birth, F.text)
async def ob_birth(msg: Message, state: FSMContext):
    lang = norm(msg.from_user.language_code)
    if msg.text in OLD_MENU:
        await msg.answer(T("Сначала дата рождения — это один шаг 🙂 Например: 21.03.1992", lang))
        return
    birth = _parse_birth_text(msg.text)
    if not birth or not _is_adult(birth):
        await msg.answer(T("Не получилось прочитать дату. Пример: 21.03.1992 (сервис 18+)", lang))
        return
    uid = msg.from_user.id
    data = await state.get_data()
    await state.clear()
    # «Лёгкий» пользователь: портрет и проверки сразу, анкета знакомств — по желанию
    db.upsert_user(uid, name=(msg.from_user.first_name or T("Друг", lang))[:50],
                   birth_date=birth.isoformat(), gender="", seek="",
                   active=0, username=msg.from_user.username or "", lang=lang)
    db.log_event(uid, "birth")
    referrer_id = db.confirm_referral(uid)
    user = db.get_user(uid)

    chs, mission = suisai.consciousness_number(birth), suisai.mission_number(birth)
    await msg.answer(
        T("✨ <b>Ваше Число Сознания — {chs}</b>\n{short}\n\n<b>Миссия — {mission}</b>\n\n"
          "Полный портрет, энергия дня, проверка любой даты и знакомства — "
          "в приложении, бесплатно и навсегда 👇", lang,
          chs=chs, short=T(suisai.CHS_SHORT[chs], lang), mission=mission),
        parse_mode="HTML",
        reply_markup=None if data.get("pair_from") else _after_value_kb(user, lang),
    )
    if data.get("pair_from"):
        await _pair_result(msg.bot, uid, data["pair_from"], msg)
    elif referrer_id:
        try:
            await msg.bot.send_message(
                referrer_id,
                T("🎉 {name} присоединился(ась) по вашей ссылке — "
                  "сегодня вам покажем на 1 кандидата больше.", user_lang(db.get_user(referrer_id)),
                  name=user["name"]))
        except Exception:
            pass


# --- Анкета знакомств: только когда человек сам захотел ---

GOAL_LABELS = {"love": "💞 Любовь", "friend": "🤝 Дружба", "business": "💼 Бизнес"}


def _goals_kb(selected: list[str], lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ " if g in selected else "") + T(label, lang), callback_data=f"goal:{g}")
         for g, label in GOAL_LABELS.items()],
        [InlineKeyboardButton(text=T("Дальше ➡️", lang), callback_data="goal:done")],
    ])


def _gender_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=T("Мужской", lang), callback_data="g:m"),
        InlineKeyboardButton(text=T("Женский", lang), callback_data="g:f"),
    ]])


async def _start_dating(target: Message, state: FSMContext):
    """Анкета в сообществе: цели → пол → (кого ищу — только для любви) → город → о себе → фото."""
    lang = user_lang(db.get_user(target.chat.id))  # в личке chat.id == id пользователя
    await state.set_state(Onboarding.goals)
    await state.update_data(goals=[], lang=lang)
    await target.answer(
        T("📝 <b>Анкета в сообществе</b> — пара минут.\n\n"
          "<b>Кого хотите найти в JoyDao?</b> Можно выбрать несколько — "
          "для каждой цели будет своя лента людей по совместимости.", lang),
        parse_mode="HTML", reply_markup=_goals_kb([], lang))


@router.callback_query(Onboarding.goals, F.data.startswith("goal:"))
async def ob_goals(cb: CallbackQuery, state: FSMContext):
    choice = cb.data.split(":")[1]
    data = await state.get_data()
    goals, lang = data.get("goals", []), data.get("lang", "ru")
    if choice == "done":
        if not goals:
            await cb.answer(T("Выберите хотя бы одну цель 🙂", lang), show_alert=True)
            return
        await state.set_state(Onboarding.gender)
        await cb.message.edit_text(T("<b>Ваш пол?</b>", lang), parse_mode="HTML", reply_markup=_gender_kb(lang))
    elif choice in GOAL_LABELS:
        goals = [g for g in GOAL_LABELS if (g in goals) != (g == choice)]  # переключить
        await state.update_data(goals=goals)
        await cb.message.edit_reply_markup(reply_markup=_goals_kb(goals, lang))
    await cb.answer()


@router.callback_query(F.data == "dating")
async def cb_dating(cb: CallbackQuery, state: FSMContext):
    if not db.get_user(cb.from_user.id):
        await cb.answer(T("Сначала дата рождения: /start", lang_of(cb.from_user)), show_alert=True)
        return
    await _start_dating(cb.message, state)
    await cb.answer()


@router.callback_query(Onboarding.gender, F.data.startswith("g:"))
async def ob_gender(cb: CallbackQuery, state: FSMContext):
    await state.update_data(gender=cb.data.split(":")[1])
    data = await state.get_data()
    lang = data.get("lang", "ru")
    if "love" not in data.get("goals", ["love"]):  # «кого ищу» — только для любви
        await state.set_state(Onboarding.city)
        await cb.message.edit_text(T("Из какого вы города?", lang))
        await cb.answer()
        return
    await state.set_state(Onboarding.seek)
    await cb.message.edit_text(T("<b>Кого вы ищете?</b>", lang), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=T("Мужчину", lang), callback_data="s:m"),
            InlineKeyboardButton(text=T("Женщину", lang), callback_data="s:f"),
            InlineKeyboardButton(text=T("Неважно", lang), callback_data="s:any"),
        ]]))
    await cb.answer()


@router.callback_query(Onboarding.seek, F.data.startswith("s:"))
async def ob_seek(cb: CallbackQuery, state: FSMContext):
    await state.update_data(seek=cb.data.split(":")[1])
    await state.set_state(Onboarding.city)
    await cb.message.edit_text(T("Из какого вы города?", (await state.get_data()).get("lang", "ru")))
    await cb.answer()


@router.message(Onboarding.city, F.text)
async def ob_city(msg: Message, state: FSMContext):
    await state.update_data(city=msg.text.strip()[:50])
    await state.set_state(Onboarding.bio)
    await msg.answer(T("Пара слов о себе — чем живёте, что цените? (до 300 символов)",
                       (await state.get_data()).get("lang", "ru")))


@router.message(Onboarding.bio, F.text)
async def ob_bio(msg: Message, state: FSMContext):
    await state.update_data(bio=msg.text.strip()[:300])
    await state.set_state(Onboarding.photo)
    await msg.answer(T("И последнее — ваше фото 📷", (await state.get_data()).get("lang", "ru")))


@router.message(Onboarding.photo, F.photo)
async def ob_photo(msg: Message, state: FSMContext):
    data = await state.get_data()
    uid = msg.from_user.id
    db.update_user(
        uid, gender=data["gender"], seek=data.get("seek", ""), city=data["city"], bio=data["bio"],
        photo_id=msg.photo[-1].file_id, active=1, username=msg.from_user.username or "",
        goals=",".join(data.get("goals") or ["love"]),
    )
    await state.clear()
    lang = data.get("lang", "ru")
    db.log_event(uid, "profile_done")
    kb = _app_kb(T("👥 Смотреть людей", lang), "pairs") if config.WEBAPP_URL else None
    hint = "" if msg.from_user.username else "\n\n" + T(
        "💡 Задайте себе @username в настройках Telegram — так взаимной симпатии "
        "будет проще вам написать.", lang)
    await msg.answer(T("Анкета готова! 🎉 Я уже подбираю людей, с которыми вы на одной волне.", lang) + hint,
                     reply_markup=kb)
    if config.ADMIN_CHAT_ID:  # премодерация
        await msg.bot.send_photo(config.ADMIN_CHAT_ID, msg.photo[-1].file_id,
            caption=f"Новая анкета: {msg.from_user.first_name}, {data['city']}\n{data['bio']}\nid={uid}")


@router.message(EditPhoto.waiting, F.photo)
async def edit_photo(msg: Message, state: FSMContext):
    await state.clear()
    db.update_user(msg.from_user.id, photo_id=msg.photo[-1].file_id)
    lang = lang_of(msg.from_user)
    await _to_app(msg, T("Фото обновлено ✨", lang), "profile", lang)
    if config.ADMIN_CHAT_ID:  # премодерация, как у новой анкеты
        await msg.bot.send_photo(config.ADMIN_CHAT_ID, msg.photo[-1].file_id,
                                 caption=f"Новое фото: {msg.from_user.first_name}, id={msg.from_user.id}")


async def _to_app(msg: Message, text: str, tab: str = "", lang: str = "ru") -> None:
    """Единый ответ «всё в приложении» (text уже переведён вызывающим)."""
    if not config.WEBAPP_URL:
        await msg.answer("Приложение ещё не подключено (WEBAPP_URL не задан).")
        return
    await msg.answer(text, reply_markup=_app_kb(tab=tab, lang=lang))


# Кнопки из старых сообщений бота — теперь ведут в нужную вкладку приложения
OLD_CALLBACKS = {"portrait": "portrait", "sharecard": "portrait", "invite": "today",
                 "checkdate": "check", "compatdate": "check", "calc": "check"}


@router.callback_query(F.data.in_(OLD_CALLBACKS))
async def old_callback(cb: CallbackQuery):
    lang = lang_of(cb.from_user)
    await _to_app(cb.message, T("Это теперь в приложении 👇", lang), OLD_CALLBACKS[cb.data], lang)
    await cb.answer()


# --- Благодарность через Telegram Stars и криптовалюту ---

CRYPTO_WALLETS = {
    "trx": {"label": "TRON / USDT (TRC-20)", "address": "TYX9R8zBtHmR2VG4J2mLhcZFkB6mB8Z1wh",
            "note": "Переводите TRX или USDT именно в сети TRC-20."},
    "sol": {"label": "Solana", "address": "95TPQQJtr94jmaLtY1xYYyeRWQhntLyYg2gszZ5w441e",
            "note": "Переводите SOL или токены сети Solana."},
    "ton": {"label": "TON", "address": "UQD3LYeH0gZx8s1GRfFUHdOmZwbzoNaOcIOKAM2VLXQIcMQw",
            "note": "Переводите TON или токены сети TON."},
    "bnb": {"label": "BNB (BSC)", "address": "0xF75d15E69917f4A9584310890320B43F23D86916",
            "note": "Переводите BNB или токены сети BSC (BEP-20)."},
}


@router.message(Command("thanks"))
async def cmd_thanks(msg: Message):
    await _thanks(msg, lang_of(msg.from_user))


@router.callback_query(F.data == "thanks")
async def thanks_menu(cb: CallbackQuery):
    await _thanks(cb.message, lang_of(cb.from_user))
    await cb.answer()


async def _thanks(target: Message, lang: str = "ru"):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="100 ⭐", callback_data="pay:100"),
         InlineKeyboardButton(text="500 ⭐", callback_data="pay:500"),
         InlineKeyboardButton(text="1000 ⭐", callback_data="pay:1000")],
        [InlineKeyboardButton(text=T("💎 Криптовалюта", lang), callback_data="crypto")],
    ])
    await target.answer(T(
        "🙏 Проект живёт на благодарности пар, у которых получилось.\n"
        "Все функции бесплатны — это просто спасибо, без обязательств.\n"
        "Если JoyDao помог вам — поддержите нас:", lang), reply_markup=kb)


@router.callback_query(F.data.startswith("pay:"))
async def send_invoice(cb: CallbackQuery):
    amount = int(cb.data.split(":")[1])
    lang = lang_of(cb.from_user)
    await cb.message.answer_invoice(
        title=T("Благодарность проекту", lang),
        description=T("Спасибо, что помогаете знакомить людей ❤️", lang),
        payload=f"thanks_{amount}",
        currency="XTR",  # Telegram Stars — провайдер не нужен
        prices=[LabeledPrice(label=T("Благодарность", lang), amount=amount)],
    )
    await cb.answer()


@router.callback_query(F.data == "crypto")
async def crypto_menu(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=w["label"], callback_data=f"crypto:{key}")]
        for key, w in CRYPTO_WALLETS.items()
    ] + [[InlineKeyboardButton(text=T("⬅ Назад", lang_of(cb.from_user)), callback_data="thanks")]])
    await cb.message.answer(T("Выберите сеть:", lang_of(cb.from_user)), reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("crypto:"))
async def crypto_address(cb: CallbackQuery):
    wallet = CRYPTO_WALLETS[cb.data.split(":")[1]]
    lang = lang_of(cb.from_user)
    qr_url = ("https://api.qrserver.com/v1/create-qr-code/?size=300x300&data="
              + wallet["address"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=T("⬅ Назад", lang), callback_data="crypto"),
    ]])
    await cb.message.answer_photo(
        qr_url,
        caption=f"<b>{wallet['label']}</b>\n\n<code>{wallet['address']}</code>\n\n"
                + T(wallet["note"], lang) + "\n" + T("Нажмите на адрес выше, чтобы скопировать.", lang),
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(msg: Message):
    amount = msg.successful_payment.total_amount
    db.add_thanks(msg.from_user.id, amount)
    await msg.answer(T("❤️ Спасибо! Ваша благодарность помогает соединять новые пары. "
                       "Хотите — поделитесь вашей историей, и она появится на Стене благодарности.",
                       lang_of(msg.from_user)))


@router.message(Command("help"))
async def cmd_help(msg: Message):
    lang = lang_of(msg.from_user)
    await _to_app(msg, T("Всё — в приложении JoyDao: энергия дня, портрет, совместимость, "
                         "проверка любой даты и знакомства 👇\n\n"
                         "Оно же открывается кнопкой «JoyDao» слева от поля ввода.\n"
                         "Поддержать проект: /thanks", lang), lang=lang)


# Старые команды живут в истории чатов — отправляем сразу в нужную вкладку
OLD_COMMANDS = {"app": "", "today": "today", "daily": "today", "me": "portrait",
                "pair": "today", "invite": "today", "check": "check", "compat": "check", "calc": "check"}


@router.message(Command(*OLD_COMMANDS))
async def cmd_open_app(msg: Message, command: CommandObject):
    lang = lang_of(msg.from_user)
    await _to_app(msg, T("Открываю 👇", lang), OLD_COMMANDS[command.command], lang)


@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    """Статистика по каналам продвижения — только для админа (см. ADMIN_CHAT_ID в .env)."""
    if not config.ADMIN_CHAT_ID or str(msg.from_user.id) != str(config.ADMIN_CHAT_ID):
        return
    f = db.funnel(7)
    lines = ["<b>Воронка за 7 дней</b> (уникальные люди):"]
    lines += [f"• {k}: {v}" for k, v in f.items()]
    rows = db.source_stats()
    lines.append("\n<b>Источники</b> (переходы → дошли до даты):")
    lines += [f"• {r['source']}: {r['opened']} → {r['completed']}" for r in rows] or ["• пока нет"]
    await msg.answer("\n".join(lines), parse_mode="HTML")


# --- Энергия дня: подписка ---

@router.callback_query(F.data.startswith("daily:"))
async def daily_toggle(cb: CallbackQuery):
    from . import mailing
    on = cb.data.split(":")[1] == "on"
    lang = lang_of(cb.from_user)
    mailing.set_opt(cb.from_user.id, on)
    if on:
        await cb.answer(T("Энергия дня включена ☀️", lang), show_alert=True)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=T("☀️ Включить обратно", lang), callback_data="daily:on"),
        ]])
        await cb.message.answer(T("Рассылка «Энергия дня» отключена. Возвращайтесь, когда захотите:", lang),
                                reply_markup=kb)
        await cb.answer()


@router.message(F.text.in_(OLD_MENU))
async def old_keyboard(msg: Message):
    """Нажали кнопку старой клавиатуры: убираем её и ведём в приложение."""
    lang = lang_of(msg.from_user)
    await msg.answer(T("Меню переехало в приложение ✨", lang), reply_markup=ReplyKeyboardRemove())
    await _to_app(msg, T("Открывай 👇", lang), lang=lang)


# --- Агент-контентщик канала ---

def _is_admin(user_id: int) -> bool:
    return bool(config.ADMIN_CHAT_ID) and str(user_id) == str(config.ADMIN_CHAT_ID)


@router.message(Command("post"))
async def cmd_post(msg: Message):
    """Ручной запуск генерации поста — только для админа."""
    from . import agent
    if not _is_admin(msg.from_user.id):
        return
    if not (config.ANTHROPIC_API_KEY and config.CHANNEL_ID):
        await msg.answer("Агент выключен: заполните ANTHROPIC_API_KEY и CHANNEL_ID в .env")
        return
    await msg.answer("Генерирую пост…")
    try:
        await agent.daily_run(msg.bot)
    except Exception as e:
        await msg.answer(f"Ошибка генерации: {e}")


@router.callback_query(F.data.startswith("agent:"))
async def agent_moderation(cb: CallbackQuery):
    """Кнопки премодерации черновика: опубликовать / переделать / пропустить."""
    from . import agent
    if not _is_admin(cb.from_user.id):
        await cb.answer("Недоступно", show_alert=True)
        return
    _, action, draft_id = cb.data.split(":")
    draft_id = int(draft_id)
    draft = agent.get_draft(draft_id)
    if not draft:
        await cb.answer("Черновик не найден", show_alert=True)
        return
    async def _edit(text: str, kb=None, html: bool = True):
        """Правка сообщения черновика: текст или (старый формат) подпись к фото."""
        kwargs = {"parse_mode": "HTML"} if html else {}
        try:
            if cb.message.photo:
                await cb.message.edit_caption(caption=text[:1024], reply_markup=kb, **kwargs)
            else:
                from aiogram.types import LinkPreviewOptions
                opts = LinkPreviewOptions(url=agent.card_url(draft["topic"]),
                                          prefer_large_media=True, show_above_text=True)
                await cb.message.edit_text(text, reply_markup=kb,
                                           link_preview_options=opts, **kwargs)
        except Exception:
            if html:
                await _edit(text, kb, html=False)

    if action == "pub":
        await agent.send_post(cb.bot, config.CHANNEL_ID, draft["text"], draft["topic"])
        agent.update_draft(draft_id, status="published")
        await _edit(f"✅ Опубликовано в {config.CHANNEL_ID}")
    elif action == "redo":
        await cb.answer("Переделываю…")
        new_text = await agent.generate_post(draft["topic"])
        agent.update_draft(draft_id, text=new_text)
        await _edit(f"📝 Черновик для {config.CHANNEL_ID}:\n\n{new_text}",
                    kb=agent.moderation_kb(draft_id))
        return
    elif action == "skip":
        agent.update_draft(draft_id, status="skipped")
        await _edit("❌ Черновик пропущен.")
    await cb.answer()


async def notify_match(bot: Bot, user_a: int, user_b: int, score: int, keys: list[str] | None = None):
    """Уведомление обоим о взаимном интересе + ключ к первому разговору (на языке каждого)."""
    import html
    from . import compat
    ua, ub = db.get_user(user_a), db.get_user(user_b)
    for me, other in ((ua, ub), (ub, ua)):
        lang = user_lang(me)
        keys = compat.compatibility(date.fromisoformat(me["birth_date"]),
                                    date.fromisoformat(other["birth_date"]), lang=lang)["explanation"]
        key_text = "\n".join(f"• {k}" for k in keys)
        name = html.escape(other["name"])
        kb = None
        if other["username"]:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=T("✍️ Написать {name}", lang, name=other["name"]), url=f"https://t.me/{other['username']}")]])
            contact = ""
        else:  # без username ссылка tg://user работает не у всех — честно предупреждаем
            contact = "\n\n" + T("Напишите: <a href='tg://user?id={id}'>{name}</a> "
                                  "(если ссылка не открывается — {name} напишет вам первым(ой))",
                                  lang, id=other["tg_id"], name=name)
        try:
            await bot.send_message(
                me["tg_id"],
                T("💫 <b>Взаимный интерес!</b>\n\n{name} из {city} тоже выбрал(а) вас.\n"
                  "Ваше созвучие: <b>{score}%</b>\n\n<b>Ключ к первому разговору:</b>\n",
                  lang, name=name, city=html.escape(other["city"]), score=score) + key_text + contact,
                parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            pass  # пользователь мог закрыть личку боту


@router.message(F.text & ~F.text.startswith("/"))
async def anything_else(msg: Message):
    """Любой текст вне анкеты — подсказка, где всё лежит (регистрируется последним)."""
    lang = lang_of(msg.from_user)
    if not db.get_user(msg.from_user.id):
        await msg.answer(T("Начнём с даты рождения — нажмите /start 🙂", lang))
        return
    await _to_app(msg, T("Всё — в приложении JoyDao 👇", lang), lang=lang)
