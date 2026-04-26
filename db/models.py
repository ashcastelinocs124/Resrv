"""Database query helpers — thin wrappers around raw SQL."""

from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any

import aiosqlite

from db.database import get_db

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ── Helpers ──────────────────────────────────────────────────────────────

def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ── Machines ─────────────────────────────────────────────────────────────

async def get_machines() -> list[dict[str, Any]]:
    """Active (non-archived) machines."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM machines WHERE archived_at IS NULL ORDER BY id"
    )
    return _rows_to_dicts(await cursor.fetchall())


async def list_machines(include_archived: bool = False) -> list[dict[str, Any]]:
    db = await get_db()
    sql = "SELECT * FROM machines"
    if not include_archived:
        sql += " WHERE archived_at IS NULL"
    sql += " ORDER BY id"
    cursor = await db.execute(sql)
    return _rows_to_dicts(await cursor.fetchall())


async def create_machine(*, name: str, slug: str) -> dict[str, Any]:
    if not _SLUG_RE.match(slug):
        raise ValueError(f"Invalid slug: {slug!r}")
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM machines WHERE slug = ? AND archived_at IS NULL",
        (slug,),
    )
    if await cursor.fetchone():
        raise ValueError(f"Slug already in use: {slug!r}")
    cursor = await db.execute(
        "INSERT INTO machines (name, slug) VALUES (?, ?) RETURNING *",
        (name, slug),
    )
    row = dict(await cursor.fetchone())
    await db.execute(
        "INSERT INTO machine_units (machine_id, label) VALUES (?, 'Main')",
        (row["id"],),
    )
    await db.commit()
    return row


async def update_machine(
    machine_id: int,
    *,
    name: str | None = None,
    slug: str | None = None,
    status: str | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if slug is not None:
        if not _SLUG_RE.match(slug):
            raise ValueError(f"Invalid slug: {slug!r}")
        db = await get_db()
        cur = await db.execute(
            "SELECT 1 FROM machines "
            "WHERE slug = ? AND archived_at IS NULL AND id != ?",
            (slug, machine_id),
        )
        if await cur.fetchone():
            raise ValueError(f"Slug already in use: {slug!r}")
        sets.append("slug = ?")
        params.append(slug)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    params.append(machine_id)
    db = await get_db()
    await db.execute(
        f"UPDATE machines SET {', '.join(sets)} WHERE id = ?", params
    )
    await db.commit()


async def archive_machine(machine_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE machines SET archived_at = datetime('now'), "
        "embed_message_id = NULL WHERE id = ?",
        (machine_id,),
    )
    await db.commit()


async def restore_machine(machine_id: int) -> None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT slug FROM machines WHERE id = ?", (machine_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("Machine not found")
    cursor = await db.execute(
        "SELECT 1 FROM machines WHERE slug = ? AND archived_at IS NULL AND id != ?",
        (row["slug"], machine_id),
    )
    if await cursor.fetchone():
        raise ValueError(f"Slug already taken: {row['slug']!r}")
    await db.execute(
        "UPDATE machines SET archived_at = NULL WHERE id = ?", (machine_id,)
    )
    await db.commit()


async def purge_machine(machine_id: int) -> dict[str, int]:
    """Hard-delete machine + cascade queue_entries + analytics_snapshots + units."""
    db = await get_db()
    qe = await db.execute(
        "DELETE FROM queue_entries WHERE machine_id = ?", (machine_id,)
    )
    qe_count = qe.rowcount
    snap = await db.execute(
        "DELETE FROM analytics_snapshots WHERE machine_id = ?", (machine_id,)
    )
    snap_count = snap.rowcount
    u = await db.execute(
        "DELETE FROM machine_units WHERE machine_id = ?", (machine_id,)
    )
    unit_count = u.rowcount
    await db.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
    await db.commit()
    return {
        "queue_entries": qe_count,
        "analytics_snapshots": snap_count,
        "machine_units": unit_count,
    }


async def count_active_queue_entries(machine_id: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM queue_entries "
        "WHERE machine_id = ? AND status IN ('waiting', 'serving')",
        (machine_id,),
    )
    row = await cursor.fetchone()
    return row["cnt"]


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


# ── Machine Units ────────────────────────────────────────────────────────

async def list_units(
    machine_id: int, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    db = await get_db()
    sql = "SELECT * FROM machine_units WHERE machine_id = ?"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY id"
    cursor = await db.execute(sql, (machine_id,))
    return _rows_to_dicts(await cursor.fetchall())


async def get_unit(unit_id: int) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM machine_units WHERE id = ?", (unit_id,)
    )
    return _row_to_dict(await cursor.fetchone())


def _validate_label(label: str) -> str:
    stripped = (label or "").strip()
    if not (1 <= len(stripped) <= 64):
        raise ValueError("label must be 1–64 characters")
    return stripped


async def create_unit(*, machine_id: int, label: str) -> dict[str, Any]:
    label = _validate_label(label)
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM machine_units "
        "WHERE machine_id = ? AND label = ? AND archived_at IS NULL",
        (machine_id, label),
    )
    if await cursor.fetchone():
        raise ValueError(f"label already in use: {label!r}")
    cursor = await db.execute(
        "INSERT INTO machine_units (machine_id, label) VALUES (?, ?) RETURNING *",
        (machine_id, label),
    )
    row = dict(await cursor.fetchone())
    await db.commit()
    return row


async def update_unit(
    unit_id: int,
    *,
    label: str | None = None,
    status: str | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if label is not None:
        label = _validate_label(label)
        db = await get_db()
        cur = await db.execute(
            """
            SELECT 1 FROM machine_units
            WHERE machine_id = (SELECT machine_id FROM machine_units WHERE id = ?)
              AND label = ? AND archived_at IS NULL AND id != ?
            """,
            (unit_id, label, unit_id),
        )
        if await cur.fetchone():
            raise ValueError(f"label already in use: {label!r}")
        sets.append("label = ?")
        params.append(label)
    if status is not None:
        if status not in {"active", "maintenance"}:
            raise ValueError(f"invalid status: {status!r}")
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    params.append(unit_id)
    db = await get_db()
    await db.execute(
        f"UPDATE machine_units SET {', '.join(sets)} WHERE id = ?", params
    )
    await db.commit()


async def archive_unit(unit_id: int) -> None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM queue_entries WHERE unit_id = ? AND status = 'serving'",
        (unit_id,),
    )
    if await cursor.fetchone():
        raise ValueError("unit has an active serving entry")
    await db.execute(
        "UPDATE machine_units SET archived_at = datetime('now') WHERE id = ?",
        (unit_id,),
    )
    await db.commit()


async def restore_unit(unit_id: int) -> None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT machine_id, label FROM machine_units WHERE id = ?", (unit_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("unit not found")
    clash = await db.execute(
        "SELECT 1 FROM machine_units "
        "WHERE machine_id = ? AND label = ? AND archived_at IS NULL AND id != ?",
        (row["machine_id"], row["label"], unit_id),
    )
    if await clash.fetchone():
        raise ValueError(f"label already in use: {row['label']!r}")
    await db.execute(
        "UPDATE machine_units SET archived_at = NULL WHERE id = ?", (unit_id,)
    )
    await db.commit()


async def count_active_units(machine_id: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM machine_units "
        "WHERE machine_id = ? AND status = 'active' AND archived_at IS NULL",
        (machine_id,),
    )
    return (await cursor.fetchone())["cnt"]


async def count_serving_on_machine(machine_id: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM queue_entries "
        "WHERE machine_id = ? AND status = 'serving' "
        "AND date(joined_at) = date('now')",
        (machine_id,),
    )
    return (await cursor.fetchone())["cnt"]


async def first_available_unit(machine_id: int) -> dict[str, Any] | None:
    """First active unit with no serving entry today."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT u.* FROM machine_units u
        WHERE u.machine_id = ?
          AND u.status = 'active'
          AND u.archived_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM queue_entries qe
              WHERE qe.unit_id = u.id
                AND qe.status = 'serving'
                AND date(qe.joined_at) = date('now')
          )
        ORDER BY u.id ASC
        LIMIT 1
        """,
        (machine_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def purge_unit(unit_id: int) -> None:
    """Hard-delete a unit. Nulls unit_id on historical queue_entries first."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM queue_entries WHERE unit_id = ? AND status = 'serving'",
        (unit_id,),
    )
    if await cursor.fetchone():
        raise ValueError("unit has an active serving entry")
    await db.execute(
        "UPDATE queue_entries SET unit_id = NULL WHERE unit_id = ?", (unit_id,)
    )
    await db.execute("DELETE FROM machine_units WHERE id = ?", (unit_id,))
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


async def register_user(
    user_id: int,
    *,
    full_name: str,
    email: str,
    major: str,
    college_id: int | None,
    graduation_year: str,
) -> None:
    """Save signup profile and mark user as registered."""
    db = await get_db()
    await db.execute(
        """
        UPDATE users
        SET full_name = ?, email = ?, major = ?, college_id = ?,
            graduation_year = ?, registered = 1
        WHERE id = ?
        """,
        (full_name, email, major, college_id, graduation_year, user_id),
    )
    await db.commit()


async def update_user_profile(
    user_id: int,
    *,
    full_name: str,
    email: str,
    major: str,
    college_id: int | None,
    graduation_year: str,
) -> None:
    """Update an existing user's profile fields."""
    db = await get_db()
    await db.execute(
        """
        UPDATE users
        SET full_name = ?, email = ?, major = ?, college_id = ?,
            graduation_year = ?
        WHERE id = ?
        """,
        (full_name, email, major, college_id, graduation_year, user_id),
    )
    await db.commit()


# ── Staff Users ──────────────────────────────────────────────────────────

async def list_staff() -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, role, created_at FROM staff_users ORDER BY id"
    )
    return _rows_to_dicts(await cursor.fetchall())


async def get_staff(staff_id: int) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, role, created_at FROM staff_users WHERE id = ?",
        (staff_id,),
    )
    return _row_to_dict(await cursor.fetchone())


async def get_staff_by_username(username: str) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, role, created_at, password_hash "
        "FROM staff_users WHERE username = ?",
        (username,),
    )
    return _row_to_dict(await cursor.fetchone())


async def create_staff(
    username: str, password_hash: str, role: str
) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) "
        "VALUES (?, ?, ?) "
        "RETURNING id, username, role, created_at",
        (username, password_hash, role),
    )
    row = dict(await cursor.fetchone())
    await db.commit()
    return row


async def update_staff(
    staff_id: int,
    *,
    role: str | None = None,
    password_hash: str | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if role is not None:
        sets.append("role = ?")
        params.append(role)
    if password_hash is not None:
        sets.append("password_hash = ?")
        params.append(password_hash)
    if not sets:
        return
    params.append(staff_id)
    db = await get_db()
    await db.execute(
        f"UPDATE staff_users SET {', '.join(sets)} WHERE id = ?", params
    )
    await db.commit()


async def delete_staff(staff_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM staff_users WHERE id = ?", (staff_id,))
    await db.commit()


async def count_admins() -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM staff_users WHERE role = 'admin'"
    )
    row = await cursor.fetchone()
    return row["cnt"]


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


async def get_user_active_entries(user_id: int) -> list[dict[str, Any]]:
    """Get ALL active entries for a user across all machines today."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name, m.name as machine_name, m.slug as machine_slug
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        JOIN machines m ON m.id = qe.machine_id
        WHERE qe.user_id = ?
          AND qe.status IN ('waiting', 'serving')
          AND date(qe.joined_at) = date('now')
        ORDER BY qe.position ASC
        """,
        (user_id,),
    )
    return _rows_to_dicts(await cursor.fetchall())


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


async def reset_reminder(entry_id: int) -> None:
    """Reset the reminded flag so the timer restarts."""
    db = await get_db()
    await db.execute(
        "UPDATE queue_entries SET reminded = 0 WHERE id = ?", (entry_id,)
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


# ── Analytics ───────────────────────────────────────────────────────────


async def insert_analytics_snapshot(
    *,
    date: str,
    machine_id: int,
    total_jobs: int,
    completed_jobs: int,
    avg_wait_mins: float | None,
    avg_serve_mins: float | None,
    peak_hour: int | None,
    ai_summary: str | None,
    no_show_count: int,
    cancelled_count: int,
    unique_users: int,
    failure_count: int,
) -> None:
    """Insert a single analytics snapshot row."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO analytics_snapshots
            (date, machine_id, total_jobs, completed_jobs, avg_wait_mins,
             avg_serve_mins, peak_hour, ai_summary, no_show_count,
             cancelled_count, unique_users, failure_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, machine_id, total_jobs, completed_jobs, avg_wait_mins,
         avg_serve_mins, peak_hour, ai_summary, no_show_count,
         cancelled_count, unique_users, failure_count),
    )
    await db.commit()


async def get_analytics_snapshots(
    *,
    start_date: str,
    end_date: str,
    machine_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get analytics snapshots for a date range, optionally filtered by machine."""
    db = await get_db()
    sql = """
        SELECT s.*, m.name as machine_name, m.slug as machine_slug
        FROM analytics_snapshots s
        JOIN machines m ON m.id = s.machine_id
        WHERE s.date >= ? AND s.date <= ?
    """
    params: list[Any] = [start_date, end_date]
    if machine_id is not None:
        sql += " AND s.machine_id = ?"
        params.append(machine_id)
    sql += " ORDER BY s.date ASC, s.machine_id ASC"
    cursor = await db.execute(sql, params)
    return _rows_to_dicts(await cursor.fetchall())


# ── Chat ─────────────────────────────────────────────────────────────────


async def create_conversation(
    *, staff_user_id: int, first_message: str
) -> dict[str, Any]:
    title = (first_message or "New chat").strip()[:60] or "New chat"
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO chat_conversations (staff_user_id, title) "
        "VALUES (?, ?) RETURNING *",
        (staff_user_id, title),
    )
    row = dict(await cursor.fetchone())
    await db.commit()
    return row


async def list_conversations(staff_user_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, title, created_at, updated_at "
        "FROM chat_conversations "
        "WHERE staff_user_id = ? "
        "ORDER BY updated_at DESC",
        (staff_user_id,),
    )
    return _rows_to_dicts(await cursor.fetchall())


async def get_conversation(
    conversation_id: int, *, staff_user_id: int
) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM chat_conversations WHERE id = ? AND staff_user_id = ?",
        (conversation_id, staff_user_id),
    )
    return _row_to_dict(await cursor.fetchone())


async def get_conversation_messages(
    conversation_id: int, *, staff_user_id: int
) -> list[dict[str, Any]] | None:
    """Return all messages for a conversation owned by this staff user.

    Returns None when the conversation doesn't exist or isn't owned by the
    caller — distinct from "exists but empty" so the API can 404.
    """
    if await get_conversation(
        conversation_id, staff_user_id=staff_user_id
    ) is None:
        return None
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM chat_messages "
        "WHERE conversation_id = ? "
        "ORDER BY id ASC",
        (conversation_id,),
    )
    return _rows_to_dicts(await cursor.fetchall())


async def get_recent_messages(
    conversation_id: int, *, limit: int = 8
) -> list[dict[str, Any]]:
    """Most-recent ``limit`` messages, returned oldest-first."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM chat_messages "
        "WHERE conversation_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (conversation_id, limit),
    )
    rows = _rows_to_dicts(await cursor.fetchall())
    return list(reversed(rows))


async def append_message(
    conversation_id: int,
    *,
    role: str,
    content: str,
    tool_call_id: str | None = None,
    tool_calls_json: str | None = None,
) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO chat_messages "
        "(conversation_id, role, content, tool_call_id, tool_calls_json) "
        "VALUES (?, ?, ?, ?, ?) RETURNING *",
        (conversation_id, role, content, tool_call_id, tool_calls_json),
    )
    row = dict(await cursor.fetchone())
    await db.execute(
        "UPDATE chat_conversations SET updated_at = datetime('now') WHERE id = ?",
        (conversation_id,),
    )
    await db.commit()
    return row


async def delete_conversation(
    conversation_id: int, *, staff_user_id: int
) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM chat_conversations WHERE id = ? AND staff_user_id = ?",
        (conversation_id, staff_user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


# ── Analytics ───────────────────────────────────────────────────────────


async def compute_live_today_stats() -> list[dict[str, Any]]:
    """Compute analytics for today from live queue_entries data."""
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT
            qe.machine_id,
            m.name as machine_name,
            m.slug as machine_slug,
            COUNT(*) as total_jobs,
            SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END) as completed_jobs,
            SUM(CASE WHEN qe.status = 'no_show' THEN 1 ELSE 0 END) as no_show_count,
            SUM(CASE WHEN qe.status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_count,
            SUM(CASE WHEN qe.job_successful = 0 THEN 1 ELSE 0 END) as failure_count,
            COUNT(DISTINCT qe.user_id) as unique_users,
            AVG(CASE
                WHEN qe.serving_at IS NOT NULL
                THEN (julianday(qe.serving_at) - julianday(qe.joined_at)) * 24 * 60
            END) as avg_wait_mins,
            AVG(CASE
                WHEN qe.completed_at IS NOT NULL AND qe.serving_at IS NOT NULL
                THEN (julianday(qe.completed_at) - julianday(qe.serving_at)) * 24 * 60
            END) as avg_serve_mins
        FROM queue_entries qe
        JOIN machines m ON m.id = qe.machine_id
        WHERE date(qe.joined_at) = date('now')
        GROUP BY qe.machine_id
        ORDER BY qe.machine_id
        """
    )
    rows = _rows_to_dicts(await cursor.fetchall())
    for row in rows:
        peak_cursor = await db.execute(
            """
            SELECT CAST(strftime('%H', qe.joined_at) AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM queue_entries qe
            WHERE qe.machine_id = ? AND date(qe.joined_at) = date('now')
            GROUP BY hour ORDER BY cnt DESC LIMIT 1
            """,
            (row["machine_id"],),
        )
        peak_row = await peak_cursor.fetchone()
        row["peak_hour"] = dict(peak_row)["hour"] if peak_row else None
    return rows


# ── Colleges ─────────────────────────────────────────────────────────────


class DuplicateCollegeError(Exception):
    """Raised when creating/restoring a college that conflicts with an active row."""


class CollegeInUseError(Exception):
    """Raised when purging a college that still has users referencing it."""


async def create_college(name: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO colleges (name) VALUES (?) RETURNING *", (name,)
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise DuplicateCollegeError(name) from e
        raise
    row = await cursor.fetchone()
    await db.commit()
    return dict(row)


async def list_active_colleges() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM colleges WHERE archived_at IS NULL ORDER BY name"
    )
    return [dict(r) for r in await cursor.fetchall()]


async def list_all_colleges() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM colleges ORDER BY archived_at IS NULL DESC, name"
    )
    return [dict(r) for r in await cursor.fetchall()]


async def get_college(college_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM colleges WHERE id = ?", (college_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_college(college_id: int, *, name: str) -> dict | None:
    db = await get_db()
    try:
        await db.execute(
            "UPDATE colleges SET name = ? WHERE id = ?", (name, college_id)
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise DuplicateCollegeError(name) from e
        raise
    await db.commit()
    return await get_college(college_id)


async def archive_college(college_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE colleges SET archived_at = datetime('now') "
        "WHERE id = ? AND archived_at IS NULL",
        (college_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def restore_college(college_id: int) -> bool:
    db = await get_db()
    target = await get_college(college_id)
    if target is None:
        return False
    # 409-equivalent: refuse if an active twin exists with the same name
    cursor = await db.execute(
        "SELECT 1 FROM colleges WHERE name = ? AND archived_at IS NULL AND id != ?",
        (target["name"], college_id),
    )
    if await cursor.fetchone():
        raise DuplicateCollegeError(target["name"])
    cursor = await db.execute(
        "UPDATE colleges SET archived_at = NULL "
        "WHERE id = ? AND archived_at IS NOT NULL",
        (college_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def count_users_in_college(college_id: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE college_id = ?", (college_id,)
    )
    row = await cursor.fetchone()
    return row["cnt"]


async def purge_college(college_id: int) -> bool:
    if await count_users_in_college(college_id) > 0:
        raise CollegeInUseError(college_id)
    db = await get_db()
    cursor = await db.execute("DELETE FROM colleges WHERE id = ?", (college_id,))
    await db.commit()
    return cursor.rowcount > 0

