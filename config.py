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
    """
    Сокращает ссылку через VK API utils.getShortLink.
    При ошибке возвращает исходную ссылку.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return full_url

    # Проверяем кэш
    cached = _short_link_cache.get(full_url)
    if cached:
        short_url, expires_at = cached
        if loop.time() < expires_at:
            return short_url
        del _short_link_cache[full_url]

    params = {
        "url": full_url,
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.vk.com/method/utils.getShortLink",
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()

        if "response" in data:
            short_url = data["response"]["short_url"]
            _short_link_cache[full_url] = (short_url, loop.time() + _CACHE_TTL)
            logger.debug(f"Сокращена ссылка: {full_url} -> {short_url}")
            return short_url

        error = data.get("error", {})
        logger.warning(f"Ошибка VK API при сокращении: {error}")
        return full_url

    except asyncio.TimeoutError:
        logger.warning(f"Таймаут при сокращении ссылки: {full_url}")
        return full_url
    except Exception as e:
        logger.error(f"Исключение при сокращении ссылки: {e}")
        return full_url


BOT_LINK = get_message_link(VK_GROUP_ID, 0)