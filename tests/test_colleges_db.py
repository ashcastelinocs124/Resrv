"""DB-layer tests for the colleges table."""

from __future__ import annotations

import pytest

from db import models

pytestmark = pytest.mark.asyncio


async def test_create_college(db):
    college = await models.create_college("Test College")
    assert college["id"] > 0
    assert college["name"] == "Test College"
    assert college["archived_at"] is None


async def test_create_college_dup_active_raises(db):
    await models.create_college("Dup College")
    with pytest.raises(models.DuplicateCollegeError):
        await models.create_college("Dup College")


async def test_list_active_colleges_excludes_archived(db):
    await models.create_college("Active A")
    b = await models.create_college("Archived B")
    await models.archive_college(b["id"])
    rows = await models.list_active_colleges()
    names = {r["name"] for r in rows}
    assert "Active A" in names
    assert "Archived B" not in names


async def test_list_all_colleges_includes_archived(db):
    await models.create_college("ListAll A")
    b = await models.create_college("ListAll B")
    await models.archive_college(b["id"])
    rows = await models.list_all_colleges()
    names = {r["name"] for r in rows}
    assert "ListAll A" in names
    assert "ListAll B" in names


async def test_update_college_renames(db):
    college = await models.create_college("Old Name")
    await models.update_college(college["id"], name="New Name")
    fetched = await models.get_college(college["id"])
    assert fetched["name"] == "New Name"


async def test_archive_then_restore(db):
    college = await models.create_college("Archive Restore")
    await models.archive_college(college["id"])
    fetched = await models.get_college(college["id"])
    assert fetched["archived_at"] is not None
    await models.restore_college(college["id"])
    fetched = await models.get_college(college["id"])
    assert fetched["archived_at"] is None


async def test_count_users_in_college(db):
    college = await models.create_college("Count College")
    user = await models.get_or_create_user(discord_id="42", discord_name="u")
    await models.register_user(
        user_id=user["id"],
        full_name="Test",
        email="test@illinois.edu",
        major="CS",
        college_id=college["id"],
        graduation_year="2027",
    )
    count = await models.count_users_in_college(college["id"])
    assert count == 1


async def test_purge_college_blocked_with_users(db):
    college = await models.create_college("Purge Blocked")
    user = await models.get_or_create_user(discord_id="43", discord_name="u")
    await models.register_user(
        user_id=user["id"],
        full_name="Test",
        email="test2@illinois.edu",
        major="CS",
        college_id=college["id"],
        graduation_year="2027",
    )
    with pytest.raises(models.CollegeInUseError):
        await models.purge_college(college["id"])


async def test_purge_college_succeeds_with_no_users(db):
    college = await models.create_college("Purge OK")
    await models.purge_college(college["id"])
    assert await models.get_college(college["id"]) is None


async def test_register_user_writes_college_id(db):
    college = await models.create_college("Reg College")
    user = await models.get_or_create_user(discord_id="44", discord_name="u")
    await models.register_user(
        user_id=user["id"],
        full_name="Reg User",
        email="reg@illinois.edu",
        major="CS",
        college_id=college["id"],
        graduation_year="2027",
    )
    fetched = await models.get_user_by_discord_id("44")
    assert fetched["college_id"] == college["id"]
    assert fetched["registered"] == 1
