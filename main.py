# main.py
import os
import json
import logging
import asyncio
import random
from fastapi import FastAPI, Request, Response, HTTPException
from vkbottle import API
from vkbottle import Keyboard, KeyboardButtonColor, Text, Callback, OpenLink
from vkbottle.bot import Bot

from config import VK_TOKEN, GROUP_ID, CONFIRM_TOKEN, ADMIN_VK_ID
import database as db
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
import admin as adm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Инициализация Supabase
db.init_supabase()

# VK API клиент
api = API(token=VK_TOKEN)
bot = Bot(token=VK_TOKEN, api=api)

# Вспомогательные функции
def _rand():
    return random.randint(1, 2_147_483_647)

def _parse_payload(message: dict) -> dict:
    try:
        p = message.get("payload")
        if not p:
            return {}
        if isinstance(p, dict):
            return p
        if isinstance(p, str):
            return json.loads(p)
    except:
        pass
    return {}

def _extract_ref_id(payload: dict, text: str) -> int | None:
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
            return ref
        ref = _parse_numeric(text)
        if ref:
            return ref

    if text.startswith("/start "):
        part = text[7:].strip()
        ref = _parse_numeric(part)
        if ref:
            return ref

    return None

def _is_start_event(payload: dict, text: str) -> bool:
    return (
        payload.get("command") == "start"
        or text in ("Начать", "Start", "/start")
        or text.startswith("/start ")
    )

async def get_user_link(vk_id: int) -> str:
    # Простая ссылка на бота с реферальным кодом
    return f"https://vk.com/club{GROUP_ID}?ref={vk_id}"

# --- Тексты политики (без HTML) ---
PRIVACY_TEXT = """
🔒 Политика конфиденциальности

📌 Какие данные мы собираем
• VK ID – для идентификации аккаунта
• Текст сообщений – для их доставки
• Дата и время активности – для напоминаний

🛡️ Зачем это нужно
• Доставлять анонимные сообщения
• Защищать от спама и злоупотреблений
• Обрабатывать жалобы

🤝 Кому мы не передаём данные
Мы не продаём и не передаём данные третьим лицам.
Данные могут быть раскрыты только по официальному запросу.

🗑️ Как удалить свои данные
Напишите /issue Удалите мои данные – мы удалим запись за 72 часа.

💬 Анонимность
Ваше имя и ID скрыты от получателя.
Полная анонимность не гарантируется при официальном запросе.

Используя бота, вы соглашаетесь с данной политикой.
"""

TERMS_TEXT = """
📋 Правила использования

Запрещено отправлять:
• Оскорбления, угрозы, преследование
• Призывы к насилию или дискриминации
• Порнографию и непристойный контент
• Спам и массовые рассылки
• Чужие личные данные без согласия
• Контент, нарушающий законодательство

⚖️ Права администрации
• Блокировать без предупреждения
• Удалять сообщения
• Передавать данные по официальному запросу

⚠️ Ограничение ответственности
Администрация не отвечает за содержание сообщений пользователей.

По вопросам: /issue Текст обращения
"""

# --- Основное меню ---
async def send_main_menu(vk_id: int, text: str | None = None):
    await db.get_or_create_user(vk_id)
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

async def _handle_start(message: dict, ref: int | None):
    vk_id = message["from_id"]
    logger.info(f"[start] user={vk_id}, ref={ref} | text={message.get('text', '')!r}")

    # Получаем имя пользователя
    try:
        info = await api.users.get(user_ids=[vk_id])
        first_name = info[0].first_name if info else ""
        last_name = info[0].last_name if info else ""
    except Exception:
        first_name = last_name = ""

    await db.get_or_create_user(vk_id, first_name, last_name)

    if ref is None:
        existing_state = current_state(vk_id)
        if existing_state == STATE_WAITING_MESSAGE:
            logger.info(f"[start] user={vk_id}: получили 'Начать' без ref, но состояние уже установлено — игнорируем")
            return

    clear_state(vk_id)

    # Уведомление админа
    try:
        await api.messages.send(
            user_id=ADMIN_VK_ID,
            message=f"🔔 Новый пользователь!\nID: {vk_id}\nИмя: {first_name} {last_name}",
            random_id=_rand(),
        )
    except Exception:
        pass

    if ref and ref != vk_id:
        target = await db.get_user(ref)
        if not target:
            logger.info(f"[start] ref={ref} не найден в БД, пробуем получить из VK API")
            try:
                info_target = await api.users.get(user_ids=[ref])
                fn = info_target[0].first_name if info_target else ""
                ln = info_target[0].last_name if info_target else ""
                target = await db.get_or_create_user(ref, fn, ln)
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
        ad.get("enabled", False)
        and ad.get("place") == place
        and bool(ad.get("text", "").strip())
    )

# --- Обработка сообщения ---
async def process_message(message: dict):
    vk_id = message["from_id"]
    text = (message.get("text") or "").strip()

    payload = _parse_payload(message)
    cmd = payload.get("cmd", "")

    logger.debug(f"[msg] user={vk_id}, text={text!r}, payload={payload}, cmd={cmd!r}")

    # Deep link / кнопка «Начать»
    if _is_start_event(payload, text):
        ref_id = _extract_ref_id(payload, text)
        logger.info(f"[start-event] user={vk_id}, ref_id={ref_id}, payload={payload}")
        await _handle_start(message, ref=ref_id)
        return

    if text in ("/menu", "/help"):
        await send_main_menu(vk_id)
        return

    if text == "/privacy":
        await api.messages.send(user_id=vk_id, message=PRIVACY_TEXT, random_id=_rand())
        return
    if text == "/terms":
        await api.messages.send(user_id=vk_id, message=TERMS_TEXT, random_id=_rand())
        return

    await db.get_or_create_user(vk_id)

    # Админские команды
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
            await db.set_ad(enabled=True)
            await api.messages.send(user_id=vk_id, message="✅ Реклама включена.", random_id=_rand())
            return
        if text == "/ad_off":
            await db.set_ad(enabled=False)
            await api.messages.send(user_id=vk_id, message="❌ Реклама выключена.", random_id=_rand())
            return
        if text.startswith("/ad_text "):
            await db.set_ad(text=text[9:].strip())
            await api.messages.send(user_id=vk_id, message="✅ Текст рекламы обновлён.", random_id=_rand())
            return
        if text.startswith("/ad_url "):
            await db.set_ad(url=text[8:].strip())
            await api.messages.send(user_id=vk_id, message="✅ URL рекламы обновлён.", random_id=_rand())
            return
        if text.startswith("/ad_btn "):
            await db.set_ad(btn_text=text[8:].strip())
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

    # Отмена / главное меню
    if cmd == "main_menu" or text in ("🏠 Главное меню", "❌ Отмена"):
        clear_state(vk_id)
        await send_main_menu(vk_id)
        return

    # Кнопки поделиться
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

    # Выбор действия после перехода по ссылке
    if cmd == "send_to_ref":
        target_id = payload.get("target_id")
        if not target_id:
            return
        target = await db.get_user(target_id)
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

    # FSM: ввод анонимного сообщения
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
            ad = await db.get_ad()
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

    # FSM: ввод ответа
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
            ad = await db.get_ad()
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

    # Кнопки (payload)
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
        msgs = await db.get_last_messages(vk_id, limit=5)
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
        stats = await db.get_user_stats(vk_id)
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
        user = await db.get_user(vk_id)
        notif = user.get("notifications", True) if user else True
        await api.messages.send(
            user_id=vk_id,
            message="⚙️ Настройки",
            keyboard=settings_kb(notif),
            random_id=_rand(),
        )
        return

    if cmd == "toggle_notifications":
        user = await db.get_user(vk_id)
        new_val = not user.get("notifications", True) if user else True
        await db.set_notifications(vk_id, new_val)
        status = "включены 🔔" if new_val else "выключены 🔕"
        await api.messages.send(
            user_id=vk_id,
            message=f"Уведомления {status}",
            keyboard=settings_kb(new_val),
            random_id=_rand(),
        )
        return

    if cmd == "my_blocks":
        blocked = await db.get_blocked_list(vk_id)
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
            await db.unblock_user(vk_id, int(blocked_id))
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
        original = await db.get_message(msg_id)
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
        msg = await db.get_message(msg_id)
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
        await db.block_user(vk_id, msg["sender_id"])
        await api.messages.send(
            user_id=vk_id,
            message="❌ Отправитель заблокирован. Он больше не сможет написать вам.",
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
        return

    # Правовые кнопки
    if cmd == "legal":
        await send_legal_menu(vk_id)
        return
    if cmd == "privacy":
        await api.messages.send(user_id=vk_id, message=PRIVACY_TEXT, random_id=_rand())
        return
    if cmd == "terms":
        await api.messages.send(user_id=vk_id, message=TERMS_TEXT, random_id=_rand())
        return

    # Admin callbacks
    if adm.is_admin(vk_id):
        if cmd == "adm_stats":
            await adm.cmd_stats(api, vk_id)
            return
        if cmd == "adm_ad":
            await adm.cmd_ad(api, vk_id)
            return
        if cmd == "adm_ad_on":
            await db.set_ad(enabled=True)
            await api.messages.send(user_id=vk_id, message="✅ Реклама включена.", random_id=_rand())
            return
        if cmd == "adm_ad_off":
            await db.set_ad(enabled=False)
            await api.messages.send(user_id=vk_id, message="❌ Реклама выключена.", random_id=_rand())
            return
        if cmd == "mod_delete":
            msg_id = payload.get("msg_id")
            if msg_id:
                await db.mark_deleted(msg_id)
                await api.messages.send(user_id=vk_id, message="✅ Сообщение удалено.", random_id=_rand())
            return
        if cmd == "mod_ban":
            sender_id = payload.get("sender_id")
            msg_id = payload.get("msg_id")
            if sender_id:
                await db.ban_user(int(sender_id))
                if msg_id:
                    await db.mark_deleted(msg_id)
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

    # /issue
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

    # Fallback
    await send_main_menu(vk_id)


# --- FastAPI эндпоинты ---
@app.get("/webhook")
async def webhook_get(request: Request):
    # Для подтверждения сервера VK
    # Если CONFIRM_TOKEN не совпадает с ожидаемым, используем захардкоженное значение
    expected_token = "0b7ca364"  # Строка из настроек VK
    token = CONFIRM_TOKEN
    if token != expected_token:
        logger.warning(f"CONFIRM_TOKEN={token} не совпадает с ожидаемым {expected_token}, используем ожидаемый")
        token = expected_token
    return Response(content=token, media_type="text/plain")

@app.post("/webhook")
async def webhook_post(request: Request):
    data = await request.json()
    logger.info(f"Received webhook: {data}")
    try:
        # Проверка типа события
        if data.get("type") == "message_new":
            message = data["object"]["message"]
            # Запускаем обработку в фоне, чтобы не блокировать ответ
            asyncio.create_task(process_message(message))
        # Можно добавить другие типы (message_reply, и т.д.)
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
    return Response(content="ok")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/cron/cleanup")
async def cleanup():
    """Эндпоинт для вызова по расписанию (например, Vercel Cron)"""
    try:
        await db.delete_old_messages(days=30)
        return {"status": "ok", "message": "Cleanup done"}
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        return {"status": "error", "message": str(e)}

# Если нужно запускать напоминания, можно сделать аналогичный эндпоинт
@app.post("/cron/reminders")
async def reminders():
    from tasks import send_reminders as _send_reminders
    # ВАЖНО: send_reminders должен быть адаптирован для однократного вызова (без цикла)
    # Пока оставим заглушку
    # await _send_reminders(api)
    return {"status": "ok", "message": "Reminders not implemented in this version"}