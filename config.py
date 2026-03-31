# config.py
import os
import random

# VK
VK_TOKEN = os.getenv("VK_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", 0))
CONFIRM_TOKEN = os.getenv("CONFIRM_TOKEN")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Админ (можно задать через переменную)
ADMIN_VK_ID = int(os.getenv("ADMIN_VK_ID", 0))

# Вспомогательные функции для ссылок (оставляем)
def get_message_link(group_id: int, user_id: int) -> str:
    return f"https://vk.me/public{group_id}?ref={user_id}"

async def get_short_link(full_link: str) -> str:
    # Здесь можно использовать vk.cc, но для простоты возвращаем полную ссылку
    return full_link