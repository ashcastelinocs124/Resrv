"""Runtime settings store with a short-lived TTL cache.

Reads go through the cache; writes invalidate. Agent/bot can call
``get_setting_int`` on every tick without hammering SQLite.
"""

from __future__ import annotations

import time

from db.database import get_db

_TTL_SECONDS = 10.0
_cache: dict[str, tuple[float, str]] = {}


async def get_setting(key: str) -> str | None:
    now = time.monotonic()
    entry = _cache.get(key)
    if entry and now - entry[0] < _TTL_SECONDS:
        return entry[1]
    db = await get_db()
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    if row is None:
        return None
    _cache[key] = (now, row["value"])
    return row["value"]


async def get_setting_int(key: str, default: int = 0) -> int:
    val = await get_setting(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


async def get_setting_bool(key: str, default: bool = False) -> bool:
    val = await get_setting(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


async def set_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET "
        "  value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )
    await db.commit()
    _cache.pop(key, None)


def invalidate_settings_cache() -> None:
    _cache.clear()


async def get_all_settings() -> dict[str, str]:
    db = await get_db()
    cursor = await db.execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in await cursor.fetchall()}
