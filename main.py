"""
main.py — Точка входа VK-бота анонимных сообщений.
"""

import asyncio
import logging
import random
from vkbottle.bot import Bot, Message
from vkbottle import API

from config import VK_TOKEN, ADMIN_VK_ID, VK_GROUP_ID, get_message_link, get_short_link
from database import (
    init_db, get_or_create_user, get_user, get_user_stats,
    set_notifications, get_blocked_list, unblock_user,
    block_user, get_message, get_ad, set_ad,
    get_last_messages, mark_deleted, close_db,
    USE_SQLITE, DatabasePool # ДОБАВЬ ЭТИ ДВА ИМПОРТА
)
from keyboards import (
    main_menu_kb, message_actions_kb, cancel_kb,
    back_to_menu_kb, settings_kb, blocks_kb,
)
from states import (
    set_state, clear_state, get_data, current_state,
    STATE_WAITING_MESSAGE, STATE_WAITING_REPLY,
)
from anon_message import send_anon_message, handle_reply, handle_report
from tasks import send_reminders, cleanup_task
import admin as adm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


bot = Bot(token=VK_TOKEN)
api: API = bot.api


def _rand() -> int:
    """Генерирует random_id для VK API (защита от дублей)."""
    return random.randint(1, 2_147_483_647)


async def get_user_link(vk_id: int) -> str:
    """Возвращает короткую ссылку на бота с параметром start=vk_id."""
    full_link = get_message_link(VK_GROUP_ID, vk_id)
    return await get_short_link(full_link)


async def send_main_menu(vk_id: int, text: str | None = None):
    await get_or_create_user(vk_id)
    link = await get_user_link(vk_id)
    if text is None:
        text = (
            f"👀 Узнай, что о тебе думают на самом деле!\n\n"
            f"👇 Твоя ссылка:\n{link}\n\n"
            f"📤 Поделись ею — и получай анонимные сообщения!\n\n"
            f"⚠️ Не отправляй оскорбления, угрозы или незаконный контент."
        )
    await api.messages.send(
        user_id=vk_id,
        message=text,
        keyboard=main_menu_kb(vk_id, link),
        random_id=_rand(),
    )


async def _handle_start(message: Message, ref: int | None):
    vk_id = message.from_id
    logger.info(f"Обработка /start для {vk_id}, ref={ref}")

    try:
        info = await api.users.get(user_ids=[vk_id])
        first_name = info[0].first_name if info else ""
        last_name = info[0].last_name if info else ""
    except Exception:
        first_name = last_name = ""

    await get_or_create_user(vk_id, first_name, last_name)
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
        if target and not target.get("is_banned"):
            set_state(vk_id, STATE_WAITING_MESSAGE, target_id=ref)
            await api.messages.send(
                user_id=vk_id,
                message="✉️ Напиши анонимное сообщение\n\nПолучатель не узнает, кто ты. Пиши ниже 👇",
                keyboard=cancel_kb(),
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


# ─── /start (без параметра) ───────────────────────────────────────────────
@bot.on.message(text="/start")
async def cmd_start_plain(message: Message):
    logger.info(f"cmd_start_plain: от {message.from_id}")
    await _handle_start(message, ref=None)


# ─── /start с параметром через текст ─────────────────────────────────────
@bot.on.message(text="/start <ref>")
async def cmd_start_ref(message: Message, ref: str = ""):
    logger.info(f"cmd_start_ref: от {message.from_id}, ref='{ref}'")
    ref_clean = ref.strip()
    ref_id = int(ref_clean) if ref_clean.isdigit() else None
    await _handle_start(message, ref=ref_id)


@bot.on.message(text="/menu")
@bot.on.message(text="/help")
async def cmd_menu(message: Message):
    await send_main_menu(message.from_id)


# ─── ОСНОВНОЙ ОБРАБОТЧИК ──────────────────────────────────────────────────
@bot.on.message()
async def handle_message(message: Message):
    vk_id = message.from_id
    text = (message.text or "").strip()
    payload = message.get_payload_json() or {}
    cmd = payload.get("cmd", "")

    # ── Кнопка «Начать» / переход по реферальной ссылке ─────────────────
    # VK отправляет {"command": "start", "hash": "USER_ID"} при открытии
    # ссылки вида https://vk.me/GROUP?start=USER_ID
    if payload.get("command") == "start":
        hash_val = str(payload.get("hash", "")).strip()
        ref_id = int(hash_val) if hash_val.isdigit() else None
        logger.info(f"Referral via payload command=start, hash='{hash_val}', ref_id={ref_id}, user={vk_id}")
        await _handle_start(message, ref=ref_id)
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

    # ── Отмена / главное меню ───────────────────────────────────────────
    if cmd == "main_menu" or text in ("🏠 Главное меню", "❌ Отмена"):
        clear_state(vk_id)
        await send_main_menu(vk_id)
        return

    state = current_state(vk_id)

    # ── FSM: ввод анонимного сообщения ──────────────────────────────────
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
                    f"✅ Вы отправили анонимное сообщение!\n\n"
                    f"💌 Начните получать анонимные сообщения прямо сейчас!\n\n"
                    f"👉 {sender_link}\n\n"
                    f"Разместите эту ссылку ☝️ к себе на страницу, чтобы вам могли написать 💬"
                    f"{ad_block}"
                ),
                keyboard=back_to_menu_kb(),
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
        await api.messages.send(
            user_id=vk_id,
            message=(
                f"💌 Начните получать анонимные сообщения прямо сейчас!\n\n"
                f"👉 {link}\n\n"
                f"Разместите эту ссылку ☝️ к себе на страницу, чтобы вам могли написать 💬"
            ),
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
        await block_user(vk_id, msg["sender_id"])
        await api.messages.send(
            user_id=vk_id,
            message="❌ Отправитель заблокирован. Он больше не сможет написать вам.",
            keyboard=back_to_menu_kb(),
            random_id=_rand(),
        )
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


def _ad_should_show(ad: dict, place: str) -> bool:
    """Проверяет, нужно ли показывать рекламу в данном месте."""
    return (
        bool(ad.get("enabled", 0))
        and ad.get("place", "AFTER_SEND") == place
        and bool(ad.get("text", "").strip())
    )

async def startup_db():
    logger.info("Инициализация базы данных...")
    try:
        await init_db()
        logger.info("✅ База данных успешно инициализирована")

        # Фоновые задачи запускаем после инициализации БД
        bot.loop_wrapper.add_task(send_reminders(api))
        bot.loop_wrapper.add_task(cleanup_task())

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise


if __name__ == "__main__":
    bot.loop_wrapper.add_task(startup_db())
    bot.run_forever()