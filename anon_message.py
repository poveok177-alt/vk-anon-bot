"""
anon_message.py — Отправка и получение анонимных сообщений через VK.
"""

import random
import logging
from vkbottle import API

from config import ADMIN_VK_ID
from database import (
    get_user, save_message, get_message, mark_replied, mark_deleted,
    block_user, is_blocked, is_banned, add_report, has_reported,
    get_ad,
)
from keyboards import message_actions_kb, back_to_menu_kb
from states import clear_state, get_data

logger = logging.getLogger(__name__)

REPORT_THRESHOLD_DELETE = 5


def _rand() -> int:
    return random.randint(1, 2_147_483_647)


def _ad_block_for_place(ad: dict, place: str) -> str:
    """Возвращает рекламный блок для вставки в сообщение, либо пустую строку."""
    if (
        bool(ad.get("enabled", 0))
        and ad.get("place", "AFTER_SEND") == place
        and bool(ad.get("text", "").strip())
    ):
        return f"\n\n─────────────\n{ad['text'].strip()}"
    return ""


async def send_anon_message(api: API, sender_id: int, target_id: int, text: str):
    """
    Отправляет анонимное сообщение от sender_id к target_id.
    Возвращает (ok: bool, error_text: str | None).
    """
    if sender_id == target_id:
        return False, "😅 Нельзя отправить сообщение самому себе!"

    if await is_banned(sender_id):
        return False, "❌ Вы заблокированы и не можете отправлять сообщения."

    target = await get_user(target_id)
    if not target or target.get("is_banned"):
        return False, "⚠️ Пользователь недоступен."

    if await is_blocked(owner_id=target_id, sender_id=sender_id):
        return False, "❌ Вы находитесь в чёрном списке этого пользователя."

    saved = await save_message(sender_id=sender_id, receiver_id=target_id, text=text)
    msg_id = saved["id"]

    # Получаем настройки рекламы одним запросом
    ad = await get_ad()
    ad_text = _ad_block_for_place(ad, "AFTER_RECEIVE")

    # Проверяем, включены ли уведомления у получателя
    if not target.get("notifications", 1):
        return True, None

    try:
        await api.messages.send(
            user_id=target_id,
            message=(
                f"💌 Тебе пришло анонимное сообщение!\n\n"
                f"{text}\n\n"
                f"↩️ Нажми «Ответить», чтобы ответить анонимно."
                f"{ad_text}"
            ),
            keyboard=message_actions_kb(msg_id),
            random_id=_rand(),
        )
    except Exception as e:
        logger.error(f"[send_anon_message] send to {target_id}: {e}")
        return False, "⚠️ Не удалось доставить сообщение. Возможно, пользователь закрыл личку."

    # Лог для администратора (некритично, ошибки игнорируем)
    try:
        await api.messages.send(
            user_id=ADMIN_VK_ID,
            message=(
                f"📝 Новое анонимное сообщение\n"
                f"От: {sender_id} → Кому: {target_id}\n"
                f"Текст: {text[:200]}"
            ),
            random_id=_rand(),
        )
    except Exception:
        pass

    return True, None


async def handle_reply(api: API, sender_id: int, text: str):
    """Обрабатывает ответ на анонимное сообщение."""
    data = get_data(sender_id)
    target_id = data.get("target_id")
    msg_id = data.get("msg_id")

    if not target_id or not msg_id:
        clear_state(sender_id)
        return False, "⚠️ Ошибка. Попробуй нажать «Ответить» заново."

    original = await get_message(msg_id)
    if not original:
        clear_state(sender_id)
        return False, "⚠️ Исходное сообщение не найдено."

    # Проверяем, что адресат ответа всё ещё доступен
    target = await get_user(target_id)
    if not target or target.get("is_banned"):
        clear_state(sender_id)
        return False, "⚠️ Пользователь недоступен."

    if await is_blocked(owner_id=target_id, sender_id=sender_id):
        clear_state(sender_id)
        return False, "❌ Вы находитесь в чёрном списке этого пользователя."

    saved = await save_message(sender_id=sender_id, receiver_id=target_id, text=text)
    await mark_replied(msg_id)

    try:
        await api.messages.send(
            user_id=target_id,
            message=(
                f"↩️ Тебе ответили анонимно!\n\n"
                f"{text}\n\n"
                f"Нажми «Ответить», чтобы продолжить диалог."
            ),
            keyboard=message_actions_kb(saved["id"]),
            random_id=_rand(),
        )
    except Exception as e:
        logger.error(f"[handle_reply] send to {target_id}: {e}")
        clear_state(sender_id)
        return False, "⚠️ Не удалось доставить ответ."

    clear_state(sender_id)
    return True, None


async def handle_report(api: API, reporter_id: int, msg_id: int) -> str:
    """Обрабатывает жалобу на сообщение. Возвращает текст ответа пользователю."""
    if await has_reported(reporter_id, msg_id):
        return "Ты уже жаловался на это сообщение."

    count = await add_report(reporter_id, msg_id)

    msg = await get_message(msg_id)
    sender_id = msg["sender_id"] if msg else 0
    text = msg["text"][:200] if msg else "—"

    # Уведомляем администратора
    try:
        from keyboards import mod_actions_kb
        await api.messages.send(
            user_id=ADMIN_VK_ID,
            message=(
                f"⚠️ Жалоба #{count} на сообщение\n"
                f"ID сообщения: {msg_id}\n"
                f"Отправитель: {sender_id}\n"
                f"Текст: {text}"
            ),
            keyboard=mod_actions_kb(msg_id, sender_id),
            random_id=_rand(),
        )
    except Exception as e:
        logger.warning(f"[handle_report] notify admin: {e}")

    if count >= REPORT_THRESHOLD_DELETE:
        await mark_deleted(msg_id)
        return "🚫 Сообщение удалено автоматически из-за большого числа жалоб."

    return "✅ Жалоба принята. Спасибо!"