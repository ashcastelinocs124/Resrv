"""Tests for the queue agent logic (state transitions)."""

from __future__ import annotations

import pytest

from db import models
from agent.loop import (
    _process_machines,
    _send_reminders,
    _expire_grace_period,
    _daily_reset,
)

# Override the agent's _bot so DM calls are no-ops.
import agent.loop as agent_mod

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace _bot with None so DM-sending is skipped in tests."""
    monkeypatch.setattr(agent_mod, "_bot", None)


# ── Queue advancement ────────────────────────────────────────────────────


async def test_advance_queue_serves_next(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    await models.join_queue(user["id"], machine["id"])

    # No one serving, Alice is waiting → agent should advance her
    await _process_machines()

    serving = await models.get_serving_entry(machine["id"])
    assert serving is not None
    assert serving["discord_name"] == "Alice"


async def test_advance_queue_does_not_serve_when_occupied(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")

    e1 = await models.join_queue(u1["id"], machine["id"])
    await models.join_queue(u2["id"], machine["id"])

    # Manually serve Alice
    await models.update_entry_status(e1["id"], "serving")

    # Agent should NOT advance Bob while Alice is serving
    await _process_machines()

    queue = await models.get_queue_for_machine(machine["id"])
    waiting = [e for e in queue if e["status"] == "waiting"]
    assert len(waiting) == 1
    assert waiting[0]["discord_name"] == "Bob"


async def test_advance_queue_skips_paused_machine(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    await models.update_machine_status(machine["id"], "maintenance")

    user = await models.get_or_create_user("1", "Alice")
    await models.join_queue(user["id"], machine["id"])

    await _process_machines()

    # Alice should still be waiting (machine is paused)
    serving = await models.get_serving_entry(machine["id"])
    assert serving is None


async def test_advance_queue_fifo_order(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")
    u3 = await models.get_or_create_user("3", "Charlie")

    await models.join_queue(u1["id"], machine["id"])
    await models.join_queue(u2["id"], machine["id"])
    await models.join_queue(u3["id"], machine["id"])

    # First tick: Alice gets served
    await _process_machines()
    serving = await models.get_serving_entry(machine["id"])
    assert serving["discord_name"] == "Alice"

    # Complete Alice, then tick: Bob gets served
    await models.update_entry_status(serving["id"], "completed")
    await _process_machines()
    serving = await models.get_serving_entry(machine["id"])
    assert serving["discord_name"] == "Bob"


# ── Reminders ────────────────────────────────────────────────────────────


async def test_reminder_marks_entry(db, monkeypatch):
    """Entries serving for > reminder_minutes should be marked reminded."""
    monkeypatch.setattr("config.settings.reminder_minutes", 30)

    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")

    # Manually backdate serving_at to 35 minutes ago
    conn = await models.get_db()
    await conn.execute(
        """
        UPDATE queue_entries
        SET serving_at = datetime('now', '-35 minutes')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await conn.commit()

    await _send_reminders()

    # Check that the entry was marked as reminded
    updated = await _get_entry(entry["id"])
    assert updated["reminded"] == 1


async def test_no_reminder_if_too_early(db, monkeypatch):
    """Entries serving for < reminder_minutes should NOT be reminded."""
    monkeypatch.setattr("config.settings.reminder_minutes", 30)

    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")

    await _send_reminders()

    updated = await _get_entry(entry["id"])
    assert updated["reminded"] == 0


# ── Grace period expiry ──────────────────────────────────────────────────


async def test_expire_grace_period(db, monkeypatch):
    """Reminded entries past grace period should become no_show."""
    monkeypatch.setattr("config.settings.reminder_minutes", 30)
    monkeypatch.setattr("config.settings.grace_minutes", 10)

    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.mark_reminded(entry["id"])

    # Backdate to 45 minutes ago (past 30 + 10 = 40)
    conn = await models.get_db()
    await conn.execute(
        """
        UPDATE queue_entries
        SET serving_at = datetime('now', '-45 minutes')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await conn.commit()

    await _expire_grace_period()

    updated = await _get_entry(entry["id"])
    assert updated["status"] == "no_show"


async def test_no_expire_if_not_reminded(db, monkeypatch):
    """Entries that haven't been reminded should not expire."""
    monkeypatch.setattr("config.settings.reminder_minutes", 30)
    monkeypatch.setattr("config.settings.grace_minutes", 10)

    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")

    conn = await models.get_db()
    await conn.execute(
        """
        UPDATE queue_entries
        SET serving_at = datetime('now', '-45 minutes')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await conn.commit()

    await _expire_grace_period()

    updated = await _get_entry(entry["id"])
    assert updated["status"] == "serving"  # still serving, not expired


# ── Daily reset ──────────────────────────────────────────────────────────


async def test_daily_reset_cancels_old_entries(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])

    # Backdate to yesterday
    conn = await models.get_db()
    await conn.execute(
        """
        UPDATE queue_entries
        SET joined_at = datetime('now', '-1 day')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await conn.commit()

    await _daily_reset()

    updated = await _get_entry(entry["id"])
    assert updated["status"] == "cancelled"


async def test_daily_reset_does_not_touch_today(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("1", "Alice")
    entry = await models.join_queue(user["id"], machine["id"])

    await _daily_reset()

    updated = await _get_entry(entry["id"])
    assert updated["status"] == "waiting"


# ── Capacity (multi-unit) ─────────────────────────────────────────────────


async def test_agent_promotes_up_to_unit_capacity(db):
    """3 active units ⇒ 3 promoted, then queue holds."""
    machine = await models.get_machine_by_slug("laser-cutter")
    mid = machine["id"]
    await models.create_unit(machine_id=mid, label="U2")
    await models.create_unit(machine_id=mid, label="U3")

    for i in range(5):
        u = await models.get_or_create_user(str(100 + i), f"user{i}")
        await models.join_queue(u["id"], mid)

    await _process_machines()

    assert await models.count_serving_on_machine(mid) == 3

    from db.database import get_db
    conn = await get_db()
    cursor = await conn.execute(
        "SELECT unit_id FROM queue_entries "
        "WHERE machine_id = ? AND status = 'serving'",
        (mid,),
    )
    unit_ids = [r["unit_id"] for r in await cursor.fetchall()]
    assert None not in unit_ids
    assert len(set(unit_ids)) == 3


async def test_agent_respects_maintenance_unit(db):
    """A maintenance unit doesn't count toward capacity."""
    machine = await models.get_machine_by_slug("laser-cutter")
    mid = machine["id"]
    await models.create_unit(machine_id=mid, label="U2")
    u3 = await models.create_unit(machine_id=mid, label="U3")
    await models.update_unit(u3["id"], status="maintenance")

    for i in range(3):
        user = await models.get_or_create_user(str(200 + i), f"m{i}")
        await models.join_queue(user["id"], mid)

    await _process_machines()

    assert await models.count_serving_on_machine(mid) == 2


# ── Daily analytics snapshot ─────────────────────────────────────────────


async def test_daily_snapshot_includes_rating_columns(
    db, monkeypatch: pytest.MonkeyPatch
):
    """The daily snapshot LEFT JOINs feedback so avg_rating + rating_count
    land in analytics_snapshots."""
    # Reset the once-per-day guard so the function actually runs in the test.
    monkeypatch.setattr(agent_mod, "_last_snapshot_date", None)
    # Skip the OpenAI call.
    async def _no_ai(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_mod, "_generate_ai_summary", _no_ai)

    machine = await models.get_machine_by_slug("laser-cutter")
    user = await models.get_or_create_user("snap-1", "u")
    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.update_entry_status(entry["id"], "completed", job_successful=1)
    await models.create_feedback(
        queue_entry_id=entry["id"], rating=5, comment=None
    )

    # Backdate joined_at to yesterday so _compute_daily_analytics picks it up.
    conn = await models.get_db()
    await conn.execute(
        """
        UPDATE queue_entries
        SET joined_at = datetime('now', '-1 day')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await conn.commit()

    from agent.loop import _compute_daily_analytics

    await _compute_daily_analytics()

    cursor = await conn.execute(
        "SELECT avg_rating, rating_count FROM analytics_snapshots "
        "WHERE machine_id = ? ORDER BY id DESC LIMIT 1",
        (machine["id"],),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["rating_count"] == 1
    assert row["avg_rating"] == 5.0


# ── Test helper ──────────────────────────────────────────────────────────


async def _get_entry(entry_id: int) -> dict:
    from db.database import get_db

    conn = await get_db()
    cursor = await conn.execute(
        "SELECT * FROM queue_entries WHERE id = ?", (entry_id,)
    )
    row = await cursor.fetchone()
    return dict(row)
