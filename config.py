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

# Короткое имя сообщества (например "my_bot")
VK_GROUP_SHORTNAME = os.getenv("VK_GROUP_SHORTNAME", "").strip()
VK_API_VERSION = "5.131"

DB_PATH = pathlib.Path(os.getenv("DB_PATH", "./data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Кэш коротких ссылок: url -> (short_url, expires_at)
_short_link_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 3600  # 1 час

logger.info(f"Конфигурация: SHORTNAME='{VK_GROUP_SHORTNAME}', GROUP_ID={VK_GROUP_ID}")


def get_message_link(group_id: int, user_id: int = 0) -> str:
    """
    Возвращает ссылку-анкету для пользователя user_id.
    Если задан VK_GROUP_SHORTNAME — используем vk.me (работает на мобильных).
    """
    if user_id:
        if VK_GROUP_SHORTNAME:
            return f"https://vk.me/{VK_GROUP_SHORTNAME}?start={user_id}"
        # fallback — только десктоп
        return f"https://vk.com/im?sel=-{group_id}&text=/start%20{user_id}"
    if VK_GROUP_SHORTNAME:
        return f"https://vk.me/{VK_GROUP_SHORTNAME}"
    return f"https://vk.com/club{group_id}"


async def get_short_link(full_url: str) -> str:
    """
    Сокращает ссылку через VK API utils.getShortLink.
    При ошибке возвращает исходную ссылку.
    """
    # asyncio.get_event_loop() устарел в Python 3.10+,
    # get_running_loop() корректно работает внутри async-контекста.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # На случай вызова вне event loop — возвращаем оригинал
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