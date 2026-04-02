"""Tests for database models and query helpers."""

from __future__ import annotations

import pytest
from db import models

pytestmark = pytest.mark.asyncio


# ── Machine queries ──────────────────────────────────────────────────────


async def test_get_machines_returns_seeded(db):
    machines = await models.get_machines()
    assert len(machines) == 4
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
