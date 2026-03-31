# database.py
# Обходит supabase-py SDK полностью — использует httpx.AsyncClient напрямую,
# чтобы избежать [Errno 16] Device or resource busy в Vercel serverless.

import os
import httpx
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional, List, Any

logger = logging.getLogger(__name__)

_URL: str = ""
_KEY: str = ""


def init_supabase():
    global _URL, _KEY
    _URL = os.getenv("SUPABASE_URL", "").rstrip("/")
    _KEY = os.getenv("SUPABASE_KEY", "")
    if not _URL or not _KEY:
        raise Exception("Missing SUPABASE_URL or SUPABASE_KEY")
    logger.info("Supabase credentials loaded")


def _headers() -> dict:
    return {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _rest(path: str) -> str:
    return f"{_URL}/rest/v1/{path}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get(path: str, params: dict = None) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(_rest(path), headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


async def _post(path: str, data: dict) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.post(_rest(path), headers=_headers(), json=data)
        r.raise_for_status()
        return r.json()


async def _patch(path: str, data: dict, params: dict = None) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.patch(_rest(path), headers=_headers(), json=data, params=params or {})
        r.raise_for_status()
        return r.json()


async def _delete(path: str, params: dict = None) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.delete(_rest(path), headers={**_headers(), "Prefer": "return=representation"}, params=params or {})
        r.raise_for_status()
        return r.json()


async def _count(path: str, params: dict = None) -> int:
    """Возвращает количество строк через заголовок Content-Range."""
    h = {**_headers(), "Prefer": "count=exact"}
    p = {**(params or {}), "select": "id"}
    async with httpx.AsyncClient() as client:
        r = await client.head(_rest(path), headers=h, params=p)
        # Content-Range: 0-N/TOTAL
        cr = r.headers.get("content-range", "0/0")
        try:
            return int(cr.split("/")[-1])
        except Exception:
            return 0


# --- Users ------------------------------------------------------------

async def get_or_create_user(vk_id: int, first_name: str = "", last_name: str = "") -> dict:
    rows = await _get("users", {"vk_id": f"eq.{vk_id}", "select": "*"})
    if rows:
        user = rows[0]
        updates = {"last_active": _now_iso()}
        if first_name:
            updates["first_name"] = first_name
        if last_name:
            updates["last_name"] = last_name
        await _patch("users", updates, {"vk_id": f"eq.{vk_id}"})
        return {**user, **updates}
    else:
        now = _now_iso()
        new_user = {
            "vk_id": vk_id,
            "first_name": first_name,
            "last_name": last_name,
            "notifications": True,
            "is_banned": False,
            "msg_count": 0,
            "link_clicks": 0,
            "created_at": now,
            "last_active": now,
        }
        rows = await _post("users", new_user)
        return rows[0] if rows else new_user


async def get_user(vk_id: int) -> Optional[dict]:
    rows = await _get("users", {"vk_id": f"eq.{vk_id}", "select": "*"})
    return rows[0] if rows else None


async def update_last_active(vk_id: int):
    await _patch("users", {"last_active": _now_iso()}, {"vk_id": f"eq.{vk_id}"})


async def set_notifications(vk_id: int, val: bool):
    await _patch("users", {"notifications": val}, {"vk_id": f"eq.{vk_id}"})


async def get_total_users() -> int:
    return await _count("users")


async def get_all_users_for_broadcast() -> List[int]:
    rows = await _get("users", {"is_banned": "eq.false", "notifications": "eq.true", "select": "vk_id"})
    return [r["vk_id"] for r in rows]


async def get_user_stats(vk_id: int) -> dict:
    incoming = await _count("messages", {"receiver_id": f"eq.{vk_id}"})
    outgoing = await _count("messages", {"sender_id": f"eq.{vk_id}"})
    replied = await _count("messages", {"receiver_id": f"eq.{vk_id}", "is_replied": "eq.true"})
    return {"incoming": incoming, "outgoing": outgoing, "replied": replied}


# --- Messages ----------------------------------------------------------

async def save_message(sender_id: int, receiver_id: int, text: str) -> dict:
    now = _now_iso()
    data = {
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "text": text,
        "is_replied": False,
        "is_deleted": False,
        "created_at": now,
    }
    rows = await _post("messages", data)
    # Увеличиваем msg_count через RPC (если есть), иначе пропускаем
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{_URL}/rest/v1/rpc/increment_msg_count",
                headers=_headers(),
                json={"user_vk_id": receiver_id},
            )
    except Exception:
        pass
    return rows[0] if rows else data


async def get_message(msg_id: int) -> Optional[dict]:
    rows = await _get("messages", {"id": f"eq.{msg_id}", "select": "*"})
    return rows[0] if rows else None


async def mark_replied(msg_id: int):
    await _patch("messages", {"is_replied": True}, {"id": f"eq.{msg_id}"})


async def mark_deleted(msg_id: int):
    await _patch("messages", {"is_deleted": True}, {"id": f"eq.{msg_id}"})


async def get_last_messages(vk_id: int, limit: int = 5) -> List[dict]:
    rows = await _get("messages", {
        "receiver_id": f"eq.{vk_id}",
        "is_deleted": "eq.false",
        "select": "*",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    return rows


async def delete_old_messages(days: int = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await _delete("messages", {"is_deleted": "eq.true", "created_at": f"lt.{cutoff}"})
    logger.info(f"Deleted {len(rows)} old messages")


# --- Blocked -----------------------------------------------------------

async def block_user(owner_id: int, blocked_id: int):
    await _post("blocked", {"owner_id": owner_id, "blocked_id": blocked_id})


async def unblock_user(owner_id: int, blocked_id: int):
    await _delete("blocked", {"owner_id": f"eq.{owner_id}", "blocked_id": f"eq.{blocked_id}"})


async def is_blocked(owner_id: int, sender_id: int) -> bool:
    rows = await _get("blocked", {"owner_id": f"eq.{owner_id}", "blocked_id": f"eq.{sender_id}", "select": "owner_id"})
    return len(rows) > 0


async def get_blocked_list(owner_id: int) -> List[dict]:
    return await _get("blocked", {"owner_id": f"eq.{owner_id}", "select": "blocked_id"})


# --- Banned ------------------------------------------------------------

async def ban_user(vk_id: int):
    try:
        await _post("banned", {"vk_id": vk_id, "banned_at": _now_iso()})
    except Exception:
        pass
    await _patch("users", {"is_banned": True}, {"vk_id": f"eq.{vk_id}"})


async def unban_user(vk_id: int):
    await _delete("banned", {"vk_id": f"eq.{vk_id}"})
    await _patch("users", {"is_banned": False}, {"vk_id": f"eq.{vk_id}"})


async def is_banned(vk_id: int) -> bool:
    rows = await _get("banned", {"vk_id": f"eq.{vk_id}", "select": "vk_id"})
    return len(rows) > 0


# --- Reports -----------------------------------------------------------

async def add_report(reporter_id: int, msg_id: int) -> int:
    try:
        await _post("reports", {"message_id": msg_id, "reporter_id": reporter_id, "created_at": _now_iso()})
    except Exception:
        pass
    return await _count("reports", {"message_id": f"eq.{msg_id}"})


async def has_reported(reporter_id: int, msg_id: int) -> bool:
    rows = await _get("reports", {"message_id": f"eq.{msg_id}", "reporter_id": f"eq.{reporter_id}", "select": "id"})
    return len(rows) > 0


# --- AD -----------------------------------------------------------------

async def get_ad() -> dict:
    rows = await _get("ad_settings", {"id": "eq.1", "select": "*"})
    if rows:
        return rows[0]
    return {"enabled": False, "text": "", "url": "", "btn_text": "📢 Реклама", "place": "AFTER_SEND"}


async def set_ad(**kwargs):
    await _patch("ad_settings", kwargs, {"id": "eq.1"})


async def is_ad_enabled() -> bool:
    ad = await get_ad()
    return ad.get("enabled", False) and bool(ad.get("text", "").strip())


# --- Дополнительно ------------------------------------------------------

async def get_inactive_users(days: int = 3) -> List[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return await _get("users", {
        "last_active": f"lt.{cutoff}",
        "is_banned": "eq.false",
        "notifications": "eq.true",
        "select": "*",
    })


async def get_db_stats() -> dict:
    users = await _count("users")
    msgs = await _count("messages")
    banned = await _count("users", {"is_banned": "eq.true"})
    return {"users": users, "messages": msgs, "banned": banned}


async def get_messages_today() -> int:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return await _count("messages", {"created_at": f"gte.{today_start}"})


async def get_reports_total() -> int:
    return await _count("reports")