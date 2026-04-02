"""Database query helpers — thin wrappers around raw SQL."""

from __future__ import annotations

from datetime import datetime, date
from typing import Any

import aiosqlite

from db.database import get_db


# ── Helpers ──────────────────────────────────────────────────────────────

def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ── Machines ─────────────────────────────────────────────────────────────

async def get_machines() -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM machines ORDER BY id")
    return _rows_to_dicts(await cursor.fetchall())


async def get_machine(machine_id: int) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM machines WHERE id = ?", (machine_id,))
    return _row_to_dict(await cursor.fetchone())


async def get_machine_by_slug(slug: str) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM machines WHERE slug = ?", (slug,))
    return _row_to_dict(await cursor.fetchone())


async def update_machine_embed_message_id(
    machine_id: int, message_id: int | None
) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE machines SET embed_message_id = ? WHERE id = ?",
        (str(message_id) if message_id else None, machine_id),
    )
    await db.commit()


async def update_machine_status(machine_id: int, status: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE machines SET status = ? WHERE id = ?", (status, machine_id)
    )
    await db.commit()


# ── Users ────────────────────────────────────────────────────────────────

async def get_or_create_user(discord_id: str, discord_name: str) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)

    cursor = await db.execute(
        "INSERT INTO users (discord_id, discord_name) VALUES (?, ?) RETURNING *",
        (discord_id, discord_name),
    )
    user = dict(await cursor.fetchone())
    await db.commit()
    return user


async def get_user_by_discord_id(discord_id: str) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
    )
    return _row_to_dict(await cursor.fetchone())


# ── Queue Entries ────────────────────────────────────────────────────────

async def get_queue_for_machine(
    machine_id: int, *, today_only: bool = True
) -> list[dict[str, Any]]:
    """Active queue entries (waiting + serving) for a machine, ordered by position."""
    db = await get_db()
    sql = """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.machine_id = ?
          AND qe.status IN ('waiting', 'serving')
    """
    params: list[Any] = [machine_id]
    if today_only:
        sql += " AND date(qe.joined_at) = date('now')"
    sql += " ORDER BY qe.position ASC"
    cursor = await db.execute(sql, params)
    return _rows_to_dicts(await cursor.fetchall())


async def get_user_active_entry(
    user_id: int, machine_id: int
) -> dict[str, Any] | None:
    """Check if user already has an active entry for this machine today."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT * FROM queue_entries
        WHERE user_id = ? AND machine_id = ?
          AND status IN ('waiting', 'serving')
          AND date(joined_at) = date('now')
        """,
        (user_id, machine_id),
    )
    return _row_to_dict(await cursor.fetchone())


async def join_queue(user_id: int, machine_id: int) -> dict[str, Any]:
    """Add user to the end of a machine's queue. Returns the new entry."""
    db = await get_db()
    # Get next position
    cursor = await db.execute(
        """
        SELECT COALESCE(MAX(position), 0) + 1 AS next_pos
        FROM queue_entries
        WHERE machine_id = ? AND date(joined_at) = date('now')
        """,
        (machine_id,),
    )
    row = await cursor.fetchone()
    next_pos = row["next_pos"]

    cursor = await db.execute(
        """
        INSERT INTO queue_entries (user_id, machine_id, status, position)
        VALUES (?, ?, 'waiting', ?)
        RETURNING *
        """,
        (user_id, machine_id, next_pos),
    )
    entry = dict(await cursor.fetchone())
    await db.commit()
    return entry


async def leave_queue(entry_id: int) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE queue_entries
        SET status = 'cancelled', completed_at = datetime('now')
        WHERE id = ?
        """,
        (entry_id,),
    )
    await db.commit()


async def update_entry_status(
    entry_id: int,
    status: str,
    **extra_fields: Any,
) -> None:
    db = await get_db()
    sets = ["status = ?"]
    params: list[Any] = [status]

    if status == "serving":
        sets.append("serving_at = datetime('now')")
    if status in ("completed", "no_show", "cancelled"):
        sets.append("completed_at = datetime('now')")

    for field, value in extra_fields.items():
        sets.append(f"{field} = ?")
        params.append(value)

    params.append(entry_id)
    await db.execute(
        f"UPDATE queue_entries SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()


async def bump_entry_to_top(entry_id: int, machine_id: int) -> None:
    """Move an entry to position 0 (top of queue)."""
    db = await get_db()
    # Shift everyone else down
    await db.execute(
        """
        UPDATE queue_entries
        SET position = position + 1
        WHERE machine_id = ? AND status = 'waiting'
          AND date(joined_at) = date('now')
        """,
        (machine_id,),
    )
    await db.execute(
        "UPDATE queue_entries SET position = 1 WHERE id = ?", (entry_id,)
    )
    await db.commit()


async def get_serving_entry(machine_id: int) -> dict[str, Any] | None:
    """Get the currently serving entry for a machine."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.machine_id = ? AND qe.status = 'serving'
          AND date(qe.joined_at) = date('now')
        LIMIT 1
        """,
        (machine_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def get_next_waiting(machine_id: int) -> dict[str, Any] | None:
    """Get the next waiting entry (lowest position) for a machine."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.machine_id = ? AND qe.status = 'waiting'
          AND date(qe.joined_at) = date('now')
        ORDER BY qe.position ASC
        LIMIT 1
        """,
        (machine_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def get_entries_needing_reminder(
    reminder_minutes: int,
) -> list[dict[str, Any]]:
    """Get serving entries that have been serving for longer than reminder_minutes
    and haven't been reminded yet."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.status = 'serving'
          AND qe.reminded = 0
          AND (julianday('now') - julianday(qe.serving_at)) * 24 * 60 >= ?
        """,
        (reminder_minutes,),
    )
    return _rows_to_dicts(await cursor.fetchall())


async def get_entries_past_grace(
    reminder_minutes: int, grace_minutes: int
) -> list[dict[str, Any]]:
    """Get serving entries past reminder + grace period that were reminded."""
    db = await get_db()
    total_minutes = reminder_minutes + grace_minutes
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.status = 'serving'
          AND qe.reminded = 1
          AND (julianday('now') - julianday(qe.serving_at)) * 24 * 60 >= ?
        """,
        (total_minutes,),
    )
    return _rows_to_dicts(await cursor.fetchall())


async def mark_reminded(entry_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE queue_entries SET reminded = 1 WHERE id = ?", (entry_id,)
    )
    await db.commit()


async def get_waiting_count(machine_id: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT COUNT(*) as cnt FROM queue_entries
        WHERE machine_id = ? AND status = 'waiting'
          AND date(joined_at) = date('now')
        """,
        (machine_id,),
    )
    row = await cursor.fetchone()
    return row["cnt"]


async def reset_stale_queues() -> int:
    """Cancel all waiting/serving entries from previous days. Returns count."""
    db = await get_db()
    cursor = await db.execute(
        """
        UPDATE queue_entries
        SET status = 'cancelled', completed_at = datetime('now')
        WHERE status IN ('waiting', 'serving')
          AND date(joined_at) < date('now')
        """
    )
    await db.commit()
    return cursor.rowcount
