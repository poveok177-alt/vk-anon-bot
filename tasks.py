# tasks.py
"""
tasks.py — Фоновые задачи:
  1. send_reminders() — каждые 6 часов напоминает неактивным
  2. cleanup_task()   — раз в сутки очищает старые записи
"""

import asyncio
import random
import logging
from vkbottle import API

from config import get_message_link, get_short_link, VK_GROUP_ID
from database import (
    get_inactive_users, update_last_active,
    set_notifications, delete_old_messages,
)
from keyboards import share_command_kb  # добавим клавиатуру

logger = logging.getLogger(__name__)

REMINDER_INTERVAL_HOURS = 6
INACTIVE_DAYS = 3
CLEANUP_INTERVAL_HOURS = 24


def _rand() -> int:
    return random.randint(1, 2_147_483_647)


async def send_reminders(api: API):
    """Каждые 6 часов напоминаем неактивным пользователям о боте."""
    # Ждём, пока main.py создаст таблицы
    await asyncio.sleep(5)
    while True:
        try:
            inactive = await get_inactive_users(days=INACTIVE_DAYS)
            logger.info(f"[reminders] Неактивных: {len(inactive)}")

            for user in inactive:
                uid = user["vk_id"]
                full_link = get_message_link(VK_GROUP_ID, uid)
                short_link = await get_short_link(full_link)
                try:
                    # Отправляем напоминание с клавиатурой share_command_kb
                    await api.messages.send(
                        user_id=uid,
                        message=(
                            f"🤫 Тишина... Кажется, о тебе начали забывать.\n\n"
                            f"А ведь кто-то прямо сейчас может хранить секрет, связанный с тобой. Напомни друзьям, где тебе можно высказаться!\n\n"
                            f"🔗 Ссылка: {short_link}\n"
                            f"🔑 Твой код: <code>/start {uid}</code>\n\n"
                            f"Выложи ссылку в сторис — проверь, кто из друзей самый смелый сегодня! 🔥"
                        ),
                        keyboard=share_command_kb(uid),  # та же клавиатура, что в главном меню
                        random_id=_rand(),
                        parse_mode="HTML",
                    )
                    await update_last_active(uid)
                    await asyncio.sleep(0.05)
                except Exception as e:
                    err_str = str(e).lower()
                    if "privacy" in err_str or "can't send" in err_str or "18" in err_str:
                        # Пользователь закрыл личку — отключаем уведомления
                        await set_notifications(uid, False)
                    else:
                        logger.error(f"[reminders] uid={uid}: {e}")
        except Exception as e:
            logger.error(f"[reminders] Общая ошибка: {e}")

        await asyncio.sleep(REMINDER_INTERVAL_HOURS * 3600)


async def cleanup_task():
    """Раз в сутки удаляет помеченные удалёнными сообщения старше 30 дней."""
    while True:
        try:
            await delete_old_messages(days=30)
            logger.info("[cleanup] Очистка завершена")
        except Exception as e:
            logger.error(f"[cleanup] {e}")
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)