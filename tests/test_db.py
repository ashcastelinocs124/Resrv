"""Tests for database models and query helpers."""

from __future__ import annotations

import pytest
from db import models

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_entry(db, entry_id):
    cursor = await db.execute("SELECT * FROM queue_entries WHERE id = ?", (entry_id,))
    row = await cursor.fetchone()
    return dict(row)


# ── Machine queries ──────────────────────────────────────────────────────


async def test_get_machines_returns_seeded(db):
    machines = await models.get_machines()
    assert len(machines) == 6
    slugs = [m["slug"] for m in machines]
    assert "laser-cutter" in slugs
    assert "cnc-router" in slugs


async def test_get_machine_by_id(db):
    machines = await models.get_machines()
    machine = await models.get_machine(machines[0]["id"])
    assert machine is not None
    assert machine["name"] == machines[0]["name"]


async def test_get_machine_by_slug(db):
    machine = await models.get_machine_by_slug("water-jet")
    assert machine is not None
    assert machine["name"] == "Water Jet"


async def test_get_machine_not_found(db):
    assert await models.get_machine(999) is None
    assert await models.get_machine_by_slug("nonexistent") is None


async def test_update_machine_status(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    assert machine["status"] == "active"

    await models.update_machine_status(machine["id"], "maintenance")
    updated = await models.get_machine(machine["id"])
    assert updated["status"] == "maintenance"


# ── User queries ─────────────────────────────────────────────────────────


async def test_get_or_create_user_creates(db):
    user = await models.get_or_create_user("12345", "TestUser")
    assert user["discord_id"] == "12345"
    assert user["discord_name"] == "TestUser"
    assert user["id"] is not None


async def test_get_or_create_user_returns_existing(db):
    user1 = await models.get_or_create_user("12345", "TestUser")
    user2 = await models.get_or_create_user("12345", "TestUser")
    assert user1["id"] == user2["id"]


async def test_get_user_by_discord_id(db):
    await models.get_or_create_user("99999", "Someone")
    user = await models.get_user_by_discord_id("99999")
    assert user is not None
    assert user["discord_name"] == "Someone"


async def test_get_user_by_discord_id_not_found(db):
    assert await models.get_user_by_discord_id("00000") is None


# ── Queue entry queries ──────────────────────────────────────────────────


async def test_join_queue(db):
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")

    entry = await models.join_queue(user["id"], machine["id"])
    assert entry["status"] == "waiting"
    assert entry["position"] == 1
    assert entry["user_id"] == user["id"]
    assert entry["machine_id"] == machine["id"]


async def test_join_queue_fifo_ordering(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")
    u3 = await models.get_or_create_user("3", "Charlie")

    e1 = await models.join_queue(u1["id"], machine["id"])
    e2 = await models.join_queue(u2["id"], machine["id"])
    e3 = await models.join_queue(u3["id"], machine["id"])

    assert e1["position"] < e2["position"] < e3["position"]


async def test_get_queue_for_machine(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")

    await models.join_queue(u1["id"], machine["id"])
    await models.join_queue(u2["id"], machine["id"])

    queue = await models.get_queue_for_machine(machine["id"])
    assert len(queue) == 2
    assert queue[0]["discord_name"] == "Alice"
    assert queue[1]["discord_name"] == "Bob"


async def test_leave_queue(db):
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])

    await models.leave_queue(entry["id"])

    queue = await models.get_queue_for_machine(machine["id"])
    assert len(queue) == 0


async def test_get_user_active_entry(db):
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")
    await models.join_queue(user["id"], machine["id"])

    active = await models.get_user_active_entry(user["id"], machine["id"])
    assert active is not None
    assert active["status"] == "waiting"


async def test_get_user_active_entry_none(db):
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")
    assert await models.get_user_active_entry(user["id"], machine["id"]) is None


async def test_update_entry_status_to_serving(db):
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])

    await models.update_entry_status(entry["id"], "serving")

    serving = await models.get_serving_entry(machine["id"])
    assert serving is not None
    assert serving["discord_name"] == "Alice"
    assert serving["serving_at"] is not None


async def test_get_next_waiting(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")
    await models.join_queue(u1["id"], machine["id"])
    await models.join_queue(u2["id"], machine["id"])

    next_entry = await models.get_next_waiting(machine["id"])
    assert next_entry is not None
    assert next_entry["discord_name"] == "Alice"


async def test_bump_entry_to_top(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    u1 = await models.get_or_create_user("1", "Alice")
    u2 = await models.get_or_create_user("2", "Bob")
    await models.join_queue(u1["id"], machine["id"])
    e2 = await models.join_queue(u2["id"], machine["id"])

    await models.bump_entry_to_top(e2["id"], machine["id"])

    next_entry = await models.get_next_waiting(machine["id"])
    assert next_entry["discord_name"] == "Bob"


async def test_get_waiting_count(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    assert await models.get_waiting_count(machine["id"]) == 0

    u1 = await models.get_or_create_user("1", "Alice")
    await models.join_queue(u1["id"], machine["id"])
    assert await models.get_waiting_count(machine["id"]) == 1


async def test_serving_entry_returns_none_when_empty(db):
    machine = await models.get_machine_by_slug("laser-cutter")
    assert await models.get_serving_entry(machine["id"]) is None


async def test_complete_entry_flow(db):
    """Test the full flow: join → serve → complete."""
    user = await models.get_or_create_user("111", "Alice")
    machine = await models.get_machine_by_slug("laser-cutter")
    entry = await models.join_queue(user["id"], machine["id"])

    # Serve
    await models.update_entry_status(entry["id"], "serving")
    serving = await models.get_serving_entry(machine["id"])
    assert serving is not None

    # Complete
    await models.update_entry_status(
        entry["id"], "completed", job_successful=1
    )
    assert await models.get_serving_entry(machine["id"]) is None

    # Should no longer appear in active queue
    queue = await models.get_queue_for_machine(machine["id"])
    assert len(queue) == 0


# ── New model helpers ────────────────────────────────────────────────────


async def test_reset_reminder(db):
    user = await models.get_or_create_user("reset1", "ResetUser")
    entry = await models.join_queue(user["id"], 1)
    await models.update_entry_status(entry["id"], "serving")
    await models.mark_reminded(entry["id"])

    # Verify reminded is 1
    updated = await _get_entry(db, entry["id"])
    assert updated["reminded"] == 1

    # Reset it
    await models.reset_reminder(entry["id"])
    updated = await _get_entry(db, entry["id"])
    assert updated["reminded"] == 0


async def test_get_user_active_entries(db):
    user = await models.get_or_create_user("multi1", "MultiUser")
    await models.join_queue(user["id"], 1)
    await models.join_queue(user["id"], 2)

    entries = await models.get_user_active_entries(user["id"])
    assert len(entries) == 2
    machine_ids = {e["machine_id"] for e in entries}
    assert machine_ids == {1, 2}


async def test_get_user_active_entries_empty(db):
    user = await models.get_or_create_user("empty1", "EmptyUser")
    entries = await models.get_user_active_entries(user["id"])
    assert entries == []


# ── Registration helpers ────────────────────────────────────────────────


async def test_register_user(db):
    """register_user saves profile fields and sets registered=1."""
    user = await models.get_or_create_user("reg1", "RegUser")
    assert user.get("registered", 0) == 0

    cursor = await db.execute(
        "SELECT id FROM colleges WHERE name = ? AND archived_at IS NULL",
        ("Grainger College of Engineering",),
    )
    college_id = (await cursor.fetchone())["id"]

    await models.register_user(
        user_id=user["id"],
        full_name="Alex Chen",
        email="achen2@illinois.edu",
        major="Computer Science",
        college_id=college_id,
        graduation_year="2027",
    )
    updated = await models.get_user_by_discord_id("reg1")
    assert updated["registered"] == 1
    assert updated["full_name"] == "Alex Chen"
    assert updated["email"] == "achen2@illinois.edu"
    assert updated["major"] == "Computer Science"
    assert updated["college_id"] == college_id
    assert updated["graduation_year"] == "2027"


async def test_update_user_profile(db):
    """update_user_profile changes existing fields."""
    user = await models.get_or_create_user("upd1", "UpdUser")
    cursor = await db.execute(
        "SELECT id FROM colleges WHERE name = ? AND archived_at IS NULL",
        ("College of Liberal Arts and Sciences",),
    )
    las_id = (await cursor.fetchone())["id"]
    cursor = await db.execute(
        "SELECT id FROM colleges WHERE name = ? AND archived_at IS NULL",
        ("Grainger College of Engineering",),
    )
    grainger_id = (await cursor.fetchone())["id"]

    await models.register_user(
        user_id=user["id"],
        full_name="Old Name",
        email="old@illinois.edu",
        major="Math",
        college_id=las_id,
        graduation_year="2026",
    )
    await models.update_user_profile(
        user_id=user["id"],
        full_name="New Name",
        email="new@illinois.edu",
        major="Physics",
        college_id=grainger_id,
        graduation_year="2028",
    )
    updated = await models.get_user_by_discord_id("upd1")
    assert updated["full_name"] == "New Name"
    assert updated["email"] == "new@illinois.edu"
    assert updated["major"] == "Physics"
    assert updated["college_id"] == grainger_id
    assert updated["graduation_year"] == "2028"
    assert updated["registered"] == 1


# ── Analytics helpers ───────────────────────────────────────────────────


async def test_insert_analytics_snapshot(db):
    """insert_analytics_snapshot stores a row and get_snapshots retrieves it."""
    await models.insert_analytics_snapshot(
        date="2026-04-08",
        machine_id=1,
        total_jobs=10,
        completed_jobs=8,
        avg_wait_mins=5.5,
        avg_serve_mins=20.0,
        peak_hour=14,
        ai_summary="Busy day.",
        no_show_count=1,
        cancelled_count=1,
        unique_users=7,
        failure_count=0,
    )
    rows = await models.get_analytics_snapshots(
        start_date="2026-04-08", end_date="2026-04-08"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["total_jobs"] == 10
    assert row["completed_jobs"] == 8
    assert row["unique_users"] == 7
    assert row["no_show_count"] == 1
    assert row["ai_summary"] == "Busy day."


async def test_get_snapshots_date_range(db):
    """get_analytics_snapshots filters by date range."""
    for day in ("2026-04-06", "2026-04-07", "2026-04-08"):
        await models.insert_analytics_snapshot(
            date=day, machine_id=1, total_jobs=5, completed_jobs=4,
            avg_wait_mins=3.0, avg_serve_mins=15.0, peak_hour=10,
            ai_summary="", no_show_count=0, cancelled_count=0,
            unique_users=3, failure_count=0,
        )
    rows = await models.get_analytics_snapshots(
        start_date="2026-04-07", end_date="2026-04-08"
    )
    assert len(rows) == 2


async def test_get_snapshots_by_machine(db):
    """get_analytics_snapshots filters by machine_id."""
    await models.insert_analytics_snapshot(
        date="2026-04-08", machine_id=1, total_jobs=5, completed_jobs=4,
        avg_wait_mins=3.0, avg_serve_mins=15.0, peak_hour=10,
        ai_summary="", no_show_count=0, cancelled_count=0,
        unique_users=3, failure_count=0,
    )
    await models.insert_analytics_snapshot(
        date="2026-04-08", machine_id=2, total_jobs=8, completed_jobs=7,
        avg_wait_mins=4.0, avg_serve_mins=18.0, peak_hour=11,
        ai_summary="", no_show_count=0, cancelled_count=0,
        unique_users=5, failure_count=0,
    )
    rows = await models.get_analytics_snapshots(
        start_date="2026-04-08", end_date="2026-04-08", machine_id=2
    )
    assert len(rows) == 1
    assert rows[0]["machine_id"] == 2


async def test_compute_live_today_stats(db):
    """compute_live_today_stats returns current day metrics from queue_entries."""
    user = await models.get_or_create_user("stats1", "StatsUser")
    machine = await models.get_machine_by_slug("laser-cutter")

    entry = await models.join_queue(user["id"], machine["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.update_entry_status(entry["id"], "completed", job_successful=1)

    stats = await models.compute_live_today_stats()
    assert len(stats) > 0
    machine_stat = next(s for s in stats if s["machine_id"] == machine["id"])
    assert machine_stat["total_jobs"] >= 1
    assert machine_stat["completed_jobs"] >= 1


# ── Machine archival schema ─────────────────────────────────────────────


async def test_machines_have_archived_at_column(db):
    cursor = await db.execute("PRAGMA table_info(machines)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert "archived_at" in columns


async def test_fresh_machines_are_not_archived(db):
    row = await (await db.execute(
        "SELECT archived_at FROM machines LIMIT 1"
    )).fetchone()
    assert row["archived_at"] is None


# ── Staff role schema ───────────────────────────────────────────────────


async def test_staff_users_have_role_column(db):
    cursor = await db.execute("PRAGMA table_info(staff_users)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert "role" in columns


async def test_settings_table_exists_and_seeded(db):
    cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
    rows = {r["key"]: r["value"] for r in await cursor.fetchall()}
    for k in (
        "reminder_minutes",
        "grace_minutes",
        "queue_reset_hour",
        "agent_tick_seconds",
        "public_mode",
        "maintenance_banner",
    ):
        assert k in rows, f"missing setting: {k}"
    assert rows["public_mode"] == "false"
    assert rows["maintenance_banner"] == ""


async def test_migration_promotes_oldest_staff_when_no_admin_exists(db):
    from api.auth import hash_password
    await db.execute("DELETE FROM staff_users")
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("first", hash_password("pw"), "staff"),
    )
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("second", hash_password("pw"), "staff"),
    )
    await db.commit()
    import db.database as dbm
    await dbm._migrate(db)
    row = await (await db.execute(
        "SELECT role FROM staff_users WHERE username = 'first'"
    )).fetchone()
    assert row["role"] == "admin"
    row = await (await db.execute(
        "SELECT role FROM staff_users WHERE username = 'second'"
    )).fetchone()
    assert row["role"] == "staff"


# ── Colleges table schema ───────────────────────────────────────────────


async def test_colleges_table_seeded_on_fresh_db(db):
    cursor = await db.execute(
        "SELECT name FROM colleges WHERE archived_at IS NULL ORDER BY id"
    )
    rows = [row["name"] for row in await cursor.fetchall()]
    assert "Grainger College of Engineering" in rows
    assert "Gies College of Business" in rows
    assert len(rows) >= 15


async def test_users_college_id_column_exists(db):
    cursor = await db.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "college_id" in cols
    assert "college" not in cols  # legacy column dropped


async def test_partial_unique_index_blocks_duplicate_active_name(db):
    await db.execute("INSERT INTO colleges (name) VALUES (?)", ("Test College",))
    with pytest.raises(Exception):
        await db.execute("INSERT INTO colleges (name) VALUES (?)", ("Test College",))


async def test_partial_unique_index_allows_archived_duplicate(db):
    await db.execute(
        "INSERT INTO colleges (name, archived_at) VALUES (?, datetime('now'))",
        ("Archived College",),
    )
    # active insert with same name must succeed
    await db.execute("INSERT INTO colleges (name) VALUES (?)", ("Archived College",))


async def test_migration_wipes_registered_flag_for_existing_users(db):
    """Upgrade path: existing registered users should be flipped to registered=0
    so they re-pick a college on next Join Queue press."""
    # Simulate a pre-migration registered user. The db fixture has already run
    # init_db, so we manually re-flip then re-run _migrate to confirm
    # idempotence + wipe.
    await db.execute(
        "INSERT INTO users (discord_id, discord_name, full_name, email, major, "
        "graduation_year, registered) VALUES (?, ?, ?, ?, ?, ?, 1)",
        ("999", "legacy", "Legacy User", "legacy@illinois.edu", "CS", "2027"),
    )
    await db.commit()
    from db import database
    await database._migrate(db)
    cursor = await db.execute(
        "SELECT registered FROM users WHERE discord_id = '999'"
    )
    row = await cursor.fetchone()
    assert row["registered"] == 0


# ── Feedback schema ─────────────────────────────────────────────────────


async def test_feedback_table_exists_with_check_constraint(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(feedback)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "queue_entry_id", "rating", "comment", "created_at"} <= cols


async def test_feedback_unique_per_queue_entry(db):
    conn = await models.get_db()
    user = await models.get_or_create_user(discord_id="ftest1", discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.update_entry_status(entry["id"], "completed", job_successful=1)
    await conn.execute(
        "INSERT INTO feedback (queue_entry_id, rating) VALUES (?, ?)",
        (entry["id"], 5),
    )
    await conn.commit()
    with pytest.raises(Exception):
        await conn.execute(
            "INSERT INTO feedback (queue_entry_id, rating) VALUES (?, ?)",
            (entry["id"], 3),
        )


async def test_feedback_check_blocks_invalid_rating(db):
    conn = await models.get_db()
    user = await models.get_or_create_user(discord_id="ftest2", discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    with pytest.raises(Exception):
        await conn.execute(
            "INSERT INTO feedback (queue_entry_id, rating) VALUES (?, ?)",
            (entry["id"], 0),
        )
    with pytest.raises(Exception):
        await conn.execute(
            "INSERT INTO feedback (queue_entry_id, rating) VALUES (?, ?)",
            (entry["id"], 6),
        )


async def test_feedback_cascades_on_queue_entry_delete(db):
    conn = await models.get_db()
    user = await models.get_or_create_user(discord_id="ftest3", discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    await conn.execute(
        "INSERT INTO feedback (queue_entry_id, rating, comment) VALUES (?, ?, ?)",
        (entry["id"], 4, "ok"),
    )
    await conn.commit()
    await conn.execute("DELETE FROM queue_entries WHERE id = ?", (entry["id"],))
    await conn.commit()
    cursor = await conn.execute(
        "SELECT COUNT(*) AS cnt FROM feedback WHERE queue_entry_id = ?",
        (entry["id"],),
    )
    row = await cursor.fetchone()
    assert row["cnt"] == 0


async def test_analytics_snapshots_has_rating_columns(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(analytics_snapshots)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "avg_rating" in cols
    assert "rating_count" in cols


# ── Agent + pinned charts schema ────────────────────────────────────────


async def test_agent_tables_exist(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(agent_conversations)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "staff_user_id", "title", "created_at", "updated_at"} <= cols
    cursor = await conn.execute("PRAGMA table_info(agent_messages)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "conversation_id", "role", "content",
             "tool_call_id", "tool_calls_json", "chart_spec_json",
             "created_at"} <= cols


async def test_pinned_charts_table_exists(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(pinned_charts)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "chart_spec_json", "title", "created_by",
             "pin_order", "created_at"} <= cols


async def test_staff_users_has_onboarded_at(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(staff_users)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "onboarded_at" in cols


async def test_data_analyst_settings_seeded(db):
    from api import settings_store
    val = await settings_store.get_setting("data_analyst_enabled")
    assert val == "false"
    val = await settings_store.get_setting("data_analyst_visible_to_staff")
    assert val == "false"


async def test_users_has_verified_at(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "verified_at" in cols

