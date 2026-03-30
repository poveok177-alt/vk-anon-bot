# main.py (исправленная версия — убраны <b> теги)
"""
main.py — Точка входа VK-бота анонимных сообщений.
"""

import asyncio
import json
import logging
import random
import urllib.parse
from vkbottle.bot import Bot, Message
from vkbottle import API
from vkbottle import Keyboard, KeyboardButtonColor, Text

from config import VK_TOKEN, ADMIN_VK_ID, VK_GROUP_ID, get_message_link, get_short_link
from database import (
    init_db, get_or_create_user, get_user, get_user_stats,
    set_notifications, get_blocked_list, unblock_user,
    block_user, get_message, get_ad, set_ad,
    get_last_messages, mark_deleted, close_db,
    USE_SQLITE, DatabasePool,
)
from keyboards import (
    main_menu_kb, message_actions_kb, cancel_kb,
    back_to_menu_kb, settings_kb, blocks_kb, ref_choice_kb,
    share_command_kb, after_send_kb,
)
from states import (
    set_state, clear_state, get_data, current_state,
    STATE_WAITING_MESSAGE, STATE_WAITING_REPLY,
)
from anon_message import send_anon_message, handle_reply, handle_report
from tasks import send_reminders, cleanup_task
from web import start_web
import admin as adm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=VK_TOKEN)
api: API = bot.api


def _rand() -> int:
    return random.randint(1, 2_147_483_647)


def _parse_payload(message: Message) -> dict:
    """Надёжный парсинг payload из сообщения VK."""
    try:
        p = message.get_payload_json()
        if p is None:
            return {}
        if isinstance(p, dict):
            return p
        if isinstance(p, str):
            try:
                parsed = json.loads(p)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}
    except Exception as e:
        logger.debug(f"[payload] parse error: {e}")
        return {}


def _extract_ref_id(payload: dict, text: str) -> int | None:
    """Извлекает ID владельца ссылки из всех возможных форматов VK deep link."""
    def _parse_numeric(s: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        if s.isdigit():
            v = int(s)
            return v if v > 0 else None
        for prefix in ("ref_", "id", "u"):
            if s.lower().startswith(prefix):
                rest = s[len(prefix):]
                if rest.isdigit():
                    v = int(rest)
                    return v if v > 0 else None
        return None

    if payload.get("command") == "start":
        raw_hash = str(payload.get("hash", "")).strip()
        ref = _parse_numeric(raw_hash)
        if ref:
            logger.info(f"[extract_ref] hash='{raw_hash}' → ref={ref}")
            return ref
        ref = _parse_numeric(text)
        if ref:
            logger.info(f"[extract_ref] hash empty, text='{text}' → ref={ref}")
            return ref

    if text.startswith("/start "):
        part = text[7:].strip()
        ref = _parse_numeric(part)
        if ref:
            logger.info(f"[extract_ref] /start text → ref={ref}")
            return ref

    logger.debug(f"[extract_ref] ref не найден: payload={payload}, text={text!r}")
    return None


def _is_start_event(payload: dict, text: str) -> bool:
    return (
        payload.get("command") == "start"
        or text in ("Начать", "Start", "/start")
        or text.startswith("/start ")
    )


async def get_user_link(vk_id: int) -> str:
    full_link = get_message_link(VK_GROUP_ID, vk_id)
    return await get_short_link(full_link)


# ─── ПРАВОВЫЕ ТЕКСТЫ (без HTML-тегов) ─────────────────────────────────────────

PRIVACY_TEXT = (
    "🔒 Политика конфиденциальности\n\n"
    "📌 Какие данные мы собираем\n"
    "• VK ID – для идентификации аккаунта\n"
    "• Текст сообщений – для их доставки\n"
    "• Дата и время активности – для напоминаний\n\n"
    "🛡️ Зачем это нужно\n"
    "• Доставлять анонимные сообщения\n"
    "• Защищать от спама и злоупотреблений\n"
    "• Обрабатывать жалобы\n\n"
    "🤝 Кому мы не передаём данные\n"
    "Мы не продаём и не передаём данные третьим лицам.\n"
    "Данные могут быть раскрыты только по официальному запросу.\n\n"
    "🗑️ Как удалить свои данные\n"
    "Напишите /issue Удалите мои данные – мы удалим запись за 72 часа.\n\n"
    "💬 Анонимность\n"
    "Ваше имя и ID скрыты от получателя.\n"
    "Полная анонимность не гарантируется при официальном запросе.\n\n"
    "Используя бота, вы соглашаетесь с данной политикой."
)

TERMS_TEXT = (
    "📋 Правила использования\n\n"
    "Запрещено отправлять:\n"
    "• Оскорбления, угрозы, преследование\n"
    "• Призывы к насилию или дискриминации\n"
    "• Порнографию и непристойный контент\n"
    "• Спам и массовые рассылки\n"
    "• Чужие личные данные без согласия\n"
    "• Контент, нарушающий законодательство\n\n"
    "⚖️ Права администрации\n"
    "• Блокировать без предупреждения\n"
    "• Удалять сообщения\n"
    "• Передавать данные по официальному запросу\n\n"
    "⚠️ Ограничение ответственности\n"
    "Администрация не отвечает за содержание сообщений пользователей.\n\n"
    "По вопросам: /issue Текст обращения"
)


async def send_main_menu(vk_id: int, text: str | None = None):
    await get_or_create_user(vk_id)
    link = await get_user_link(vk_id)

    if text is None:
        text = (
            f"🔥 Твой личный ящик для секретов открыт!\n\n"
            f"Узнай, что о тебе думают на самом деле, когда не видят твоего лица. Это место для самых честных слов, признаний и вопросов.\n\n"
            f"📌 КАК ПОЛУЧАТЬ АНОНИМКИ:\n"
            f"1️⃣ Поделись ссылкой и кодом ниже (в сторис или в профиле).\n"
            f"2️⃣ Друзья перейдут в бота и введут твой код в чат.\n"
            f"3️⃣ Ты получишь их сообщения прямо здесь!\n\n"
            f"🔗 ТВОЯ ССЫЛКА:\n{link}\n"
            f"🔑 ТВОЙ СЕКРЕТНЫЙ КОД:\n/start {vk_id}\n\n"
            f"👇 Нажми на кнопку, чтобы поделиться с миром!"
        )
    await api.messages.send(
        user_id=vk_id,
        message=text,
        keyboard=share_command_kb(vk_id),
        random_id=_rand(),
    )


async def send_legal_menu(vk_id: int):
    """Отправляет меню выбора правового документа."""
    kb = (
        Keyboard(inline=True)
        .add(Text("📋 Правила", payload={"cmd": "terms"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🔒 Политика", payload={"cmd": "privacy"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🏠 Главное меню", payload={"cmd": "main_menu"}))
    )
    await api.messages.send(
        user_id=vk_id,
        message="⚖️ Выберите документ для ознакомления:",
        keyboard=kb.get_json(),
        random_id=_rand(),
    )


async def _handle_start(message: Message, ref: int | None):
    vk_id = message.from_id
    logger.info(f"[start] user={vk_id}, ref={ref} | text={message.text!r}")

    try:
        info = await api.users.get(user_ids=[vk_id])
        first_name = info[0].first_name if info else ""
        last_name = info[0].last_name if info else ""
    except Exception:
        first_name = last_name = ""

    await get_or_create_user(vk_id, first_name, last_name)

    if ref is None:
        existing_state = current_state(vk_id)
        if existing_state == STATE_WAITING_MESSAGE:
            logger.info(f"[start] user={vk_id}: получили 'Начать' без ref, но состояние уже установлено — игнорируем")
            return

    clear_state(vk_id)

    try:
        await api.messages.send(
            user_id=ADMIN_VK_ID,
            message=f"🔔 Новый пользователь!\nID: {vk_id}\nИмя: {first_name} {last_name}",
            random_id=_rand(),
        )
    except Exception:
        pass

    if ref and ref != vk_id:
        target = await get_user(ref)
        if not target:
            logger.info(f"[start] ref={ref} не найден в БД, пробуем получить из VK API")
            try:
                info_target = await api.users.get(user_ids=[ref])
                fn = info_target[0].first_name if info_target else ""
                ln = info_target[0].last_name if info_target else ""
                target = await get_or_create_user(ref, fn, ln)
            except Exception as e:
                logger.warning(f"[start] Не удалось создать пользователя ref={ref}: {e}")
                target = None

        if target and not target.get("is_banned"):
            await api.messages.send(
                user_id=vk_id,
                message=(
                    f"👋 Привет! Ты перешёл по ссылке пользователя {target.get('first_name', '')}.\n\n"
                    f"Выбери действие:"
                ),
                keyboard=ref_choice_kb(ref),
                random_id=_rand(),
            )
            return
        else:
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Пользователь, которому вы хотите написать, недоступен.",
                random_id=_rand(),
            )

    await send_main_menu(vk_id)


def _ad_should_show(ad: dict, place: str) -> bool:
    return (
        bool(ad.get("enabled", 0))
        and ad.get("place", "AFTER_SEND") == place
        and bool(ad.get("text", "").strip())
    )


# ─── ЕДИНЫЙ ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ────────────────────────────────────────

@bot.on.message()
async def handle_message(message: Message):
    vk_id = message.from_id
    text = (message.text or "").strip()

    payload = _parse_payload(message)
    cmd = payload.get("cmd", "")

    logger.debug(f"[msg] user={vk_id}, text={text!r}, payload={payload}, cmd={cmd!r}")

    # ── Deep link / кнопка «Начать» ──────────────────────────────────────
    if _is_start_event(payload, text):
        ref_id = _extract_ref_id(payload, text)
        logger.info(f"[start-event] user={vk_id}, ref_id={ref_id}, payload={payload}")
        await _handle_start(message, ref=ref_id)
        return

    if text in ("/menu", "/help"):
        await send_main_menu(vk_id)
        return

    # ── Правовые команды (без HTML) ─────────────────────────────────────
    if text == "/privacy":
        await api.messages.send(user_id=vk_id, message=PRIVACY_TEXT, random_id=_rand())
        return
    if text == "/terms":
        await api.messages.send(user_id=vk_id, message=TERMS_TEXT, random_id=_rand())
        return

    await get_or_create_user(vk_id)

    # ── ADMIN COMMANDS ───────────────────────────────────────────────────
    if adm.is_admin(vk_id):
        if text == "/admin":
            await adm.cmd_admin(api, vk_id)
            return
        if text == "/stats":
            await adm.cmd_stats(api, vk_id)
            return
        if text.startswith("/ban "):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                await adm.cmd_ban(api, vk_id, int(parts[1]))
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /ban ID", random_id=_rand())
            return
        if text.startswith("/unban "):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                await adm.cmd_unban(api, vk_id, int(parts[1]))
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /unban ID", random_id=_rand())
            return
        if text.startswith("/userinfo "):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                await adm.cmd_userinfo(api, vk_id, int(parts[1]))
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /userinfo ID", random_id=_rand())
            return
        if text.startswith("/broadcast "):
            msg_text = text[len("/broadcast "):].strip()
            if msg_text:
                await adm.cmd_broadcast(api, vk_id, msg_text)
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /broadcast текст", random_id=_rand())
            return
        if text.startswith("/fakebroadcast "):
            msg_text = text[len("/fakebroadcast "):].strip()
            if msg_text:
                await adm.cmd_fakebroadcast(api, vk_id, msg_text)
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /fakebroadcast текст", random_id=_rand())
            return
        if text.startswith("/fakemsg "):
            parts = text.split(maxsplit=2)
            if len(parts) == 3 and parts[1].isdigit():
                await adm.cmd_fakemsg(api, vk_id, int(parts[1]), parts[2])
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /fakemsg ID текст", random_id=_rand())
            return
        if text == "/ad":
            await adm.cmd_ad(api, vk_id)
            return
        if text == "/ad_on":
            await set_ad(enabled=1)
            await api.messages.send(user_id=vk_id, message="✅ Реклама включена.", random_id=_rand())
            return
        if text == "/ad_off":
            await set_ad(enabled=0)
            await api.messages.send(user_id=vk_id, message="❌ Реклама выключена.", random_id=_rand())
            return
        if text.startswith("/ad_text "):
            await set_ad(text=text[9:].strip())
            await api.messages.send(user_id=vk_id, message="✅ Текст рекламы обновлён.", random_id=_rand())
            return
        if text.startswith("/ad_url "):
            await set_ad(url=text[8:].strip())
            await api.messages.send(user_id=vk_id, message="✅ URL рекламы обновлён.", random_id=_rand())
            return
        if text.startswith("/ad_btn "):
            await set_ad(btn_text=text[8:].strip())
            await api.messages.send(user_id=vk_id, message="✅ Текст кнопки обновлён.", random_id=_rand())
            return
        if text.startswith("/ad_place "):
            parts = text.split()
            place = parts[1] if len(parts) > 1 else ""
            if place:
                await adm.cmd_ad_place(api, vk_id, place)
            else:
                await api.messages.send(user_id=vk_id, message="Использование: /ad_place AFTER_SEND", random_id=_rand())
            return
        if text == "/ad_preview":
            await adm.cmd_ad_preview(api, vk_id)
            return

    # ── Отмена / главное меню ────────────────────────────────────────────
    if cmd == "main_menu" or text in ("🏠 Главное меню", "❌ Отмена"):
        clear_state(vk_id)
        await send_main_menu(vk_id)
        return

    # ── КНОПКИ: поделиться в сторис, на странице, скопировать текст ──────
    if cmd == "share_to_stories":
        link = await get_user_link(vk_id)
        command = f"/start {vk_id}"
        story_text = (
            f"🎭 Пиши мне всё, что думаешь! Я не узнаю, кто это. 🤐\n\n"
            f"Заходи в бота: {link}\n"
            f"Введи мой код в чат: {command}\n\n"
            f"Жду твоих откровений... 👇"
        )
        await api.messages.send(
            user_id=vk_id,
            message="📱 Скопируй этот текст и отправь в сторис:",
            random_id=_rand(),
        )
        await api.messages.send(
            user_id=vk_id,
            message=story_text,
            random_id=_rand(),
        )
        return

    if cmd == "share_to_wall":
        link = await get_user_link(vk_id)
        command = f"/start {vk_id}"
        wall_text = (
            f"📤 Хочешь сказать мне что-то анонимно?\n\n"
            f"Сейчас — лучший момент. Я не узнаю твое имя, а ты сможешь выговориться! 🎭\n\n"
            f"📌 ИНСТРУКЦИЯ:\n"
            f"1️⃣ Запусти бота по ссылке: {link}\n"
            f"2️⃣ Введи в чат мой код: {command}\n"
            f"3️⃣ Пиши всё, что на уме.\n\n"
            f"Жду твое сообщение... 🔥"
        )
        await api.messages.send(
            user_id=vk_id,
            message="📝 Скопируй этот текст и опубликуй на стене:",
            random_id=_rand(),
        )
        await api.messages.send(
            user_id=vk_id,
            message=wall_text,
            random_id=_rand(),
        )
        return

    if cmd == "copy_text":
        link = await get_user_link(vk_id)
        command = f"/start {vk_id}"
        text_to_copy = (
            f"📤 Хочешь сказать мне что-то анонимно?\n\n"
            f"Сейчас — лучший момент. Я не узнаю твое имя, а ты сможешь выговориться! 🎭\n\n"
            f"📌 ИНСТРУКЦИЯ:\n"
            f"1️⃣ Запусти бота по ссылке: {link}\n"
            f"2️⃣ Введи в чат мой код: {command}\n"
            f"3️⃣ Пиши всё, что на уме.\n\n"
            f"Жду твое сообщение... 🔥"
        )
        await api.messages.send(
            user_id=vk_id,
            message="📋 Скопируй этот текст и отправь друзьям:",
            random_id=_rand(),
        )
        await api.messages.send(
            user_id=vk_id,
            message=text_to_copy,
            random_id=_rand(),
        )
        return

    # ── Обработка выбора действия после перехода по ссылке ───────────────
    if cmd == "send_to_ref":
        target_id = payload.get("target_id")
        if not target_id:
            return
        target = await get_user(target_id)
        if not target or target.get("is_banned"):
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Пользователь, которому вы хотите написать, недоступен.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
            return
        set_state(vk_id, STATE_WAITING_MESSAGE, target_id=target_id)
        t_name = target.get("first_name", "") or ""
        greeting = f"✉️ Написать анонимное сообщение{f' для {t_name}' if t_name else ''}\n\n"
        await api.messages.send(
            user_id=vk_id,
            message=(
                f"{greeting}"
                f"Получатель не узнает, кто ты. Просто напиши сообщение 👇"
            ),
            keyboard=cancel_kb(),
            random_id=_rand(),
        )
        return

    state = current_state(vk_id)

    # ── FSM: ввод анонимного сообщения ───────────────────────────────────
    if state == STATE_WAITING_MESSAGE:
        if not text:
            await api.messages.send(user_id=vk_id, message="⚠️ Отправь текстовое сообщение.", random_id=_rand())
            return
        if len(text) > 4000:
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Сообщение слишком длинное. Максимум 4000 символов.",
                keyboard=cancel_kb(),
                random_id=_rand(),
            )
            return
        data = get_data(vk_id)
        target_id = data.get("target_id")
        if not target_id:
            clear_state(vk_id)
            await send_main_menu(vk_id)
            return
        ok, err = await send_anon_message(api, vk_id, target_id, text)
        clear_state(vk_id)
        if ok:
            sender_link = await get_user_link(vk_id)
            ad = await get_ad()
            ad_block = ""
            if _ad_should_show(ad, "AFTER_SEND"):
                ad_block = f"\n\n─────────────\n{ad['text'].strip()}"
            await api.messages.send(
                user_id=vk_id,
                message=(
                    f"✅ Сообщение улетело анонимно!\n"
                    f"Теперь жди ответ... 😈\n\n"
                    f"🎭 Хочешь тоже получать анонимные сообщения?\n"
                    f"Узнай, что люди скрывают от тебя. Это бесплатно, честно и полностью анонимно.\n\n"
                    f"📌 ТВОЯ ИНСТРУКЦИЯ:\n"
                    f"1️⃣ Скопируй свою ссылку и код ниже.\n"
                    f"2️⃣ Размести их в сторис или в профиле.\n"
                    f"3️⃣ Получай сообщения, о которых ты даже не догадывался!\n\n"
                    f"🔗 ТВОЯ ССЫЛКА:\n{sender_link}\n"
                    f"🔑 ТВОЙ КОД:\n/start {vk_id}\n"
                    f"{ad_block}"
                ),
                keyboard=after_send_kb(vk_id),
                random_id=_rand(),
            )
        else:
            await api.messages.send(
                user_id=vk_id,
                message=err or "⚠️ Ошибка отправки.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        return

    # ── FSM: ввод ответа ─────────────────────────────────────────────────
    if state == STATE_WAITING_REPLY:
        if not text:
            await api.messages.send(user_id=vk_id, message="⚠️ Отправь текстовое сообщение.", random_id=_rand())
            return
        if len(text) > 4000:
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Сообщение слишком длинное. Максимум 4000 символов.",
                keyboard=cancel_kb(),
                random_id=_rand(),
            )
            return
        ok, err = await handle_reply(api, vk_id, text)
        if ok:
            ad = await get_ad()
            ad_block = ""
            if _ad_should_show(ad, "AFTER_REPLY"):
                ad_block = f"\n\n─────────────\n{ad['text'].strip()}"
            await api.messages.send(
                user_id=vk_id,
                message=f"✅ Ответ отправлен анонимно!{ad_block}",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        else:
            await api.messages.send(
                user_id=vk_id,
                message=err or "⚠️ Ошибка.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        return

    # ── КНОПКИ (payload) ─────────────────────────────────────────────────
    if cmd == "my_link":
        link = await get_user_link(vk_id)
        command = f"/start {vk_id}"
        template = (
            f"🤳 Твой магнит для признаний готов!\n\n"
            f"Скопируй этот текст и закинь в сторис или друзьям в личку:\n\n"
            f"«Хочешь сказать мне правду в лицо, но боишься? 🎭 Напиши мне анонимно!\n\n"
            f"Зайди в бота: {link}\n"
            f"Введи мой код в чат: {command}\n\n"
            f"Пиши всё, что хочешь!»"
        )
        await api.messages.send(
            user_id=vk_id,
            message=template,
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    if cmd == "my_dialogs":
        msgs = await get_last_messages(vk_id, limit=5)
        if not msgs:
            await api.messages.send(
                user_id=vk_id,
                message="💬 Пока сообщений нет 😢\n\nПоделись ссылкой, чтобы получать анонимки!",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        else:
            text_out = "💬 Последние анонимные сообщения:\n\n"
            for m in msgs:
                mark = "✅" if m.get("is_replied") else "🔹"
                short = m["text"][:40] + ("…" if len(m["text"]) > 40 else "")
                text_out += f"{mark} {short}\n"
            await api.messages.send(
                user_id=vk_id,
                message=text_out,
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        return

    if cmd == "my_stats":
        stats = await get_user_stats(vk_id)
        link = await get_user_link(vk_id)
        await api.messages.send(
            user_id=vk_id,
            message=(
                f"📊 Твоя статистика\n\n"
                f"💌 Получено анонимок: {stats['incoming']}\n"
                f"📤 Отправлено: {stats['outgoing']}\n"
                f"✅ Ответил: {stats['replied']}\n\n"
                f"🔗 Твоя ссылка: {link}"
            ),
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    if cmd == "settings":
        user = await get_or_create_user(vk_id)
        notif = bool(user.get("notifications", 1))
        await api.messages.send(
            user_id=vk_id,
            message="⚙️ Настройки",
            keyboard=settings_kb(notif),
            random_id=_rand(),
        )
        return

    if cmd == "toggle_notifications":
        user = await get_or_create_user(vk_id)
        new_val = not bool(user.get("notifications", 1))
        await set_notifications(vk_id, new_val)
        status = "включены 🔔" if new_val else "выключены 🔕"
        await api.messages.send(
            user_id=vk_id,
            message=f"Уведомления {status}",
            keyboard=settings_kb(new_val),
            random_id=_rand(),
        )
        return

    if cmd == "my_blocks":
        blocked = await get_blocked_list(vk_id)
        if not blocked:
            await api.messages.send(
                user_id=vk_id,
                message="🚫 Заблокированные пользователи\n\nСписок пуст. Дыши спокойно 😌",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        else:
            msg_text = f"🚫 Заблокированных: {len(blocked)}\n\n"
            for i, b in enumerate(blocked[:10], 1):
                msg_text += f"{i}. ID: {b['blocked_id']}\n"
            await api.messages.send(
                user_id=vk_id,
                message=msg_text,
                keyboard=blocks_kb(blocked),
                random_id=_rand(),
            )
        return

    if cmd == "unblock":
        blocked_id = payload.get("blocked_id")
        if blocked_id:
            await unblock_user(vk_id, int(blocked_id))
            await api.messages.send(
                user_id=vk_id,
                message=f"✅ Пользователь {blocked_id} разблокирован.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
        return

    if cmd == "support":
        await api.messages.send(
            user_id=vk_id,
            message=(
                "🆘 Техническая поддержка\n\n"
                "Если возник вопрос, жалоба или предложение — "
                "напиши нам в группу или воспользуйся командой:\n\n"
                "/issue Текст вашего сообщения"
            ),
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    if cmd == "reply":
        msg_id = payload.get("msg_id")
        if not msg_id:
            return
        original = await get_message(msg_id)
        if not original:
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Сообщение не найдено.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
            return
        if original.get("is_deleted"):
            await api.messages.send(
                user_id=vk_id,
                message="⚠️ Это сообщение было удалено.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
            return
        if original["sender_id"] == 0:
            await api.messages.send(
                user_id=vk_id,
                message="↩️ Это сообщение пришло с сайта — ответить на него нельзя.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
            return
        set_state(vk_id, STATE_WAITING_REPLY, target_id=original["sender_id"], msg_id=msg_id)
        await api.messages.send(
            user_id=vk_id,
            message="✏️ Напиши ответ — он будет доставлен анонимно:",
            keyboard=cancel_kb(),
            random_id=_rand(),
        )
        return

    if cmd == "report":
        msg_id = payload.get("msg_id")
        if not msg_id:
            return
        result = await handle_report(api, vk_id, msg_id)
        await api.messages.send(
            user_id=vk_id,
            message=result,
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    if cmd == "block":
        msg_id = payload.get("msg_id")
        if not msg_id:
            return
        msg = await get_message(msg_id)
        if not msg:
            await api.messages.send(user_id=vk_id, message="⚠️ Сообщение не найдено.", random_id=_rand())
            return
        if msg["receiver_id"] != vk_id:
            await api.messages.send(user_id=vk_id, message="⛔ Это не ваше сообщение.", random_id=_rand())
            return
        if msg["sender_id"] == 0:
            await api.messages.send(
                user_id=vk_id,
                message="🚫 Нельзя заблокировать анонима, написавшего с сайта.",
                keyboard=back_to_menu_kb(),
                random_id=_rand(),
            )
            return
        await block_user(vk_id, msg["sender_id"])
        await api.messages.send(
            user_id=vk_id,
            message="❌ Отправитель заблокирован. Он больше не сможет написать вам.",
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    # ── Правовые кнопки ─────────────────────────────────────────────────
    if cmd == "legal":
        await send_legal_menu(vk_id)
        return
    if cmd == "privacy":
        await api.messages.send(user_id=vk_id, message=PRIVACY_TEXT, random_id=_rand())
        return
    if cmd == "terms":
        await api.messages.send(user_id=vk_id, message=TERMS_TEXT, random_id=_rand())
        return

    # ── ADMIN CALLBACKS ──────────────────────────────────────────────────
    if adm.is_admin(vk_id):
        if cmd == "adm_stats":
            await adm.cmd_stats(api, vk_id)
            return
        if cmd == "adm_ad":
            await adm.cmd_ad(api, vk_id)
            return
        if cmd == "adm_ad_on":
            await set_ad(enabled=1)
            await api.messages.send(user_id=vk_id, message="✅ Реклама включена.", random_id=_rand())
            return
        if cmd == "adm_ad_off":
            await set_ad(enabled=0)
            await api.messages.send(user_id=vk_id, message="❌ Реклама выключена.", random_id=_rand())
            return
        if cmd == "mod_delete":
            msg_id = payload.get("msg_id")
            if msg_id:
                await mark_deleted(msg_id)
                await api.messages.send(user_id=vk_id, message="✅ Сообщение удалено.", random_id=_rand())
            return
        if cmd == "mod_ban":
            sender_id = payload.get("sender_id")
            msg_id = payload.get("msg_id")
            if sender_id:
                from database import ban_user as _ban
                await _ban(int(sender_id))
                if msg_id:
                    await mark_deleted(msg_id)
                await api.messages.send(
                    user_id=vk_id,
                    message=f"🚫 Пользователь {sender_id} забанен.",
                    random_id=_rand(),
                )
            return
        if cmd == "mod_ignore":
            await api.messages.send(user_id=vk_id, message="✅ Жалоба отклонена.", random_id=_rand())
            return
        if cmd in ("adm_broadcast_info", "adm_user_info", "adm_back"):
            await adm.cmd_admin(api, vk_id)
            return

    # ── /issue ───────────────────────────────────────────────────────────
    if text.startswith("/issue "):
        issue_text = text[7:].strip()
        if issue_text:
            try:
                await api.messages.send(
                    user_id=ADMIN_VK_ID,
                    message=f"📩 Обращение от пользователя\nID: {vk_id}\n\n{issue_text}",
                    random_id=_rand(),
                )
                await api.messages.send(
                    user_id=vk_id,
                    message="✅ Ваше сообщение отправлено в поддержку!",
                    keyboard=back_to_menu_kb(),
                    random_id=_rand(),
                )
            except Exception:
                await api.messages.send(user_id=vk_id, message="⚠️ Не удалось отправить.", random_id=_rand())
        else:
            await api.messages.send(
                user_id=vk_id,
                message="💡 Использование: /issue Текст вашего сообщения",
                random_id=_rand(),
            )
        return

    # ── Fallback ─────────────────────────────────────────────────────────
    await send_main_menu(vk_id)


async def startup_db():
    logger.info("Инициализация базы данных...")
    try:
        await init_db()
        logger.info("✅ База данных успешно инициализирована")
        await start_web(api)
        bot.loop_wrapper.add_task(send_reminders(api))
        bot.loop_wrapper.add_task(cleanup_task())
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise


if __name__ == "__main__":
    bot.loop_wrapper.add_task(startup_db())
    bot.run_forever()