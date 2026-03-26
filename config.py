import os
import pathlib
import aiohttp
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
ADMIN_VK_ID = int(os.getenv("ADMIN_VK_ID", "0"))

# Короткое имя сообщества (например "my_bot") — ОБЯЗАТЕЛЬНО для реферальных ссылок!
# Найти: Управление → Настройки → Основные → Адрес страницы
VK_GROUP_SHORTNAME = os.getenv("VK_GROUP_SHORTNAME", "").strip()
VK_API_VERSION = "5.131"

DB_PATH = pathlib.Path(os.getenv("DB_PATH", "./data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Кэш коротких ссылок: url -> (short_url, expires_at)
_short_link_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 3600  # 1 час

if VK_GROUP_SHORTNAME:
    logger.info(f"Конфигурация: SHORTNAME='{VK_GROUP_SHORTNAME}', GROUP_ID={VK_GROUP_ID}")
else:
    logger.warning(
        "⚠️  VK_GROUP_SHORTNAME не задан в .env!\n"
        "   Реферальные ссылки будут использовать запасной формат.\n"
        "   Для надёжной работы задайте короткое имя группы:\n"
        "   Управление → Настройки → Основные → Адрес страницы"
    )


def get_message_link(group_id: int, user_id: int = 0) -> str:
    """
    Возвращает реферальную ссылку для пользователя user_id.

    Приоритет форматов:
    1. vk.me/{shortname}?start={user_id}  — лучший вариант, требует VK_GROUP_SHORTNAME
    2. vk.com/write-{group_id}?start={user_id} — запасной вариант через group_id

    Оба формата передают параметр start, который VK отправляет боту
    в виде payload {"command": "start", "hash": "USER_ID"}.
    """
    if VK_GROUP_SHORTNAME:
        if user_id:
            return f"https://vk.me/{VK_GROUP_SHORTNAME}?start={user_id}"
        return f"https://vk.me/{VK_GROUP_SHORTNAME}"

    # Запасной формат: vk.com/write-{group_id}
    # Открывает чат с сообществом и передаёт параметр start боту
    if group_id:
        if user_id:
            return f"https://vk.com/write-{group_id}?start={user_id}"
        return f"https://vk.com/write-{group_id}"

    logger.error("Ни VK_GROUP_SHORTNAME, ни VK_GROUP_ID не заданы — ссылки не будут работать!")
    return "https://vk.com/"


async def get_short_link(full_url: str) -> str:
    return full_url  # временно отключено для отладки


BOT_LINK = get_message_link(VK_GROUP_ID, 0)