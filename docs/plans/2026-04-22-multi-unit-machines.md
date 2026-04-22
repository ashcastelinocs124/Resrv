# Multi-Unit Machines Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let admins represent each machine as N labeled units that share one FIFO queue, with the agent auto-assigning a free unit on promotion.

**Architecture:** New `machine_units` table with soft-delete (partial unique index on `(machine_id, label)` per the pattern in learnings.md). `queue_entries` gets a nullable `unit_id` populated on promotion. The agent keeps promoting until `serving_count == active_unit_count`. Every existing machine gets one backfilled unit labeled `"Main"` so current queues keep working; single-`"Main"` machines render exactly as today on both Discord and the public frontend.

**Tech Stack:** aiosqlite (SQLite WAL), FastAPI, discord.py, React + Vite + Tailwind, Pytest.

**Design doc:** `docs/plans/2026-04-22-multi-unit-machines-design.md`

**Pre-flight notes (from `learnings.md`):**
- Partial unique indexes must be created in `_migrate` **after** the ALTER that adds the column they reference, using `CREATE UNIQUE INDEX IF NOT EXISTS`.
- Soft-delete + label reuse requires a partial index, not column-level `UNIQUE`.
- `@tasks.loop(seconds=...)` reads config at import time — don't rely on dynamic setting reads for loop interval, but per-tick values (like capacity) are fine via `get_setting_int`.
- `aiosqlite.Row` + `dict(row)` everywhere; keep the `_row_to_dict` / `_rows_to_dicts` helpers.

---

## Task 1: DB migration — `machine_units` table + partial unique index

**Files:**
- Modify: `db/database.py` (_create_tables, _migrate, _seed_machines)
- Test: `tests/test_machines_db.py` (new test)

**Step 1: Write the failing test**

Append to `tests/test_machines_db.py`:

```python
async def test_migration_creates_machine_units_table(fresh_db):
    db = fresh_db
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='machine_units'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_migration_backfills_main_unit_for_existing_machines(fresh_db):
    db = fresh_db
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


async def test_migration_partial_unique_index_allows_label_reuse_after_archive(
    fresh_db,
):
    db = fresh_db
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


async def test_migration_partial_unique_index_rejects_duplicate_active_label(
    fresh_db,
):
    import aiosqlite
    db = fresh_db
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


async def test_migration_adds_unit_id_to_queue_entries(fresh_db):
    db = fresh_db
    cursor = await db.execute("PRAGMA table_info(queue_entries)")
    cols = {r[1] for r in await cursor.fetchall()}
    assert "unit_id" in cols
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_machines_db.py -v -k "migration_creates_machine_units or backfills_main or partial_unique or adds_unit_id"`
Expected: FAIL (table/column/index missing).

**Step 3: Implement migration**

In `db/database.py`:

- Add to `_create_tables` (so fresh DBs get the table from scratch; index still goes in `_migrate`):

```python
CREATE TABLE IF NOT EXISTS machine_units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id   INTEGER NOT NULL REFERENCES machines(id),
    label        TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'active',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    archived_at  TEXT
);
```

- In `_migrate` (AFTER the existing machines block and BEFORE staff block):

```python
# machine_units table may be missing on upgrades from pre-multi-unit DBs.
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS machine_units (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id   INTEGER NOT NULL REFERENCES machines(id),
        label        TEXT    NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'active',
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        archived_at  TEXT
    )
    """
)
await db.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_machine_units_label_active "
    "ON machine_units(machine_id, label) WHERE archived_at IS NULL"
)

# Add queue_entries.unit_id if missing
cursor = await db.execute("PRAGMA table_info(queue_entries)")
qe_cols = {row[1] for row in await cursor.fetchall()}
if "unit_id" not in qe_cols:
    await db.execute(
        "ALTER TABLE queue_entries "
        "ADD COLUMN unit_id INTEGER REFERENCES machine_units(id)"
    )

# Backfill: every non-archived machine with zero units gets a "Main" unit
await db.execute(
    """
    INSERT INTO machine_units (machine_id, label)
    SELECT m.id, 'Main'
    FROM machines m
    WHERE m.archived_at IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM machine_units u
          WHERE u.machine_id = m.id AND u.archived_at IS NULL
      )
    """
)
```

**Step 4: Run tests — expect pass**

Run: `pytest tests/test_machines_db.py -v`
Expected: all PASS (including existing machine tests).

**Step 5: Commit**

```bash
git add db/database.py tests/test_machines_db.py
git commit -m "feat(db): add machine_units table + backfill Main unit per machine"
```

---

## Task 2: `db/models.py` — machine_unit CRUD helpers

**Files:**
- Modify: `db/models.py` (append a new "Machine Units" section)
- Test: `tests/test_machines_db.py`

**Step 1: Write the failing test**

Append to `tests/test_machines_db.py`:

```python
async def test_list_units_for_machine(fresh_db):
    from db import models
    machines = await models.get_machines()
    mid = machines[0]["id"]
    units = await models.list_units(mid)
    assert len(units) == 1
    assert units[0]["label"] == "Main"
    assert units[0]["status"] == "active"
    assert units[0]["archived_at"] is None


async def test_create_unit_success(fresh_db):
    from db import models
    mid = (await models.get_machines())[0]["id"]
    unit = await models.create_unit(machine_id=mid, label="Prusa MK4")
    assert unit["label"] == "Prusa MK4"
    assert unit["status"] == "active"


async def test_create_unit_duplicate_label_raises(fresh_db):
    from db import models
    mid = (await models.get_machines())[0]["id"]
    await models.create_unit(machine_id=mid, label="Prusa")
    with pytest.raises(ValueError, match="label already in use"):
        await models.create_unit(machine_id=mid, label="Prusa")


async def test_create_unit_blank_label_rejected(fresh_db):
    from db import models
    mid = (await models.get_machines())[0]["id"]
    with pytest.raises(ValueError):
        await models.create_unit(machine_id=mid, label="   ")


async def test_update_unit_label_and_status(fresh_db):
    from db import models
    mid = (await models.get_machines())[0]["id"]
    unit = await models.create_unit(machine_id=mid, label="X1")
    await models.update_unit(unit["id"], label="Bambu X1", status="maintenance")
    after = await models.get_unit(unit["id"])
    assert after["label"] == "Bambu X1"
    assert after["status"] == "maintenance"


async def test_archive_and_restore_unit(fresh_db):
    from db import models
    mid = (await models.get_machines())[0]["id"]
    unit = await models.create_unit(machine_id=mid, label="Ender")
    await models.archive_unit(unit["id"])
    active = await models.list_units(mid)
    assert all(u["label"] != "Ender" for u in active)
    await models.restore_unit(unit["id"])
    active = await models.list_units(mid)
    assert any(u["label"] == "Ender" for u in active)


async def test_archive_unit_with_serving_entry_raises(fresh_db):
    """Archiving a unit currently holding a serving entry must fail."""
    from db import models
    mid = (await models.get_machines())[0]["id"]
    unit = await models.create_unit(machine_id=mid, label="Z")
    user = await models.get_or_create_user("42", "tester")
    entry = await models.join_queue(user["id"], mid)
    await models.update_entry_status(entry["id"], "serving", unit_id=unit["id"])
    with pytest.raises(ValueError, match="active serving entry"):
        await models.archive_unit(unit["id"])
```

**Step 2: Run — verify fail**

Run: `pytest tests/test_machines_db.py -v -k "unit"`
Expected: FAIL (module has no `list_units` / `create_unit` / etc.).

**Step 3: Implement**

Append to `db/models.py`:

```python
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
        # uniqueness: same machine, another active unit using this label
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
        "SELECT 1 FROM queue_entries "
        "WHERE unit_id = ? AND status = 'serving'",
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


async def purge_unit(unit_id: int) -> None:
    """Hard-delete a unit. Caller must confirm no serving entries exist."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM queue_entries "
        "WHERE unit_id = ? AND status = 'serving'",
        (unit_id,),
    )
    if await cursor.fetchone():
        raise ValueError("unit has an active serving entry")
    # Null out unit_id on historical entries to preserve the row
    await db.execute(
        "UPDATE queue_entries SET unit_id = NULL WHERE unit_id = ?", (unit_id,)
    )
    await db.execute("DELETE FROM machine_units WHERE id = ?", (unit_id,))
    await db.commit()
```

**Step 4: Run — expect pass**

Run: `pytest tests/test_machines_db.py -v -k "unit"`
Expected: all PASS.

**Step 5: Commit**

```bash
git add db/models.py tests/test_machines_db.py
git commit -m "feat(db): machine_unit CRUD helpers with label validation + archive guard"
```

---

## Task 3: Queue agent — capacity-based promotion

**Files:**
- Modify: `db/models.py` (add `count_active_units`, `count_serving_on_machine`, `first_available_unit`, extend `update_entry_status` to accept `unit_id`)
- Modify: `agent/loop.py` (`_process_machines`)
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Append to `tests/test_agent.py`:

```python
async def test_agent_promotes_up_to_unit_capacity(fresh_db):
    """3 active units ⇒ 3 promoted, then queue holds."""
    from db import models
    from agent.loop import _process_machines

    mid = (await models.get_machines())[0]["id"]
    # Ensure exactly 3 active units (Main + two new)
    await models.create_unit(machine_id=mid, label="U2")
    await models.create_unit(machine_id=mid, label="U3")

    users = []
    for i in range(5):
        u = await models.get_or_create_user(str(100 + i), f"user{i}")
        await models.join_queue(u["id"], mid)
        users.append(u)

    await _process_machines()

    serving = await models.count_serving_on_machine(mid)
    assert serving == 3
    # Each serving entry has a distinct unit_id
    db = await (await models.__dict__["get_db"])() if False else None  # noqa
    from db.database import get_db
    db = await get_db()
    cursor = await db.execute(
        "SELECT unit_id FROM queue_entries "
        "WHERE machine_id = ? AND status = 'serving'", (mid,)
    )
    unit_ids = [r["unit_id"] for r in await cursor.fetchall()]
    assert len(set(unit_ids)) == 3
    assert None not in unit_ids


async def test_agent_respects_maintenance_unit(fresh_db):
    """A maintenance unit doesn't count toward capacity."""
    from db import models
    from agent.loop import _process_machines

    mid = (await models.get_machines())[0]["id"]
    u2 = await models.create_unit(machine_id=mid, label="U2")
    u3 = await models.create_unit(machine_id=mid, label="U3")
    await models.update_unit(u3["id"], status="maintenance")

    for i in range(3):
        user = await models.get_or_create_user(str(200 + i), f"m{i}")
        await models.join_queue(user["id"], mid)

    await _process_machines()

    serving = await models.count_serving_on_machine(mid)
    assert serving == 2  # Main + U2; U3 is in maintenance
```

**Step 2: Run — verify fail**

Run: `pytest tests/test_agent.py -v -k "capacity or maintenance_unit"`
Expected: FAIL.

**Step 3: Implement**

Add to `db/models.py`:

```python
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
    """First active unit on this machine with no serving entry today."""
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
```

Rewrite `_process_machines` in `agent/loop.py`:

```python
async def _process_machines() -> None:
    """For each active machine, promote waiting users until capacity is full."""
    machines = await models.get_machines()
    for machine in machines:
        if machine["status"] != "active":
            continue

        capacity = await models.count_active_units(machine["id"])
        if capacity == 0:
            continue

        serving = await models.count_serving_on_machine(machine["id"])
        while serving < capacity:
            next_entry = await models.get_next_waiting(machine["id"])
            if next_entry is None:
                break
            unit = await models.first_available_unit(machine["id"])
            if unit is None:
                break  # shouldn't happen if serving < capacity, but safe
            await models.update_entry_status(
                next_entry["id"], "serving", unit_id=unit["id"]
            )
            log.info(
                "Advanced queue: %s on %s / %s",
                next_entry["discord_name"], machine["name"], unit["label"],
            )

            unit_suffix = (
                "" if unit["label"] == "Main"
                else f" (use the **{unit['label']}**)"
            )
            reminder_minutes = await get_setting_int(
                "reminder_minutes", settings.reminder_minutes
            )
            await _dm_user(
                next_entry["discord_id"],
                f"You're up! Head to the **{machine['name']}**{unit_suffix} now. "
                f"You'll receive a reminder after {reminder_minutes} minutes.",
            )
            serving += 1

        if _bot is not None:
            await _bot.update_queue_embeds(machine["id"])
```

`update_entry_status` already supports `**extra_fields`, so passing `unit_id=...` just works. No change needed.

**Step 4: Run — expect pass**

Run: `pytest tests/test_agent.py -v`
Expected: all PASS (including existing agent tests — they still have one unit = "Main", so they'll promote exactly one like before).

**Step 5: Commit**

```bash
git add db/models.py agent/loop.py tests/test_agent.py
git commit -m "feat(agent): capacity-based promotion using machine_units count"
```

---

## Task 4: API — nested unit routes under machines

**Files:**
- Create: `api/routes/units.py`
- Modify: `api/main.py` (register the new router)
- Modify: `api/deps.py` (add `notify_embed_refresh` — a thin wrapper over `notify_embed_update` for semantic clarity; reuse `notify_embed_update` if already identical)
- Test: `tests/test_machines_admin.py` (new tests) or new `tests/test_units_api.py`

**Step 1: Write the failing test**

Create `tests/test_units_api.py`:

```python
"""Unit CRUD API tests — mirrors machines auth split."""
import pytest
from httpx import AsyncClient

from tests.conftest import admin_headers, staff_headers


@pytest.mark.asyncio
async def test_list_units_public(api_client: AsyncClient, seeded_machine_id: int):
    r = await api_client.get(f"/api/machines/{seeded_machine_id}/units/")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["label"] == "Main"


@pytest.mark.asyncio
async def test_create_unit_requires_staff(api_client, seeded_machine_id):
    r = await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Prusa"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_unit_staff_ok(api_client, seeded_machine_id):
    r = await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Prusa MK4"},
        headers=staff_headers(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["label"] == "Prusa MK4"


@pytest.mark.asyncio
async def test_create_unit_duplicate_label_409(api_client, seeded_machine_id):
    await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Dup"}, headers=staff_headers(),
    )
    r = await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Dup"}, headers=staff_headers(),
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_patch_unit_status_staff_ok(api_client, seeded_machine_id):
    created = (await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "X"}, headers=staff_headers(),
    )).json()
    r = await api_client.patch(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}",
        json={"status": "maintenance"},
        headers=staff_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "maintenance"


@pytest.mark.asyncio
async def test_delete_unit_requires_admin(api_client, seeded_machine_id):
    created = (await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "ToDel"}, headers=staff_headers(),
    )).json()
    r = await api_client.delete(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}",
        headers=staff_headers(),
    )
    assert r.status_code == 403

    r = await api_client.delete(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}",
        headers=admin_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_restore_unit_admin(api_client, seeded_machine_id):
    created = (await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Restorable"}, headers=staff_headers(),
    )).json()
    await api_client.delete(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}",
        headers=admin_headers(),
    )
    r = await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}/restore",
        headers=admin_headers(),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_purge_unit_requires_confirm_label(api_client, seeded_machine_id):
    created = (await api_client.post(
        f"/api/machines/{seeded_machine_id}/units/",
        json={"label": "Purgable"}, headers=staff_headers(),
    )).json()
    r = await api_client.delete(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}?purge=true",
        headers=admin_headers(),
        json={"confirm_label": "WRONG"},
    )
    assert r.status_code == 400
    r = await api_client.delete(
        f"/api/machines/{seeded_machine_id}/units/{created['id']}?purge=true",
        headers=admin_headers(),
        json={"confirm_label": "Purgable"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "purged"
```

If `conftest.py` doesn't already expose `seeded_machine_id`, add a fixture that returns `(await models.get_machines())[0]["id"]`. Check `tests/conftest.py` and mirror existing fixture style (`admin_headers`, `staff_headers` already exist for `test_machines_admin.py` — reuse the same helpers).

**Step 2: Run — verify fail**

Run: `pytest tests/test_units_api.py -v`
Expected: FAIL (404s — routes don't exist).

**Step 3: Implement the router**

Create `api/routes/units.py`:

```python
"""Machine unit management endpoints, nested under /api/machines/{mid}/units/."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_admin, require_staff
from api.deps import notify_embed_update
from db import models

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/machines/{machine_id}/units",
    tags=["units"],
)


# ── Schemas ──────────────────────────────────────────────────────────────

class UnitOut(BaseModel):
    id: int
    machine_id: int
    label: str
    status: str
    archived_at: str | None = None
    created_at: str


class UnitCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class UnitUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["active", "maintenance"] | None = None


class UnitPurgeConfirm(BaseModel):
    confirm_label: str


async def _require_machine(machine_id: int) -> dict:
    m = await models.get_machine(machine_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return m


async def _require_unit(machine_id: int, unit_id: int) -> dict:
    u = await models.get_unit(unit_id)
    if u is None or u["machine_id"] != machine_id:
        raise HTTPException(status_code=404, detail="Unit not found")
    return u


# ── Routes ───────────────────────────────────────────────────────────────

@router.get("/", response_model=list[UnitOut])
async def list_all(
    machine_id: int, include_archived: bool = Query(False)
) -> list[dict]:
    await _require_machine(machine_id)
    return await models.list_units(machine_id, include_archived=include_archived)


@router.post(
    "/",
    response_model=UnitOut,
    status_code=201,
    dependencies=[Depends(require_staff)],
)
async def create(machine_id: int, body: UnitCreate) -> dict:
    await _require_machine(machine_id)
    try:
        u = await models.create_unit(machine_id=machine_id, label=body.label)
    except ValueError as e:
        msg = str(e)
        code = 409 if "already in use" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    notify_embed_update(machine_id)
    return u


@router.patch(
    "/{unit_id}",
    response_model=UnitOut,
    dependencies=[Depends(require_staff)],
)
async def patch(machine_id: int, unit_id: int, body: UnitUpdate) -> dict:
    await _require_unit(machine_id, unit_id)
    try:
        await models.update_unit(unit_id, label=body.label, status=body.status)
    except ValueError as e:
        msg = str(e)
        code = 409 if "already in use" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    notify_embed_update(machine_id)
    after = await models.get_unit(unit_id)
    assert after is not None
    return after


@router.delete("/{unit_id}", dependencies=[Depends(require_admin)])
async def delete(
    machine_id: int,
    unit_id: int,
    purge: bool = Query(False),
    body: UnitPurgeConfirm | None = Body(default=None),
) -> dict:
    u = await _require_unit(machine_id, unit_id)
    try:
        if purge:
            if body is None or body.confirm_label != u["label"]:
                raise HTTPException(
                    status_code=400,
                    detail="confirm_label must equal the unit label",
                )
            await models.purge_unit(unit_id)
            notify_embed_update(machine_id)
            return {"status": "purged"}
        await models.archive_unit(unit_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    notify_embed_update(machine_id)
    return {"status": "archived"}


@router.post(
    "/{unit_id}/restore",
    response_model=UnitOut,
    dependencies=[Depends(require_admin)],
)
async def restore(machine_id: int, unit_id: int) -> dict:
    await _require_unit(machine_id, unit_id)
    try:
        await models.restore_unit(unit_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    notify_embed_update(machine_id)
    after = await models.get_unit(unit_id)
    assert after is not None
    return after
```

In `api/main.py`, register the router alongside the existing ones:

```python
from api.routes import units  # add to imports
app.include_router(units.router)
```

**Step 4: Run — expect pass**

Run: `pytest tests/test_units_api.py -v`
Expected: all PASS. Also run full suite to catch regressions: `pytest -v`.

**Step 5: Commit**

```bash
git add api/routes/units.py api/main.py tests/test_units_api.py
git commit -m "feat(api): nested unit CRUD routes under /api/machines/{id}/units"
```

---

## Task 5: Machine create seeds a default "Main" unit

**Files:**
- Modify: `db/models.py::create_machine`
- Test: `tests/test_machines_db.py`

**Step 1: Write the failing test**

Append:

```python
async def test_create_machine_seeds_main_unit(fresh_db):
    from db import models
    m = await models.create_machine(name="Plotter", slug="plotter")
    units = await models.list_units(m["id"])
    assert len(units) == 1
    assert units[0]["label"] == "Main"
    assert units[0]["status"] == "active"
```

**Step 2: Run — verify fail**

Run: `pytest tests/test_machines_db.py -v -k "seeds_main_unit"`
Expected: FAIL (no unit created).

**Step 3: Implement**

In `db/models.py::create_machine`, after the `RETURNING *` insert, before `await db.commit()`:

```python
await db.execute(
    "INSERT INTO machine_units (machine_id, label) VALUES (?, 'Main')",
    (row["id"],),
)
```

Keep it inside the same transaction (single `commit` at the end).

**Step 4: Run — expect pass**

Run: `pytest tests/test_machines_db.py -v`
Expected: all PASS.

**Step 5: Commit**

```bash
git add db/models.py tests/test_machines_db.py
git commit -m "feat(db): create_machine seeds a default 'Main' unit in same tx"
```

---

## Task 6: Embed the unit list in machine GET responses

**Files:**
- Modify: `api/routes/machines.py` (MachineOut, list_all, get_single)
- Test: `tests/test_machines_admin.py` (new assertions) or extend `test_api.py`

**Step 1: Write the failing test**

Append to the appropriate test file:

```python
@pytest.mark.asyncio
async def test_machine_get_includes_units(api_client, seeded_machine_id):
    r = await api_client.get(f"/api/machines/{seeded_machine_id}")
    assert r.status_code == 200
    data = r.json()
    assert "units" in data
    assert len(data["units"]) >= 1
    assert data["units"][0]["label"] == "Main"


@pytest.mark.asyncio
async def test_machines_list_includes_units(api_client):
    r = await api_client.get("/api/machines/")
    assert r.status_code == 200
    for m in r.json():
        assert "units" in m
```

**Step 2: Run — verify fail**

Run: `pytest -v -k "includes_units"`
Expected: FAIL.

**Step 3: Implement**

In `api/routes/machines.py`:

```python
class UnitSummary(BaseModel):
    id: int
    label: str
    status: str


class MachineOut(BaseModel):
    id: int
    name: str
    slug: str
    status: str
    archived_at: str | None = None
    created_at: str
    units: list[UnitSummary] = []


async def _attach_units(machines: list[dict]) -> list[dict]:
    for m in machines:
        units = await models.list_units(m["id"])
        m["units"] = [
            {"id": u["id"], "label": u["label"], "status": u["status"]}
            for u in units
        ]
    return machines


@router.get("/", response_model=list[MachineOut])
async def list_all(include_archived: bool = Query(False)) -> list[dict]:
    rows = await models.list_machines(include_archived=include_archived)
    return await _attach_units(rows)


@router.get("/{machine_id}", response_model=MachineOut)
async def get_single(machine_id: int) -> dict:
    machine = await models.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    (attached,) = await _attach_units([machine])
    return attached
```

Other endpoints (`patch`, `restore`) that return `MachineOut` should also attach units. Add `await _attach_units([m]); return m` before the return statements.

**Step 4: Run — expect pass**

Run: `pytest -v`
Expected: all PASS.

**Step 5: Commit**

```bash
git add api/routes/machines.py tests/*.py
git commit -m "feat(api): include unit summary list in machine GET responses"
```

---

## Task 7: Discord embed — units block + promotion DM uses unit label

**Files:**
- Modify: `bot/embeds.py::build_machine_embed` (accept a `units` arg)
- Modify: `bot/bot.py::update_queue_embeds` (fetch units and pass them in)
- Modify: `agent/loop.py` — DM already updated in Task 3, verify it's live
- Test: `tests/test_agent.py` or new `tests/test_embeds.py`

**Step 1: Write the failing test**

Create `tests/test_embeds.py`:

```python
from bot.embeds import build_machine_embed


def test_embed_renders_units_block():
    machine = {"id": 1, "name": "3D Printer", "slug": "3d", "status": "active"}
    units = [
        {"id": 10, "label": "Prusa MK4", "status": "active", "serving_name": None},
        {"id": 11, "label": "Bambu X1", "status": "active", "serving_name": "alice"},
        {"id": 12, "label": "Ender 3", "status": "maintenance", "serving_name": None},
    ]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    text = " ".join(f.value for f in embed.fields)
    assert "Prusa MK4" in text
    assert "Bambu X1" in text
    assert "alice" in text
    assert "Ender 3" in text
    assert "maintenance" in text.lower() or "🔧" in text


def test_embed_hides_units_block_when_single_main_unit():
    machine = {"id": 1, "name": "Laser", "slug": "laser", "status": "active"}
    units = [{"id": 10, "label": "Main", "status": "active", "serving_name": None}]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    text = " ".join(f.name for f in embed.fields)
    assert "Units" not in text


def test_embed_all_units_unavailable_when_all_maintenance():
    machine = {"id": 1, "name": "CNC", "slug": "cnc", "status": "active"}
    units = [
        {"id": 10, "label": "A", "status": "maintenance", "serving_name": None},
        {"id": 11, "label": "B", "status": "maintenance", "serving_name": None},
    ]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    text = " ".join(f.value for f in embed.fields)
    assert "unavailable" in text.lower()
```

**Step 2: Run — verify fail**

Run: `pytest tests/test_embeds.py -v`
Expected: FAIL (units kwarg missing).

**Step 3: Implement**

In `bot/embeds.py`:

```python
_STATUS_ICON = {"active": "🟢", "serving": "🔵", "maintenance": "🔧"}


def build_machine_embed(
    machine: dict[str, Any],
    queue_entries: list[dict[str, Any]],
    units: list[dict[str, Any]] | None = None,
) -> discord.Embed:
    status: str = machine["status"]
    colour = _STATUS_COLOURS.get(status, discord.Colour.greyple())
    embed = discord.Embed(title=machine["name"], colour=colour)

    status_display = {
        "active": "Open", "maintenance": "Paused", "offline": "Offline",
    }.get(status, status.capitalize())
    embed.add_field(name="Status", value=status_display, inline=True)

    serving_rows = [e for e in queue_entries if e["status"] == "serving"]
    waiting = [e for e in queue_entries if e["status"] == "waiting"]

    embed.add_field(name="Waiting", value=str(len(waiting)), inline=True)

    # Units block — skip when the machine has a single "Main" unit (back-compat)
    if units is not None and not (
        len(units) == 1 and units[0]["label"] == "Main"
    ):
        active_units = [u for u in units if u["status"] != "archived"]
        if not active_units or all(
            u["status"] == "maintenance" for u in active_units
        ):
            embed.add_field(
                name="Units", value="_All units unavailable_", inline=False
            )
        else:
            lines = []
            for u in active_units:
                if u["status"] == "maintenance":
                    lines.append(f"• {u['label']} — 🔧 maintenance")
                elif u.get("serving_name"):
                    lines.append(f"• {u['label']} — 🔵 {u['serving_name']}")
                else:
                    lines.append(f"• {u['label']} — 🟢 available")
            embed.add_field(name="Units", value="\n".join(lines), inline=False)

    # Keep existing Now Serving / Queue fields
    if serving_rows:
        embed.add_field(
            name="Now Serving",
            value=", ".join(e["discord_name"] for e in serving_rows),
            inline=False,
        )
    else:
        embed.add_field(name="Now Serving", value="--", inline=False)

    if waiting:
        lines = []
        for idx, entry in enumerate(waiting, start=1):
            lines.append(f"**{idx}.** {entry['discord_name']}")
            if idx >= 10:
                remaining = len(waiting) - 10
                if remaining > 0:
                    lines.append(f"*...and {remaining} more*")
                break
        embed.add_field(name="Queue", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Queue", value="*No one waiting*", inline=False)

    embed.set_footer(text=f"Machine: {machine['slug']}")
    return embed
```

In `bot/bot.py::update_queue_embeds`, before building the embed, fetch units and their current serving entries:

```python
units = await models.list_units(machine_id)
serving_entries = await models.get_queue_for_machine(machine_id)
serving_map: dict[int, str] = {}
for e in serving_entries:
    if e["status"] == "serving" and e.get("unit_id"):
        serving_map[e["unit_id"]] = e["discord_name"]
units_view = [
    {
        "id": u["id"], "label": u["label"], "status": u["status"],
        "serving_name": serving_map.get(u["id"]),
    }
    for u in units
]
embed = build_machine_embed(machine, queue_entries=entries, units=units_view)
```

(Adjust the exact variable names to match the existing `update_queue_embeds` body — `machine`, `entries` are likely already defined.)

**Step 4: Run — expect pass**

Run: `pytest tests/test_embeds.py -v && pytest -v`
Expected: all PASS.

**Step 5: Commit**

```bash
git add bot/embeds.py bot/bot.py tests/test_embeds.py
git commit -m "feat(bot): render unit status block in machine embed; hide for single-Main"
```

---

## Task 8: Frontend types + API client for units

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/admin.ts`

**Step 1: Extend types**

```ts
// web/src/api/types.ts
export type UnitStatus = "active" | "maintenance";

export interface MachineUnit {
  id: number;
  machine_id: number;
  label: string;
  status: UnitStatus;
  archived_at: string | null;
  created_at: string;
}

export interface UnitSummary {
  id: number;
  label: string;
  status: UnitStatus;
}

export interface Machine {
  // ... existing fields
  units: UnitSummary[];
}
```

**Step 2: Add admin client functions**

```ts
// web/src/api/admin.ts
import { api } from "./client";
import type { MachineUnit } from "./types";

export async function listUnits(machineId: number): Promise<MachineUnit[]> {
  return api.get(`/api/machines/${machineId}/units/`);
}

export async function createUnit(
  machineId: number, label: string
): Promise<MachineUnit> {
  return api.post(`/api/machines/${machineId}/units/`, { label });
}

export async function updateUnit(
  machineId: number,
  unitId: number,
  body: { label?: string; status?: "active" | "maintenance" }
): Promise<MachineUnit> {
  return api.patch(`/api/machines/${machineId}/units/${unitId}`, body);
}

export async function archiveUnit(machineId: number, unitId: number): Promise<void> {
  return api.delete(`/api/machines/${machineId}/units/${unitId}`);
}

export async function purgeUnit(
  machineId: number, unitId: number, confirmLabel: string
): Promise<void> {
  return api.delete(
    `/api/machines/${machineId}/units/${unitId}?purge=true`,
    { body: { confirm_label: confirmLabel } }
  );
}

export async function restoreUnit(machineId: number, unitId: number): Promise<MachineUnit> {
  return api.post(`/api/machines/${machineId}/units/${unitId}/restore`);
}
```

(Mirror the method signatures used by existing staff/machine admin calls; reuse any `api` helper that's already there.)

**Step 3: Tsc check**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

**Step 4: Commit**

```bash
git add web/src/api/types.ts web/src/api/admin.ts
git commit -m "feat(web): add MachineUnit types + unit admin API client"
```

---

## Task 9: Frontend admin UI — expandable units section per machine

**Files:**
- Modify: `web/src/pages/admin/Machines.tsx`

**Step 1: Add state + rendering**

- Add per-row `expanded` state (e.g. `Set<number>`) and a chevron button in each machine row that toggles it.
- When expanded, render a nested section:
  - Small header row with the machine name and an `[Add unit]` input + save button.
  - One row per active unit: label (click to edit inline), status chip, buttons: `Maintenance` / `Activate`, `Archive` (opens the existing red destructive modal, re-purposed with `confirm_label` retype).
- Optimistic refresh: after any mutation, refetch the machine list (or patch it in place by re-reading `/api/machines/{id}`) so the `units` summary stays current.
- Do NOT allow unit CRUD on archived machines — collapse the section entirely when `machine.archived_at !== null`.

**Step 2: Manual smoke test**

- Start backend (`python main.py`) and frontend (`cd web && npm run dev`).
- Log in as admin (`admin` / `changeme`).
- `/admin/machines`: expand a row, add a unit, rename it, toggle maintenance, archive.
- Refresh and verify the state persisted.
- Attempt to create a duplicate label — expect an inline error.

**Step 3: Commit**

```bash
git add web/src/pages/admin/Machines.tsx
git commit -m "feat(web): admin units UI — expandable per-machine units section"
```

---

## Task 10: Frontend public queue — unit chip strip per machine

**Files:**
- Modify: `web/src/components/QueueCard.tsx` (or `MachineColumn.tsx` — whichever renders one machine card)

**Step 1: Render chips**

For each machine returned by `/api/machines/`:

```tsx
{!(machine.units.length === 1 && machine.units[0].label === "Main") && (
  <div className="flex flex-wrap gap-1 mb-2">
    {machine.units.map(u => (
      <UnitChip key={u.id} unit={u} servingName={servingNameByUnit[u.id]} />
    ))}
  </div>
)}
```

`UnitChip`: small pill, `bg-green-100 text-green-800` when `status='active'` and no serving, `bg-blue-100 text-blue-800` with a name when serving, `bg-gray-200 text-gray-600` when `status='maintenance'`.

`servingNameByUnit` is built from the queue entries already on the card — filter by `status='serving'` and key by `unit_id`. If `unit_id` is null (pre-migration row), fall back to "in use".

**Step 2: Manual smoke test**

- Start bot (so the agent promotes) and the frontend.
- Queue 3 users on a 2-unit machine.
- Public page should show: one green chip (available), one blue chip with display name, queue count = 1.
- Flip a unit to maintenance from the admin panel — page should update within polling interval.

**Step 3: Commit**

```bash
git add web/src/components/QueueCard.tsx
git commit -m "feat(web): render unit chip strip on public queue cards"
```

---

## Task 11: Full regression + cleanup

**Step 1: Run the whole test suite**

```bash
pytest -v
```

Expected: all tests PASS (109 existing + ~25 new). Fix any breakage. Known to verify:
- `test_agent.py::test_advance_queue` — existing single-unit machines still promote exactly one because `"Main"` = 1 active unit.
- `test_machines_admin.py` — machine create now seeds a unit; update any assertion that counts rows.
- `test_api.py::test_get_machines` — responses now include `units`; either assert `units in response[0]` or allow extra keys.

**Step 2: Type + lint frontend**

```bash
cd web && npx tsc --noEmit && npm run lint --if-present
```

**Step 3: Update docs**

- Append a **Completed Work** entry to `CLAUDE.md` with 3–5 bullets.
- Append to `short_term_memory.md`.
- Update `learnings.md` if any new gotchas surfaced.

**Step 4: Commit**

```bash
git add CLAUDE.md short_term_memory.md learnings.md
git commit -m "docs: record multi-unit machines completion"
```

---

## Out of scope (explicit)

- Per-unit analytics. `analytics_snapshots` stays keyed by `machine_id`.
- User-facing unit picking (the bot never asks which unit).
- Historical per-unit uptime or maintenance logs.
- Bulk unit ops (duplicate 5 identical units in one click).

## Rollback

Each task is one commit. If things go sideways after deploy, revert the offending commit(s) — the partial-index and `unit_id` column are additive, so a partial rollback leaves the DB usable (queries that don't read `unit_id` still work).
