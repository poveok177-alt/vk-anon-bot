# database.py
# Резолвит IP Supabase один раз при старте, чтобы обойти
# [Errno 16] Device or resource busy в Vercel Python serverless.

import os
import json
import socket
import asyncio
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

_URL: str = ""
_KEY: str = ""
_HOST: str = ""       # hostname, например abc.supabase.co
_HOST_IP: str = ""    # резолвленный IP


def init_supabase():
    global _URL, _KEY, _HOST, _HOST_IP
    _URL = os.getenv("SUPABASE_URL", "").rstrip("/")
    _KEY = os.getenv("SUPABASE_KEY", "")
    if not _URL or not _KEY:
        raise Exception("Missing SUPABASE_URL or SUPABASE_KEY")

    # Парсим hostname из URL
    parsed = urllib.parse.urlparse(_URL)
    _HOST = parsed.hostname or ""

    # Резолвим IP один раз синхронно при старте
    try:
        _HOST_IP = socket.gethostbyname(_HOST)
        logger.info(f"Supabase {_HOST} -> {_HOST_IP}")
    except Exception as e:
        logger.warning(f"DNS pre-resolve failed: {e}, will use hostname")
        _HOST_IP = _HOST

    logger.info("Supabase credentials loaded")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rest_url(path: str, params: dict = None) -> str:
    # Если IP успешно резолвнулся — подставляем IP, Host передаём в заголовке
    base = _URL if not _HOST_IP or _HOST_IP == _HOST else _URL.replace(_HOST, _HOST_IP)
    url = f"{base}/rest/v1/{path}"
    if params:
        qs = "&".join(f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url += "?" + qs
    return url


def _base_headers() -> dict:
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # Если используем IP — нужен Host заголовок
    if _HOST_IP and _HOST_IP != _HOST:
        h["Host"] = _HOST
    return h


def _sync_request(method: str, path: str, params: dict = None,
                  body: dict = None, extra_headers: dict = None):
    url = _rest_url(path, params)
    headers = _base_headers()
    if extra_headers:
        headers.update(extra_headers)
    data_bytes = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            cr = resp.headers.get("content-range", "")
            data = json.loads(raw) if raw.strip() else []
            return data, cr
    except urllib.error.HTTPError as e:
        raw = e.read()
        logger.error(f"HTTP {e.code} {method} {url}: {raw[:300]}")
        raise
    except Exception as e:
        logger.error(f"Request error {method} {url}: {e}")
        raise


async def _req(method: str, path: str, params: dict = None,
               body: dict = None, extra_headers: dict = None):
    return await asyncio.to_thread(_sync_request, method, path, params, body, extra_headers)


async def _get(path: str, params: dict = None) -> list:
    data, _ = await _req("GET", path, params=params)
    return data if isinstance(data, list) else []


async def _post(path: str, body: dict) -> list:
    data, _ = await _req("POST", path, body=body)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


async def _patch(path: str, body: dict, params: dict = None) -> list:
    data, _ = await _req("PATCH", path, params=params, body=body)
    return data if isinstance(data, list) else []


async def _delete(path: str, params: dict = None) -> list:
    data, _ = await _req("DELETE", path, params=params,
                          extra_headers={"Prefer": "return=representation"})
    return data if isinstance(data, list) else []


async def _count(path: str, params: dict = None) -> int:
    p = {**(params or {}), "select": "id"}
    _, cr = await _req("GET", path, params=p, extra_headers={"Prefer": "count=exact"})
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
            "vk_id": vk_id, "first_name": first_name, "last_name": last_name,
            "notifications": True, "is_banned": False, "msg_count": 0,
            "link_clicks": 0, "created_at": now, "last_active": now,
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
    data = {"sender_id": sender_id, "receiver_id": receiver_id, "text": text,
            "is_replied": False, "is_deleted": False, "created_at": now}
    rows = await _post("messages", data)
    return rows[0] if rows else data


async def get_message(msg_id: int) -> Optional[dict]:
    rows = await _get("messages", {"id": f"eq.{msg_id}", "select": "*"})
    return rows[0] if rows else None


async def mark_replied(msg_id: int):
    await _patch("messages", {"is_replied": True}, {"id": f"eq.{msg_id}"})


async def mark_deleted(msg_id: int):
    await _patch("messages", {"is_deleted": True}, {"id": f"eq.{msg_id}"})


async def get_last_messages(vk_id: int, limit: int = 5) -> List[dict]:
    return await _get("messages", {
        "receiver_id": f"eq.{vk_id}", "is_deleted": "eq.false",
        "select": "*", "order": "created_at.desc", "limit": str(limit),
    })


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
        "last_active": f"lt.{cutoff}", "is_banned": "eq.false",
        "notifications": "eq.true", "select": "*",
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