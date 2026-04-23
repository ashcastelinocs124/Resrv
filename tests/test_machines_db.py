"""Tests for machine DB helpers (create/update/archive/restore/purge)."""

from __future__ import annotations

import pytest

from db import models

pytestmark = pytest.mark.asyncio


async def test_create_machine(db):
    m = await models.create_machine(name="New Tool", slug="new-tool")
    assert m["name"] == "New Tool"
    assert m["archived_at"] is None


async def test_create_machine_rejects_bad_slug(db):
    with pytest.raises(ValueError):
        await models.create_machine(name="Bad", slug="Bad Slug")


async def test_create_machine_rejects_duplicate_active_slug(db):
    await models.create_machine(name="Dup", slug="dup-tool")
    with pytest.raises(ValueError):
        await models.create_machine(name="Dup 2", slug="dup-tool")


async def test_archive_hides_from_list(db):
    m = await models.create_machine(name="X", slug="x-tool")
    await models.archive_machine(m["id"])
    listed = await models.list_machines()
    assert all(row["slug"] != "x-tool" for row in listed)
    all_rows = await models.list_machines(include_archived=True)
    assert any(row["slug"] == "x-tool" for row in all_rows)


async def test_restore_brings_it_back(db):
    m = await models.create_machine(name="R", slug="r-tool")
    await models.archive_machine(m["id"])
    await models.restore_machine(m["id"])
    assert any(
        row["slug"] == "r-tool" for row in await models.list_machines()
    )


async def test_restore_blocked_if_slug_taken(db):
    a = await models.create_machine(name="A", slug="shared")
    await models.archive_machine(a["id"])
    await models.create_machine(name="B", slug="shared")
    with pytest.raises(ValueError):
        await models.restore_machine(a["id"])


async def test_purge_removes_row(db):
    m = await models.create_machine(name="Doomed", slug="doomed")
    await models.purge_machine(m["id"])
    assert await models.get_machine(m["id"]) is None


async def test_update_machine_slug_uniqueness(db):
    a = await models.create_machine(name="A", slug="alpha")
    b = await models.create_machine(name="B", slug="beta")
    with pytest.raises(ValueError):
        await models.update_machine(b["id"], slug="alpha")
    await models.update_machine(b["id"], slug="gamma")


async def test_count_active_queue_entries(db):
    m = (await models.list_machines())[0]
    assert await models.count_active_queue_entries(m["id"]) == 0
    user = await models.get_or_create_user("u1", "U1")
    await models.join_queue(user["id"], m["id"])
    assert await models.count_active_queue_entries(m["id"]) == 1


async def test_migration_creates_machine_units_table(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='machine_units'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_migration_backfills_main_unit_for_existing_machines(db):
    # Every non-archived machine should have exactly one unit labeled "Main"
    cursor = await db.execute(
        """
        SELECT m.id, m.name,
               (SELECT COUNT(*) FROM machine_units u
                WHERE u.machine_id = m.id AND u.archived_at IS NULL) AS unit_count,
               (SELECT u.label FROM machine_units u
                WHERE u.machine_id = m.id AND u.archived_at IS NULL LIMIT 1) AS label
        FROM machines m
        WHERE m.archived_at IS NULL
        """
    )
    rows = await cursor.fetchall()
    assert len(rows) > 0
    for r in rows:
        assert r["unit_count"] == 1, f"machine {r['name']} has {r['unit_count']} units"
        assert r["label"] == "Main"


async def test_migration_partial_unique_index_allows_label_reuse_after_archive(db):
    cursor = await db.execute(
        "SELECT id FROM machines WHERE archived_at IS NULL LIMIT 1"
    )
    machine_id = (await cursor.fetchone())["id"]

    await db.execute(
        "INSERT INTO machine_units (machine_id, label) VALUES (?, ?)",
        (machine_id, "Prusa MK4"),
    )
    await db.execute(
        """
        UPDATE machine_units
        SET archived_at = datetime('now')
        WHERE machine_id = ? AND label = 'Prusa MK4'
        """,
        (machine_id,),
    )
    # Re-insert same label — must succeed because first row is archived
    await db.execute(
        "INSERT INTO machine_units (machine_id, label) VALUES (?, ?)",
        (machine_id, "Prusa MK4"),
    )
    await db.commit()


async def test_migration_partial_unique_index_rejects_duplicate_active_label(db):
    import aiosqlite
    cursor = await db.execute(
        "SELECT id FROM machines WHERE archived_at IS NULL LIMIT 1"
    )
    machine_id = (await cursor.fetchone())["id"]

    await db.execute(
        "INSERT INTO machine_units (machine_id, label) VALUES (?, ?)",
        (machine_id, "Bambu"),
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO machine_units (machine_id, label) VALUES (?, ?)",
            (machine_id, "Bambu"),
        )


async def test_migration_adds_unit_id_to_queue_entries(db):
    cursor = await db.execute("PRAGMA table_info(queue_entries)")
    cols = {r[1] for r in await cursor.fetchall()}
    assert "unit_id" in cols
