# Multi-Unit Machines — Design

**Date:** 2026-04-22
**Branch:** `feat/customizable-admin` (follow-up) or a new branch off `main`
**Status:** Design approved; implementation plan to follow.

## Problem

A single "machine" in the SCD makerspace is often several physical units of the same class (three 3D printers, two laser cutters). The current schema has one row in `machines` per user-facing machine, one embed, one serving slot. Admins need a way to represent quantity while keeping a shared FIFO queue, and staff need to mark individual units down for maintenance without taking the whole queue offline.

## Decisions (from brainstorm)

- **Semantic model:** individual labeled units under a parent machine; units share one FIFO queue; agent auto-assigns a free unit on promotion.
- **Admin UX:** explicit labeled units (add/remove/rename individually). No "quantity" shortcut.
- **Discord UX:** one embed per machine, with a "Units" block showing per-unit status. No unit-picking at join time — bot tells the user which unit when they're promoted.

## Data model

New table:

```sql
CREATE TABLE machine_units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id   INTEGER NOT NULL REFERENCES machines(id),
    label        TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'active',  -- {active, maintenance}
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    archived_at  TEXT
);

-- Label is unique per machine only among non-archived rows (mirrors
-- machines.slug partial index; see learnings.md 2026-04-22 entry).
CREATE UNIQUE INDEX idx_machine_units_label_active
    ON machine_units(machine_id, label)
    WHERE archived_at IS NULL;
```

Column added to `queue_entries`:

```sql
ALTER TABLE queue_entries ADD COLUMN unit_id INTEGER REFERENCES machine_units(id);
-- Populated only when status='serving'. NULL for waiting/completed/cancelled.
```

### Migration

1. Create `machine_units`, partial unique index, `unit_id` column — all in `_migrate`, post-ALTER (per the learnings.md partial-index rule).
2. For every existing non-archived machine, insert one unit labeled `"Main"`.
3. Machine `POST /api/machines/` is wrapped in a transaction that creates the machine and seeds one `"Main"` unit, so new machines work identically to old ones.

## Queue agent logic

Per tick, per non-paused machine:

```
active_unit_count = count(units WHERE status='active' AND archived_at IS NULL)
serving_count     = count(queue_entries WHERE machine_id=? AND status='serving')

while serving_count < active_unit_count AND waiting entry exists:
    entry       = oldest waiting queue_entry
    unit_id     = first active unit with no active serving entry
    promote(entry, unit_id)          -- status='serving', serving_at=now(), unit_id
    serving_count += 1
```

Rules:

- Machine paused → no promotions (unchanged).
- Unit flipped to maintenance while someone is serving → the user keeps serving; the slot does not re-open to that unit until it's reactivated.
- Archive a unit with an active serving entry → **409**, same pattern as machine archive.
- `active_unit_count == 0` → effectively paused; embed surfaces this.
- Reminder + grace-expiry logic unchanged (operates per queue entry).

## API

Nested under machines, same auth split as existing routes (staff mutate, admin destroy):

| Method | Path | Auth | Body |
|---|---|---|---|
| GET | `/api/machines/{mid}/units/` | public | — |
| POST | `/api/machines/{mid}/units/` | staff | `{label}` |
| PATCH | `/api/machines/{mid}/units/{uid}` | staff | `{label?, status?}` |
| DELETE | `/api/machines/{mid}/units/{uid}` | admin | — (soft archive) |
| DELETE | `/api/machines/{mid}/units/{uid}?purge=true` | admin | `{confirm_label}` |
| POST | `/api/machines/{mid}/units/{uid}/restore` | admin | — |

- `GET /api/machines/` and `GET /api/machines/{id}` responses include a `units: [...]` array (denormalized to avoid N+1 from the public page).
- Label validation: trimmed, 1–64 chars, human-readable (no slug regex).
- Duplicate label on same machine → 409 (caught from `IntegrityError` on partial index).
- All mutating endpoints call `notify_embed_refresh(machine_id)` via `api/deps.py`.

## Admin web UI

`/admin/machines` rows get an expand chevron. Expanded state renders a nested unit list per machine:

```
▼ 3D Printer                       [Add unit]
    ├ Prusa MK4        active       [Edit] [Maintenance] [Archive]
    ├ Bambu X1         active       [Edit] [Maintenance] [Archive]
    └ Ender 3          maintenance  [Edit] [Activate]    [Archive]
```

- Inline add: single text input + Save.
- Inline rename: click label → edit in place → blur-to-save.
- Status toggle: single button, flips active ↔ maintenance.
- Archive: red destructive modal with `confirm_label` retype (reuses machine purge pattern).
- Archived machines hide their unit section — restore the machine first.

## Discord embed

Description gains a **Units** block between title and queue list:

```
**Units**
• Prusa MK4 — 🟢 @alice (serving 14m)
• Bambu X1 — ⚪ available
• Ender 3 — 🔧 maintenance

**Queue (3 waiting)**
1. @bob — 2m ago
2. @carol — 5m ago
3. @dave — 8m ago
```

- Archived units hidden.
- `active_unit_count == 0` → block collapses to `_All units unavailable_`.
- Join/Check/Leave buttons unchanged (operate on machine, not unit).
- Promotion DM: `"You're up! Head to the **Prusa MK4**."` Falls back to `"You're up!"` when the only unit is labeled `"Main"` (back-compat for single-unit machines).
- Embed re-renders on: unit create/update/archive/restore, queue mutations, and agent promotion (the existing per-tick refresh covers promotion).

## Public queue frontend (React)

Each machine card shows a unit chip strip above the queue:

```
[🟢 Prusa MK4]  [🔵 Bambu X1 — alice]  [⚫ Ender 3 — maint]
```

- Green = available, blue = serving (with display name), gray = maintenance.
- Strip is hidden when `units.length === 1 && label === "Main"` — single-unit machines keep today's look.
- Chips are read-only.

## Error handling

- Label collision → 409 `{"detail": "label already in use for this machine"}`
- Archive unit with serving entry → 409 `{"detail": "unit has an active serving entry"}`
- Purge without matching `confirm_label` → 400
- Missing machine/unit → 404
- Non-staff on POST/PATCH → 401/403
- Non-admin on DELETE/restore → 403

## Testing

On top of the existing 109 tests:

- Migration: existing machines get exactly one unit labeled `"Main"`; rerunning the migration is idempotent.
- Capacity: 3-active-unit machine promotes 3 from FIFO, then holds.
- Maintenance exclusion: flipping one of 3 to maintenance caps promotions at 2.
- Archive blocked: archiving a unit with an active serving entry → 409.
- Unit auto-assignment: promotion picks the first active unit without a serving entry (deterministic).
- Label uniqueness: duplicate label → 409; after archive, reuse succeeds.
- Auth: POST/PATCH staff-only; DELETE/purge/restore admin-only.
- Embed refresh bridge: unit mutations invoke `notify_embed_refresh`.
- Frontend: chip strip hidden for single-"Main" case; visible otherwise.

## Out of scope

- Per-unit analytics breakdown (current `analytics_snapshots` stays keyed by `machine_id`).
- User-facing unit preference at join time.
- Unit-specific status messages or color theming.
- Historical per-unit uptime tracking.
