# admin.py
"""
admin.py — Команды администратора VK-бота.
"""

import asyncio
import random
import logging
from vkbottle import API
from vkbottle import Keyboard, KeyboardButtonColor, OpenLink

from config import ADMIN_VK_ID, VK_GROUP_ID, get_message_link
from database import (
    get_total_users, get_all_users_for_broadcast,
    get_user, ban_user, unban_user, get_user_stats,
    save_message, get_ad, set_ad, is_ad_enabled,
)
from keyboards import admin_menu_kb, ad_panel_kb, message_actions_kb

logger = logging.getLogger(__name__)


def _rand() -> int:
    return random.randint(1, 2_147_483_647)


def is_admin(vk_id: int) -> bool:
    return vk_id == ADMIN_VK_ID


async def cmd_admin(api: API, admin_id: int):
    total = await get_total_users()
    ad = await get_ad()
    ad_status = "✅ ВКЛ" if ad.get("enabled") else "❌ ВЫКЛ"
    place = ad.get("place", "AFTER_SEND")
    place_text = {
        "AFTER_SEND": "После отправки анонимки (отправителю)",
        "AFTER_RECEIVE": "При получении анонимки",
        "AFTER_REPLY": "После ответа",
    }.get(place, place)

    await api.messages.send(
        user_id=admin_id,
        message=(
            f"👑 Панель администратора\n\n"
            f"👥 Всего пользователей: {total}\n"
            f"📢 Реклама: {ad_status}\n"
            f"📍 Место показа: {place_text}\n\n"
            f"Все команды:\n"
            f"/broadcast текст — рассылка всем\n"
            f"/fakebroadcast текст — анонимка всем\n"
            f"/fakemsg ID текст — анонимка одному\n"
            f"/ban ID — забанить\n"
            f"/unban ID — разбанить\n"
            f"/userinfo ID — инфо о юзере\n"
            f"/stats — статистика бота\n\n"
            f"Реклама:\n"
            f"/ad — панель рекламы\n"
            f"/ad_on — включить\n"
            f"/ad_off — выключить\n"
            f"/ad_text текст — текст рекламы\n"
            f"/ad_url ссылка — ссылка кнопки\n"
            f"/ad_btn текст — текст кнопки\n"
            f"/ad_place AFTER_SEND — место показа\n"
            f"/ad_preview — предпросмотр"
        ),
        keyboard=admin_menu_kb(),
        random_id=_rand(),
    )


async def cmd_stats(api: API, admin_id: int):
    total = await get_total_users()

    try:
        from database import get_db_stats
        db_stats = await get_db_stats()
        msgs_total = db_stats.get("messages", 0)
        banned = db_stats.get("banned", 0)

        from database import USE_SQLITE
        if USE_SQLITE:
            import sqlite3
            from config import DB_PATH

            def _today():
                with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                    msgs_today = c.execute(
                        "SELECT COUNT(*) FROM messages WHERE date(created_at)=date('now')"
                    ).fetchone()[0]
                    reports = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
                return msgs_today, reports

            msgs_today, reports = await asyncio.to_thread(_today)
        else:
            from database import DatabasePool
            pool = await DatabasePool.get_pool()
            async with pool.acquire() as conn:
                msgs_today = await conn.fetchval(
                    "SELECT COUNT(*) FROM messages WHERE created_at::date = CURRENT_DATE"
                )
                reports = await conn.fetchval("SELECT COUNT(*) FROM reports")
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        msgs_total = msgs_today = banned = reports = 0

    await api.messages.send(
        user_id=admin_id,
        message=(
            f"📊 Статистика бота\n\n"
            f"👥 Всего пользователей: {total}\n"
            f"🚫 Забанено: {banned}\n\n"
            f"💬 Сообщений всего: {msgs_total}\n"
            f"💬 Сообщений сегодня: {msgs_today}\n"
            f"⚠️ Жалоб всего: {reports}"
        ),
        random_id=_rand(),
    )


async def cmd_ban(api: API, admin_id: int, target_id: int):
    await ban_user(target_id)
    await api.messages.send(
        user_id=admin_id,
        message=f"🚫 Пользователь {target_id} заблокирован.",
        random_id=_rand(),
    )


async def cmd_unban(api: API, admin_id: int, target_id: int):
    await unban_user(target_id)
    await api.messages.send(
        user_id=admin_id,
        message=f"✅ Пользователь {target_id} разблокирован.",
        random_id=_rand(),
    )


async def cmd_userinfo(api: API, admin_id: int, target_id: int):
    user = await get_user(target_id)
    if not user:
        await api.messages.send(
            user_id=admin_id,
            message=f"⚠️ Пользователь {target_id} не найден в базе.",
            random_id=_rand(),
        )
        return

    stats = await get_user_stats(target_id)
    await api.messages.send(
        user_id=admin_id,
        message=(
            f"👤 Информация о пользователе\n\n"
            f"VK ID: {target_id}\n"
            f"Имя: {user.get('first_name','')} {user.get('last_name','')}\n"
            f"Забанен: {'🚫 Да' if user.get('is_banned') else '✅ Нет'}\n"
            f"Уведомления: {'🔔 Вкл' if user.get('notifications') else '🔕 Выкл'}\n"
            f"Последняя активность: {str(user.get('last_active','—'))[:19]}\n\n"
            f"💬 Получено: {stats['incoming']}\n"
            f"📤 Отправлено: {stats['outgoing']}\n"
            f"✅ Ответил: {stats['replied']}"
        ),
        random_id=_rand(),
    )


async def cmd_broadcast(api: API, admin_id: int, text: str):
    users = await get_all_users_for_broadcast()
    sent = blocked = errors = 0

    await api.messages.send(
        user_id=admin_id,
        message=f"🚀 Начинаю рассылку на {len(users)} пользователей...",
        random_id=_rand(),
    )

    for uid in users:
        try:
            await api.messages.send(user_id=uid, message=text, random_id=_rand())
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            err_str = str(e).lower()
            if "can't send" in err_str or "privacy" in err_str:
                blocked += 1
            else:
                errors += 1
                logger.error(f"[broadcast] uid={uid}: {e}")

    await api.messages.send(
        user_id=admin_id,
        message=(
            f"✅ Рассылка завершена!\n\n"
            f"📥 Доставлено: {sent}\n"
            f"🚫 Закрытые ЛС: {blocked}\n"
            f"⚠️ Ошибки: {errors}\n"
            f"Охват: {round(sent/len(users)*100) if users else 0}%"
        ),
        random_id=_rand(),
    )


async def cmd_fakebroadcast(api: API, admin_id: int, text: str):
    users = await get_all_users_for_broadcast()
    sent = 0
    errors = 0

    await api.messages.send(
        user_id=admin_id,
        message=f"🚀 Начинаю анонимную рассылку на {len(users)} пользователей...",
        random_id=_rand(),
    )

    for uid in users:
        try:
            saved = await save_message(sender_id=admin_id, receiver_id=uid, text=text)
            await api.messages.send(
                user_id=uid,
                message=f"💌 Тебе пришло анонимное сообщение!\n\n{text}",
                keyboard=message_actions_kb(saved["id"]),
                random_id=_rand(),
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            errors += 1
            logger.error(f"[fakebroadcast] uid={uid}: {e}")

    await api.messages.send(
        user_id=admin_id,
        message=(
            f"✅ Анонимная рассылка завершена!\n\n"
            f"📥 Доставлено: {sent}\n"
            f"⚠️ Ошибки: {errors}\n"
            f"Охват: {round(sent/len(users)*100) if users else 0}%"
        ),
        random_id=_rand(),
    )


async def cmd_fakemsg(api: API, admin_id: int, target_id: int, text: str):
    try:
        saved = await save_message(sender_id=admin_id, receiver_id=target_id, text=text)
        await api.messages.send(
            user_id=target_id,
            message=f"💌 Тебе пришло анонимное сообщение!\n\n{text}",
            keyboard=message_actions_kb(saved["id"]),
            random_id=_rand(),
        )
        await api.messages.send(
            user_id=admin_id,
            message=f"✅ Анонимка отправлена пользователю {target_id}",
            random_id=_rand(),
        )
    except Exception as e:
        await api.messages.send(
            user_id=admin_id,
            message=f"⚠️ Ошибка: {e}",
            random_id=_rand(),
        )


async def cmd_ad(api: API, admin_id: int):
    ad = await get_ad()
    enabled = bool(ad.get("enabled", 0))
    place = ad.get("place", "AFTER_SEND")

    place_text = {
        "AFTER_SEND": "После отправки анонимки (отправителю)",
        "AFTER_RECEIVE": "При получении анонимки",
        "AFTER_REPLY": "После ответа",
    }.get(place, place)

    await api.messages.send(
        user_id=admin_id,
        message=(
            f"📢 Управление рекламой\n\n"
            f"Статус: {'✅ ВКЛЮЧЕНА' if enabled else '❌ ВЫКЛЮЧЕНА'}\n"
            f"Место: {place_text}\n\n"
            f"Текст:\n{ad.get('text', '—') or '—'}\n\n"
            f"Ссылка кнопки: {ad.get('url', '—') or '—'}\n"
            f"Текст кнопки: {ad.get('btn_text', '—')}\n\n"
            f"Команды для изменения:\n"
            f"/ad_text Текст рекламного объявления\n"
            f"/ad_url https://example.com\n"
            f"/ad_btn Текст кнопки\n"
            f"/ad_place AFTER_SEND\n\n"
            f"Места показа:\n"
            f"• AFTER_SEND — после отправки анонимки\n"
            f"• AFTER_RECEIVE — при получении анонимки\n"
            f"• AFTER_REPLY — после ответа"
        ),
        keyboard=ad_panel_kb(enabled),
        random_id=_rand(),
    )


async def cmd_ad_place(api: API, admin_id: int, place: str):
    valid_places = ["AFTER_SEND", "AFTER_RECEIVE", "AFTER_REPLY"]
    if place not in valid_places:
        await api.messages.send(
            user_id=admin_id,
            message=f"❌ Неверное место. Доступные: {', '.join(valid_places)}",
            random_id=_rand(),
        )
        return

    await set_ad(place=place)
    await api.messages.send(
        user_id=admin_id,
        message=f"✅ Место показа изменено на {place}",
        random_id=_rand(),
    )


async def cmd_ad_preview(api: API, admin_id: int):
    ad = await get_ad()
    if not ad.get("enabled") or not ad.get("text"):
        await api.messages.send(
            user_id=admin_id,
            message="❌ Реклама выключена или текст пуст",
            random_id=_rand(),
        )
        return

    ad_text = ad.get("text", "")
    url = ad.get("url", "")
    btn_text = ad.get("btn_text", "📢 Реклама")

    message_text = f"📢 *Предпросмотр рекламы*\n\n{ad_text}"

    if url:
        kb = Keyboard(inline=True).add(OpenLink(btn_text, url), color=KeyboardButtonColor.PRIMARY)
        await api.messages.send(
            user_id=admin_id,
            message=message_text,
            keyboard=kb.get_json(),
            random_id=_rand(),
        )
    else:
        await api.messages.send(user_id=admin_id, message=message_text, random_id=_rand())