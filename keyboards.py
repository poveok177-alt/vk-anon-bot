"""
keyboards.py — Клавиатуры для VK-бота.
"""

from vkbottle import Keyboard, KeyboardButtonColor, Text, OpenLink, Callback


def main_menu_kb(vk_id: int, link: str) -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("📤 Поделиться ссылкой", payload={"cmd": "my_link"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("💬 Мои диалоги", payload={"cmd": "my_dialogs"}))
        .row()
        .add(Text("📊 Статистика", payload={"cmd": "my_stats"}))
        .add(Text("⚙️ Настройки", payload={"cmd": "settings"}))
        .row()
        .add(Text("🚫 Блокировки", payload={"cmd": "my_blocks"}))
        .add(Text("🆘 Поддержка", payload={"cmd": "support"}))
    )
    return kb.get_json()


def message_actions_kb(msg_id: int) -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("✏️ Ответить анонимно", payload={"cmd": "reply", "msg_id": msg_id}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🚫 Пожаловаться", payload={"cmd": "report", "msg_id": msg_id}), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("❌ Заблокировать", payload={"cmd": "block", "msg_id": msg_id}), color=KeyboardButtonColor.NEGATIVE)
    )
    return kb.get_json()


def cancel_kb() -> str:
    kb = Keyboard(inline=True).add(Text("❌ Отмена", payload={"cmd": "main_menu"}), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def back_to_menu_kb() -> str:
    kb = Keyboard(inline=True).add(Text("🏠 Главное меню", payload={"cmd": "main_menu"}))
    return kb.get_json()


def settings_kb(notifications_on: bool) -> str:
    notif_text = "🔔 Уведомления: ВКЛ" if notifications_on else "🔕 Уведомления: ВЫКЛ"
    kb = (
        Keyboard(inline=True)
        .add(Text(notif_text, payload={"cmd": "toggle_notifications"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🏠 Главное меню", payload={"cmd": "main_menu"}))
    )
    return kb.get_json()


def blocks_kb(blocked_list: list[dict]) -> str:
    kb = Keyboard(inline=True)
    for i, b in enumerate(blocked_list[:10], 1):
        bid = b["blocked_id"]
        kb.add(Text(f"🔓 Разблокировать #{i}", payload={"cmd": "unblock", "blocked_id": bid}))
        kb.row()
    kb.add(Text("🏠 Главное меню", payload={"cmd": "main_menu"}))
    return kb.get_json()


def admin_menu_kb() -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("📊 Статистика", payload={"cmd": "adm_stats"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📢 Реклама", payload={"cmd": "adm_ad"}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📤 Рассылка", payload={"cmd": "adm_broadcast_info"}))
        .row()
        .add(Text("🔨 Действия с юзером", payload={"cmd": "adm_user_info"}))
    )
    return kb.get_json()


def ad_panel_kb(enabled: bool) -> str:
    toggle_text = "❌ Выключить рекламу" if enabled else "✅ Включить рекламу"
    toggle_cmd = "adm_ad_off" if enabled else "adm_ad_on"
    kb = (
        Keyboard(inline=True)
        .add(Text(toggle_text, payload={"cmd": toggle_cmd}),
             color=KeyboardButtonColor.NEGATIVE if enabled else KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("◀️ Назад", payload={"cmd": "adm_back"}))
    )
    return kb.get_json()


def mod_actions_kb(msg_id: int, sender_id: int) -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("❌ Удалить", payload={"cmd": "mod_delete", "msg_id": msg_id}), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("🚫 Забанить", payload={"cmd": "mod_ban", "msg_id": msg_id, "sender_id": sender_id}), color=KeyboardButtonColor.NEGATIVE)
        .row()
        .add(Text("✅ Игнор", payload={"cmd": "mod_ignore", "msg_id": msg_id}), color=KeyboardButtonColor.POSITIVE)
    )
    return kb.get_json()


def share_command_kb(vk_id: int) -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("📋 Скопировать команду", payload={"cmd": "copy_command", "user_id": vk_id}), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📤 Поделиться ссылкой", payload={"cmd": "my_link"}))
    )
    return kb.get_json()


def ref_choice_kb(target_id: int) -> str:
    kb = (
        Keyboard(inline=True)
        .add(Text("✉️ Отправить анонимно", payload={"cmd": "send_to_ref", "target_id": target_id}),
             color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📤 Начать получать", payload={"cmd": "main_menu"}))
    )
    return kb.get_json()