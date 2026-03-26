"""
database.py — PostgreSQL база данных для VK-бота анонимок.

Таблицы:
  users        — пользователи
  messages     — анонимные сообщения
  blocked      — блокировки
  banned       — баны
  reports      — жалобы
  ad_settings  — настройки рекламы
"""

import os
import asyncpg
import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Получаем URL базы данных из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL", os.getenv("POSTGRES_URL", ""))
if not DATABASE_URL:
    logger.warning("DATABASE_URL не задан! Будет использован SQLite fallback")
    # Здесь можно оставить SQLite как fallback или выдать ошибку
    USE_SQLITE = True
else:
    USE_SQLITE = False

# Для SQLite fallback (если нужно)
if USE_SQLITE:
    import sqlite3
    from config import DB_PATH
    logger.info("Используется SQLite база данных")


class DatabasePool:
    """Пул соединений с PostgreSQL"""
    _pool = None

    @classmethod
    async def get_pool(cls):
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        return cls._pool

    @classmethod
    async def close(cls):
        if cls._pool:
            await cls._pool.close()
            cls._pool = None


async def init_db():
    """Создаёт все таблицы при первом запуске"""
    if USE_SQLITE:
        # SQLite fallback
        from config import DB_PATH
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            _init_sqlite(c)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        # Создаём таблицы если их нет
        # Внутри async def init_db():
        # В таблице users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                ...
                created_at      TIMESTAMPTZ NOT NULL, -- Было TIMESTAMP
                last_active     TIMESTAMPTZ NOT NULL  -- Было TIMESTAMP
            )
        """)

        # В таблице messages
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                ...
                created_at      TIMESTAMPTZ NOT NULL  -- Было TIMESTAMP
            )
        """)

        # В таблице banned
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned (
                vk_id       BIGINT PRIMARY KEY,
                banned_at   TIMESTAMPTZ NOT NULL      -- Было TIMESTAMP
            )
        """)

        # В таблице reports
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                ...
                created_at  TIMESTAMPTZ NOT NULL,     -- Было TIMESTAMP
                UNIQUE(message_id, reporter_id)
            )
        """)

        # Создаём индексы
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_receiver ON messages(receiver_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(sender_id)")

    logger.info("PostgreSQL инициализирован")


def _init_sqlite(c):
    """Инициализация SQLite (fallback)"""
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            vk_id           INTEGER PRIMARY KEY,
            first_name      TEXT    DEFAULT '',
            last_name       TEXT    DEFAULT '',
            notifications   INTEGER DEFAULT 1,
            is_banned       INTEGER DEFAULT 0,
            msg_count       INTEGER DEFAULT 0,
            link_clicks     INTEGER DEFAULT 0,
            created_at      TEXT    NOT NULL,
            last_active     TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id   INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            text        TEXT    NOT NULL,
            is_replied  INTEGER DEFAULT 0,
            is_deleted  INTEGER DEFAULT 0,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blocked (
            owner_id    INTEGER NOT NULL,
            blocked_id  INTEGER NOT NULL,
            PRIMARY KEY (owner_id, blocked_id)
        );

        CREATE TABLE IF NOT EXISTS banned (
            vk_id       INTEGER PRIMARY KEY,
            banned_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            created_at  TEXT    NOT NULL,
            UNIQUE(message_id, reporter_id)
        );

        CREATE TABLE IF NOT EXISTS ad_settings (
            id       INTEGER PRIMARY KEY CHECK (id = 1),
            enabled  INTEGER DEFAULT 0,
            text     TEXT    DEFAULT '',
            url      TEXT    DEFAULT '',
            btn_text TEXT    DEFAULT '📢 Реклама'
        );

        INSERT OR IGNORE INTO ad_settings (id, enabled, text, url, btn_text)
        VALUES (1, 0, '', '', '📢 Реклама');

        CREATE INDEX IF NOT EXISTS idx_msg_receiver ON messages(receiver_id);
        CREATE INDEX IF NOT EXISTS idx_msg_sender   ON messages(sender_id);
    """)

    # Миграция для place
    try:
        c.execute("ALTER TABLE ad_settings ADD COLUMN place TEXT DEFAULT 'AFTER_SEND'")
        logger.info("[DB] Добавлена колонка place в ad_settings")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            logger.warning(f"[DB] Ошибка миграции: {e}")

    c.execute("UPDATE ad_settings SET place = 'AFTER_SEND' WHERE place IS NULL")
    logger.info("[DB] SQLite инициализирован")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

def _now_ts() -> datetime:
    # Обязательно добавляем .replace(tzinfo=None) для работы с PostgreSQL
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─── USERS ────────────────────────────────────────────────────────────────────

async def get_or_create_user(vk_id: int, first_name: str = "", last_name: str = "") -> dict:
    if USE_SQLITE:
        return await _sqlite_get_or_create_user(vk_id, first_name, last_name)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE vk_id = $1", vk_id)
        now = _now_ts()

        if row:
            await conn.execute("UPDATE users SET last_active = $1 WHERE vk_id = $2", now, vk_id)
            res = dict(row)
            res["last_active"] = now
            return res

        await conn.execute("""
            INSERT INTO users (vk_id, first_name, last_name, created_at, last_active, notifications, is_banned, msg_count, link_clicks)
            VALUES ($1, $2, $3, $4, $5, 1, 0, 0, 0)
        """, vk_id, first_name, last_name, now, now)

        return {
            "vk_id": vk_id, "first_name": first_name, "last_name": last_name,
            "notifications": 1, "is_banned": 0, "msg_count": 0, "link_clicks": 0,
            "created_at": now, "last_active": now
        }
async def _sqlite_get_or_create_user(vk_id: int, first_name: str = "", last_name: str = "") -> dict:
    def _f():
        import sqlite3
        from config import DB_PATH
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM users WHERE vk_id=?", (vk_id,)).fetchone()
            if row:
                c.execute("UPDATE users SET last_active=? WHERE vk_id=?", (_now(), vk_id))
                return dict(row)
            now = _now()
            c.execute(
                "INSERT INTO users (vk_id,first_name,last_name,created_at,last_active) VALUES (?,?,?,?,?)",
                (vk_id, first_name, last_name, now, now)
            )
            return {"vk_id": vk_id, "first_name": first_name, "last_name": last_name,
                    "notifications": 1, "is_banned": 0, "msg_count": 0, "link_clicks": 0,
                    "created_at": now, "last_active": now}
    return await asyncio.to_thread(_f)


async def get_user(vk_id: int) -> dict | None:
    if USE_SQLITE:
        return await _sqlite_get_user(vk_id)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE vk_id = $1", vk_id)
        return dict(row) if row else None


async def _sqlite_get_user(vk_id: int) -> dict | None:
    def _f():
        import sqlite3
        from config import DB_PATH
        with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM users WHERE vk_id=?", (vk_id,)).fetchone()
            return dict(row) if row else None
    return await asyncio.to_thread(_f)


async def update_last_active(vk_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute("UPDATE users SET last_active=? WHERE vk_id=?", (_now(), vk_id))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_active = $1 WHERE vk_id = $2", _now_ts(), vk_id)


async def set_notifications(vk_id: int, val: bool):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute("UPDATE users SET notifications=? WHERE vk_id=?", (int(val), vk_id))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET notifications = $1 WHERE vk_id = $2", int(val), vk_id)


async def get_total_users() -> int:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM users")


async def get_all_users_for_broadcast() -> list[int]:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                rows = c.execute(
                    "SELECT vk_id FROM users WHERE is_banned=0 AND notifications=1"
                ).fetchall()
                return [r[0] for r in rows]
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT vk_id FROM users WHERE is_banned=0 AND notifications=1")
        return [r["vk_id"] for r in rows]


async def get_user_stats(vk_id: int) -> dict:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                incoming = c.execute(
                    "SELECT COUNT(*) FROM messages WHERE receiver_id=?", (vk_id,)
                ).fetchone()[0]
                outgoing = c.execute(
                    "SELECT COUNT(*) FROM messages WHERE sender_id=?", (vk_id,)
                ).fetchone()[0]
                replied = c.execute(
                    "SELECT COUNT(*) FROM messages WHERE receiver_id=? AND is_replied=1", (vk_id,)
                ).fetchone()[0]
                return {"incoming": incoming, "outgoing": outgoing, "replied": replied}
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        incoming = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE receiver_id = $1", vk_id)
        outgoing = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE sender_id = $1", vk_id)
        replied = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE receiver_id = $1 AND is_replied = 1", vk_id)
        return {"incoming": incoming, "outgoing": outgoing, "replied": replied}


# ─── MESSAGES ─────────────────────────────────────────────────────────────────

async def save_message(sender_id: int, receiver_id: int, text: str) -> dict:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                cur = c.execute(
                    "INSERT INTO messages (sender_id,receiver_id,text,created_at) VALUES (?,?,?,?)",
                    (sender_id, receiver_id, text, _now())
                )
                row_id = cur.lastrowid
                c.execute("UPDATE users SET msg_count=msg_count+1 WHERE vk_id=?", (receiver_id,))
                return {"id": row_id, "sender_id": sender_id, "receiver_id": receiver_id, "text": text}
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        now = _now_ts()  # Используем объект datetime с UTC
        row_id = await conn.fetchval("""
                INSERT INTO messages (sender_id, receiver_id, text, created_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, sender_id, receiver_id, text, now)  # Передаем объект, а не строку

        await conn.execute("UPDATE users SET msg_count = msg_count + 1 WHERE vk_id = $1", receiver_id)
        return {"id": row_id, "sender_id": sender_id, "receiver_id": receiver_id, "text": text}


async def get_message(msg_id: int) -> dict | None:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.row_factory = sqlite3.Row
                row = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
                return dict(row) if row else None
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM messages WHERE id = $1", msg_id)
        return dict(row) if row else None


async def mark_replied(msg_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute("UPDATE messages SET is_replied=1 WHERE id=?", (msg_id,))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE messages SET is_replied = 1 WHERE id = $1", msg_id)


async def mark_deleted(msg_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE messages SET is_deleted = 1 WHERE id = $1", msg_id)


async def get_last_messages(vk_id: int, limit: int = 5) -> list[dict]:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    "SELECT * FROM messages WHERE receiver_id=? AND is_deleted=0 ORDER BY created_at DESC LIMIT ?",
                    (vk_id, limit)
                ).fetchall()
                return [dict(r) for r in rows]
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM messages 
            WHERE receiver_id = $1 AND is_deleted = 0 
            ORDER BY created_at DESC LIMIT $2
        """, vk_id, limit)
        return [dict(r) for r in rows]


async def delete_old_messages(days: int = 30):
    # Добавляем timezone.utc, чтобы время было "aware"
    cutoff = _now_ts() - timedelta(days=days)

    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                # Для SQLite используем isoformat
                cur = c.execute("DELETE FROM messages WHERE created_at < ? AND is_deleted = 1", (cutoff.isoformat(),))
                return cur.rowcount

        deleted = await asyncio.to_thread(_f)
        logger.info(f"[DB] Удалено старых сообщений: {deleted}")
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        # Для PostgreSQL asyncpg сам поймет объект cutoff с таймзоной
        result = await conn.execute("DELETE FROM messages WHERE created_at < $1 AND is_deleted = 1", cutoff)
        logger.info(f"[DB] Очистка завершена: {result}")

# ─── BLOCKED ──────────────────────────────────────────────────────────────────

async def block_user(owner_id: int, blocked_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute(
                    "INSERT OR IGNORE INTO blocked (owner_id, blocked_id) VALUES (?,?)",
                    (owner_id, blocked_id)
                )
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO blocked (owner_id, blocked_id) 
            VALUES ($1, $2) 
            ON CONFLICT DO NOTHING
        """, owner_id, blocked_id)


async def unblock_user(owner_id: int, blocked_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute(
                    "DELETE FROM blocked WHERE owner_id=? AND blocked_id=?",
                    (owner_id, blocked_id)
                )
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked WHERE owner_id = $1 AND blocked_id = $2", owner_id, blocked_id)


async def is_blocked(owner_id: int, sender_id: int) -> bool:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                row = c.execute(
                    "SELECT 1 FROM blocked WHERE owner_id=? AND blocked_id=?",
                    (owner_id, sender_id)
                ).fetchone()
                return row is not None
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM blocked WHERE owner_id = $1 AND blocked_id = $2", owner_id, sender_id)
        return row is not None


async def get_blocked_list(owner_id: int) -> list[dict]:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    "SELECT blocked_id FROM blocked WHERE owner_id=?", (owner_id,)
                ).fetchall()
                return [dict(r) for r in rows]
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT blocked_id FROM blocked WHERE owner_id = $1", owner_id)
        return [dict(r) for r in rows]


# ─── BANNED ───────────────────────────────────────────────────────────────────

async def ban_user(vk_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute(
                    "INSERT OR IGNORE INTO banned (vk_id, banned_at) VALUES (?,?)",
                    (vk_id, _now())
                )
                c.execute("UPDATE users SET is_banned=1 WHERE vk_id=?", (vk_id,))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO banned (vk_id, banned_at) 
            VALUES ($1, $2) 
            ON CONFLICT DO NOTHING
        """, vk_id, _now_ts())
        await conn.execute("UPDATE users SET is_banned = 1 WHERE vk_id = $1", vk_id)


async def unban_user(vk_id: int):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute("DELETE FROM banned WHERE vk_id=?", (vk_id,))
                c.execute("UPDATE users SET is_banned=0 WHERE vk_id=?", (vk_id,))
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM banned WHERE vk_id = $1", vk_id)
        await conn.execute("UPDATE users SET is_banned = 0 WHERE vk_id = $1", vk_id)


async def is_banned(vk_id: int) -> bool:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                row = c.execute("SELECT 1 FROM banned WHERE vk_id=?", (vk_id,)).fetchone()
                return row is not None
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM banned WHERE vk_id = $1", vk_id)
        return row is not None


# ─── REPORTS ──────────────────────────────────────────────────────────────────

async def add_report(reporter_id: int, msg_id: int) -> int:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                try:
                    c.execute(
                        "INSERT INTO reports (message_id, reporter_id, created_at) VALUES (?,?,?)",
                        (msg_id, reporter_id, _now())
                    )
                except sqlite3.IntegrityError:
                    pass
                count = c.execute(
                    "SELECT COUNT(*) FROM reports WHERE message_id=?", (msg_id,)
                ).fetchone()[0]
                return count
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO reports (message_id, reporter_id, created_at)
                VALUES ($1, $2, $3)
            """, msg_id, reporter_id, _now_ts())
        except asyncpg.UniqueViolationError:
            pass
        count = await conn.fetchval("SELECT COUNT(*) FROM reports WHERE message_id = $1", msg_id)
        return count


async def has_reported(reporter_id: int, msg_id: int) -> bool:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                row = c.execute(
                    "SELECT 1 FROM reports WHERE message_id=? AND reporter_id=?",
                    (msg_id, reporter_id)
                ).fetchone()
                return row is not None
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM reports WHERE message_id = $1 AND reporter_id = $2", msg_id, reporter_id)
        return row is not None


# ─── AD ───────────────────────────────────────────────────────────────────────

async def get_ad() -> dict:
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.row_factory = sqlite3.Row
                row = c.execute("SELECT * FROM ad_settings WHERE id=1").fetchone()
                return dict(row) if row else {}
        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ad_settings WHERE id = 1")
        return dict(row) if row else {}


async def set_ad(**kwargs):
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values())
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.execute(f"UPDATE ad_settings SET {sets} WHERE id=1", vals)
        await asyncio.to_thread(_f)
        return

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        sets = ", ".join(f"{k}=${i+1}" for i, k in enumerate(kwargs.keys()))
        vals = list(kwargs.values())
        await conn.execute(f"UPDATE ad_settings SET {sets} WHERE id = 1", *vals)


async def is_ad_enabled() -> bool:
    ad = await get_ad()
    has_content = bool(ad.get("text", "").strip()) or bool(ad.get("url", "").strip())
    return bool(ad.get("enabled", 0)) and has_content


# ─── ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ (ДЛЯ TASKS И ADMIN) ──────────────────────────────

async def get_inactive_users(days: int = 3) -> list[dict]:
    """Возвращает список пользователей, которые не проявляли активность N дней."""
    cutoff = _now_ts() - timedelta(days=days)

    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                c.row_factory = sqlite3.Row
                # cutoff_str для сравнения в SQLite
                cutoff_str = cutoff.isoformat()
                rows = c.execute(
                    "SELECT * FROM users WHERE last_active < ? AND is_banned = 0 AND notifications = 1",
                    (cutoff_str,)
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM users WHERE last_active < $1 AND is_banned = 0 AND notifications = 1",
            cutoff
        )
        return [dict(r) for r in rows]


async def get_db_stats() -> dict:
    """Общая статистика для админ-панели."""
    if USE_SQLITE:
        def _f():
            import sqlite3
            from config import DB_PATH
            with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
                users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                msgs = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                banned = c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
                return {"users": users, "messages": msgs, "banned": banned}

        return await asyncio.to_thread(_f)

    pool = await DatabasePool.get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        msgs = await conn.fetchval("SELECT COUNT(*) FROM messages")
        banned = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        return {"users": users, "messages": msgs, "banned": banned}


# ─── ЗАКРЫТИЕ ПУЛА ───────────────────────────────────────────────────────────

async def close_db():
    """Закрывает пул соединений (вызывать при остановке бота)"""
    if not USE_SQLITE:
        await DatabasePool.close()
    logger.info("Соединение с БД закрыто")


def get_conn():
    """Вспомогательная функция для legacy-запросов (SQLite)"""
    if USE_SQLITE:
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    return None