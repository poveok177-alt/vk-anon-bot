# database.py
import os
import asyncio
from supabase import create_client, Client
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

_supabase: Optional[Client] = None


def init_supabase():
    global _supabase
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        raise Exception("Missing SUPABASE_URL or SUPABASE_KEY")
    _supabase = create_client(url, key)
    logger.info("Supabase client initialized")


def _db() -> Client:
    if _supabase is None:
        raise Exception("Supabase not initialised. Call init_supabase() first.")
    return _supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Вспомогательная функция: запускает синхронный вызов в отдельном потоке,
# чтобы не блокировать asyncio event loop (исправляет [Errno 16]).
async def _run(fn):
    return await asyncio.to_thread(fn)


# --- Users ------------------------------------------------------------

async def get_or_create_user(vk_id: int, first_name: str = "", last_name: str = "") -> dict:
    resp = await _run(lambda: _db().table("users").select("*").eq("vk_id", vk_id).execute())
    if resp.data:
        user = resp.data[0]
        updates = {"last_active": _now_iso()}
        if first_name:
            updates["first_name"] = first_name
        if last_name:
            updates["last_name"] = last_name
        await _run(lambda: _db().table("users").update(updates).eq("vk_id", vk_id).execute())
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
        await _run(lambda: _db().table("users").insert(new_user).execute())
        return new_user


async def get_user(vk_id: int) -> Optional[dict]:
    resp = await _run(lambda: _db().table("users").select("*").eq("vk_id", vk_id).execute())
    return resp.data[0] if resp.data else None


async def update_last_active(vk_id: int):
    await _run(lambda: _db().table("users").update({"last_active": _now_iso()}).eq("vk_id", vk_id).execute())


async def set_notifications(vk_id: int, val: bool):
    await _run(lambda: _db().table("users").update({"notifications": val}).eq("vk_id", vk_id).execute())


async def get_total_users() -> int:
    resp = await _run(lambda: _db().table("users").select("vk_id", count="exact").execute())
    return resp.count


async def get_all_users_for_broadcast() -> List[int]:
    resp = await _run(
        lambda: _db().table("users").select("vk_id").eq("is_banned", False).eq("notifications", True).execute()
    )
    return [row["vk_id"] for row in resp.data]


async def get_user_stats(vk_id: int) -> dict:
    incoming = await _run(lambda: _db().table("messages").select("id", count="exact").eq("receiver_id", vk_id).execute())
    outgoing = await _run(lambda: _db().table("messages").select("id", count="exact").eq("sender_id", vk_id).execute())
    replied = await _run(
        lambda: _db().table("messages").select("id", count="exact").eq("receiver_id", vk_id).eq("is_replied", True).execute()
    )
    return {"incoming": incoming.count, "outgoing": outgoing.count, "replied": replied.count}


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
    resp = await _run(lambda: _db().table("messages").insert(data).execute())
    return resp.data[0]


async def get_message(msg_id: int) -> Optional[dict]:
    resp = await _run(lambda: _db().table("messages").select("*").eq("id", msg_id).execute())
    return resp.data[0] if resp.data else None


async def mark_replied(msg_id: int):
    await _run(lambda: _db().table("messages").update({"is_replied": True}).eq("id", msg_id).execute())


async def mark_deleted(msg_id: int):
    await _run(lambda: _db().table("messages").update({"is_deleted": True}).eq("id", msg_id).execute())


async def get_last_messages(vk_id: int, limit: int = 5) -> List[dict]:
    resp = await _run(
        lambda: _db().table("messages").select("*").eq("receiver_id", vk_id).eq("is_deleted", False)
                     .order("created_at", desc=True).limit(limit).execute()
    )
    return resp.data


async def delete_old_messages(days: int = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    resp = await _run(
        lambda: _db().table("messages").delete().eq("is_deleted", True).lt("created_at", cutoff).execute()
    )
    logger.info(f"Deleted {len(resp.data)} old messages")


# --- Blocked -----------------------------------------------------------

async def block_user(owner_id: int, blocked_id: int):
    await _run(lambda: _db().table("blocked").insert({"owner_id": owner_id, "blocked_id": blocked_id}).execute())


async def unblock_user(owner_id: int, blocked_id: int):
    await _run(lambda: _db().table("blocked").delete().eq("owner_id", owner_id).eq("blocked_id", blocked_id).execute())


async def is_blocked(owner_id: int, sender_id: int) -> bool:
    resp = await _run(
        lambda: _db().table("blocked").select("owner_id").eq("owner_id", owner_id).eq("blocked_id", sender_id).execute()
    )
    return len(resp.data) > 0


async def get_blocked_list(owner_id: int) -> List[dict]:
    resp = await _run(lambda: _db().table("blocked").select("blocked_id").eq("owner_id", owner_id).execute())
    return resp.data


# --- Banned ------------------------------------------------------------

async def ban_user(vk_id: int):
    await _run(lambda: _db().table("banned").insert({"vk_id": vk_id, "banned_at": _now_iso()}).execute())
    await _run(lambda: _db().table("users").update({"is_banned": True}).eq("vk_id", vk_id).execute())


async def unban_user(vk_id: int):
    await _run(lambda: _db().table("banned").delete().eq("vk_id", vk_id).execute())
    await _run(lambda: _db().table("users").update({"is_banned": False}).eq("vk_id", vk_id).execute())


async def is_banned(vk_id: int) -> bool:
    resp = await _run(lambda: _db().table("banned").select("vk_id").eq("vk_id", vk_id).execute())
    return len(resp.data) > 0


# --- Reports -----------------------------------------------------------

async def add_report(reporter_id: int, msg_id: int) -> int:
    try:
        await _run(
            lambda: _db().table("reports").insert(
                {"message_id": msg_id, "reporter_id": reporter_id, "created_at": _now_iso()}
            ).execute()
        )
    except Exception:
        pass
    resp = await _run(lambda: _db().table("reports").select("id", count="exact").eq("message_id", msg_id).execute())
    return resp.count


async def has_reported(reporter_id: int, msg_id: int) -> bool:
    resp = await _run(
        lambda: _db().table("reports").select("id").eq("message_id", msg_id).eq("reporter_id", reporter_id).execute()
    )
    return len(resp.data) > 0


# --- AD -----------------------------------------------------------------

async def get_ad() -> dict:
    resp = await _run(lambda: _db().table("ad_settings").select("*").eq("id", 1).execute())
    if resp.data:
        return resp.data[0]
    return {"enabled": False, "text": "", "url": "", "btn_text": "📢 Реклама", "place": "AFTER_SEND"}


async def set_ad(**kwargs):
    await _run(lambda: _db().table("ad_settings").update(kwargs).eq("id", 1).execute())


async def is_ad_enabled() -> bool:
    ad = await get_ad()
    return ad.get("enabled", False) and bool(ad.get("text", "").strip())


# --- Дополнительно ------------------------------------------------------

async def get_inactive_users(days: int = 3) -> List[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    resp = await _run(
        lambda: _db().table("users").select("*").lt("last_active", cutoff)
                     .eq("is_banned", False).eq("notifications", True).execute()
    )
    return resp.data


async def get_db_stats() -> dict:
    users = await _run(lambda: _db().table("users").select("vk_id", count="exact").execute())
    msgs = await _run(lambda: _db().table("messages").select("id", count="exact").execute())
    banned = await _run(lambda: _db().table("users").select("vk_id", count="exact").eq("is_banned", True).execute())
    return {"users": users.count, "messages": msgs.count, "banned": banned.count}


async def get_messages_today() -> int:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    resp = await _run(lambda: _db().table("messages").select("id", count="exact").gte("created_at", today_start).execute())
    return resp.count


async def get_reports_total() -> int:
    resp = await _run(lambda: _db().table("reports").select("id", count="exact").execute())
    return resp.count