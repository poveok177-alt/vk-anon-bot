"""
config.py — Конфигурация VK-бота анонимных сообщений.
"""

import os
import logging
import aiohttp

logger = logging.getLogger(__name__)

# ─── Основные токены и ID ──────────────────────────────────────────────────────

VK_TOKEN = os.getenv("VK_TOKEN", "")          # Токен сообщества VK
ADMIN_VK_ID = int(os.getenv("ADMIN_VK_ID", "0"))  # ID администратора (числовой)
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))   # ID сообщества (числовой, без минуса)

# Короткое имя сообщества (например "myanonbot").
# Если есть — ссылка будет красивее: vk.me/myanonbot?start=USER_ID
# Если нет — используется числовой ID: vk.com/im?sel=-GROUP_ID&start=USER_ID
VK_GROUP_SHORT_NAME = os.getenv("VK_GROUP_SHORT_NAME", "")


# ─── Формирование deep link ────────────────────────────────────────────────────

def get_message_link(group_id: int, user_id: int) -> str:
    """
    Возвращает VK deep link для бота с параметром start=USER_ID.

    При переходе по этой ссылке VK отправит боту:
        payload = {"command": "start", "hash": "<user_id>"}
    и/или текст "/start <user_id>".

    Два возможных формата:
    1. vk.me/GROUP_SHORT_NAME?start=USER_ID  (рекомендуется, нужно короткое имя)
    2. vk.com/im?sel=-GROUP_ID&start=USER_ID (работает по числовому ID)
    """
    if VK_GROUP_SHORT_NAME:
        return f"https://vk.me/{VK_GROUP_SHORT_NAME}?start={user_id}"
    else:
        return f"https://vk.com/im?sel=-{group_id}&start={user_id}"


# ─── Сокращение ссылок через VK API ───────────────────────────────────────────

async def get_short_link(url: str) -> str:
    """
    Сокращает URL через VK API (utils.getShortLink).
    При ошибке возвращает оригинальный URL.
    """
    if not VK_TOKEN:
        logger.warning("[get_short_link] VK_TOKEN не задан, возвращаю оригинал")
        return url

    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "url": url,
                "access_token": VK_TOKEN,
                "v": "5.199",
                "private": 0,
            }
            async with session.get(
                "https://api.vk.com/method/utils.getShortLink",
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                short = data.get("response", {}).get("short_url", "")
                if short:
                    return short
                logger.warning(f"[get_short_link] VK ответил: {data}")
    except Exception as e:
        logger.warning(f"[get_short_link] Ошибка: {e}")

    return url