"""Mentor shifts + training assignment routing."""
from __future__ import annotations

import pytest

from db import models

pytestmark = pytest.mark.asyncio


async def _make_trainee(
    db, *, discord_id: str, full_name: str | None = None
) -> dict:
    user = await models.get_or_create_user(
        discord_id=discord_id, discord_name=f"d_{discord_id}"
    )
    if full_name is not None:
        college = await models.create_college(f"C_{discord_id}")
        await models.register_user(
            user_id=user["id"],
            full_name=full_name,
            email=f"{discord_id}@illinois.edu",
            major="CS",
            college_id=college["id"],
            graduation_year="2027",
        )
    return user


async def test_start_and_end_shift_round_trip(db):
    await models.start_mentor_shift("mentor1")
    shifts = await models.list_open_mentor_shifts()
    assert len(shifts) == 1 and shifts[0]["discord_id"] == "mentor1"

    closed = await models.end_mentor_shift("mentor1")
    assert closed is not None and closed["ended_at"] is not None
    assert await models.list_open_mentor_shifts() == []


async def test_double_start_raises(db):
    await models.start_mentor_shift("mentor1")
    with pytest.raises(models.MentorShiftAlreadyOpenError):
        await models.start_mentor_shift("mentor1")


async def test_end_shift_when_none_open_returns_none(db):
    assert await models.end_mentor_shift("nobody") is None


async def test_pick_free_mentor_none_when_no_one_on_shift(db):
    assert await models.pick_free_mentor() is None


async def test_pick_free_mentor_with_one_mentor(db):
    await models.start_mentor_shift("solo")
    assert await models.pick_free_mentor() == "solo"


async def test_pick_free_mentor_load_balances(db):
    """The mentor with fewer active training assignments should win."""
    machine = await models.create_machine(name="M", slug="m")
    await models.start_mentor_shift("busy")
    await models.start_mentor_shift("free")

    # Give "busy" one active training assignment.
    trainee = await _make_trainee(db, discord_id="t1")
    entry = await models.join_queue(trainee["id"], machine["id"], purpose="training")
    await models.assign_mentor_to_entry(entry["id"], "busy")

    assert await models.pick_free_mentor() == "free"


async def test_pick_free_mentor_tiebreak_prefers_longer_open_shift(db):
    """When loads are equal, the mentor on shift the longest wins."""
    import asyncio

    await models.start_mentor_shift("first")
    await asyncio.sleep(1.1)  # SQLite second-resolution timestamps
    await models.start_mentor_shift("second")
    assert await models.pick_free_mentor() == "first"


async def test_unassigned_training_entries_lists_only_today_unassigned(db):
    machine = await models.create_machine(name="N", slug="n")
    t1 = await _make_trainee(db, discord_id="t10", full_name="Trainee Ten")
    t2 = await _make_trainee(db, discord_id="t11", full_name="Trainee Eleven")

    e1 = await models.join_queue(t1["id"], machine["id"], purpose="training")
    e2 = await models.join_queue(t2["id"], machine["id"], purpose="training")
    await models.assign_mentor_to_entry(e2["id"], "some_mentor")

    rows = await models.get_unassigned_training_entries()
    assert [r["id"] for r in rows] == [e1["id"]]
    assert rows[0]["full_name"] == "Trainee Ten"
    assert rows[0]["machine_name"] == "N"


async def test_production_entries_are_not_picked_up_as_unassigned(db):
    machine = await models.create_machine(name="P", slug="p")
    trainee = await _make_trainee(db, discord_id="t20")
    await models.join_queue(trainee["id"], machine["id"], purpose="production")
    assert await models.get_unassigned_training_entries() == []
