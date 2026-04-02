"""Integration tests for the DM cog intent handlers (_do_action)."""

from __future__ import annotations

import pytest

from db import models
from db.database import get_db, init_db, close_db
from bot.cogs.dm import DMCog

pytestmark = pytest.mark.asyncio


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def db():
    conn = await init_db()
    yield conn
    await close_db()


async def _make_entry_dict(
    db, user_id: int, machine_id: int, entry_id: int
) -> dict:
    """Build the entry dict that _do_action expects (matches get_user_active_entries shape)."""
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name,
               m.name as machine_name, m.slug as machine_slug
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        JOIN machines m ON m.id = qe.machine_id
        WHERE qe.id = ?
        """,
        (entry_id,),
    )
    row = await cursor.fetchone()
    return dict(row)


# ── Tests ───────────────────────────────────────────────────────────────


async def test_done_while_serving(db):
    """'done' on a serving entry marks it completed."""
    user = await models.get_or_create_user("dm1", "DMUser1")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")

    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("done", entry_dict)

    assert "done" in result.lower() or "Done" in result

    cursor = await db.execute(
        "SELECT status FROM queue_entries WHERE id = ?", (entry["id"],)
    )
    row = await cursor.fetchone()
    assert row["status"] == "completed"


async def test_done_while_waiting(db):
    """'done' on a waiting entry treats it as leave (cancelled)."""
    user = await models.get_or_create_user("dm2", "DMUser2")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])

    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("done", entry_dict)

    assert "removed" in result.lower()

    cursor = await db.execute(
        "SELECT status FROM queue_entries WHERE id = ?", (entry["id"],)
    )
    row = await cursor.fetchone()
    assert row["status"] == "cancelled"


async def test_more_time_resets_reminder(db):
    """'more_time' on a serving entry with reminded=1 resets to reminded=0."""
    user = await models.get_or_create_user("dm3", "DMUser3")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.mark_reminded(entry["id"])

    # Verify reminded is 1 before action
    cursor = await db.execute(
        "SELECT reminded FROM queue_entries WHERE id = ?", (entry["id"],)
    )
    row = await cursor.fetchone()
    assert row["reminded"] == 1

    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("more_time", entry_dict)

    assert "reset" in result.lower() or "timer" in result.lower()

    cursor = await db.execute(
        "SELECT reminded FROM queue_entries WHERE id = ?", (entry["id"],)
    )
    row = await cursor.fetchone()
    assert row["reminded"] == 0


async def test_leave_cancels_entry(db):
    """'leave' cancels the entry."""
    user = await models.get_or_create_user("dm4", "DMUser4")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])

    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("leave", entry_dict)

    assert "removed" in result.lower()

    cursor = await db.execute(
        "SELECT status FROM queue_entries WHERE id = ?", (entry["id"],)
    )
    row = await cursor.fetchone()
    assert row["status"] == "cancelled"


async def test_check_position_while_waiting(db):
    """'check_position' while waiting returns string containing '#1'."""
    user = await models.get_or_create_user("dm5", "DMUser5")
    machine = await models.get_machine_by_slug("laser-cutter")
    await models.join_queue(user["id"], machine["id"])

    entry = await models.join_queue(
        (await models.get_or_create_user("dm5x", "Placeholder"))["id"],
        machine["id"],
    )
    # Actually we want to test dm5's position, not the placeholder.
    # Let's redo: dm5 joins first, so they're #1.
    # Clean up: cancel placeholder entry and re-fetch dm5's entry.
    await models.leave_queue(entry["id"])

    user_entry = await models.get_user_active_entry(user["id"], machine["id"])
    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], user_entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("check_position", entry_dict)

    assert "#1" in result


async def test_check_position_while_serving(db):
    """'check_position' while serving returns string containing 'served'."""
    user = await models.get_or_create_user("dm6", "DMUser6")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")

    entry_dict = await _make_entry_dict(db, user["id"], machine["id"], entry["id"])

    cog = DMCog.__new__(DMCog)
    result = await cog._do_action("check_position", entry_dict)

    assert "served" in result.lower()
