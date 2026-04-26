# Discord Signup with UIUC College Picker — Design

**Date:** 2026-04-26
**Branch:** `feat/customizable-admin`
**Status:** Approved (sections 1-6)

## Problem

The current Discord signup modal (`SignupModal` in `bot/cogs/queue.py`) collects `college` as
a free-text field. Users typo, capitalize inconsistently, or write
"Engineering" vs. "Grainger Engineering". The data is unusable for grouping
or filtering. We need a structured, admin-managed list of UIUC colleges with
a real picker UX, plus analytics that can filter and group on it.

## Decisions (from brainstorming)

| # | Decision |
|---|---|
| Q1 | UIUC-only signup. `@illinois.edu` email gate stays. |
| Q2 | Two-step Discord flow: ephemeral `StringSelect` → modal. (Modals can't hold dropdowns.) |
| Q3 | Admin-managed colleges list (new table, new admin page). |
| Q4 | Existing registered users get their `college` wiped + `registered=0` flipped, forcing re-signup on next Join Queue press. |
| Q5 | Re-signup modal prefills `full_name`, `email`, `major`, `graduation_year` so the only forced action is picking a college. |
| Q6 | Store FK only (`users.college_id INTEGER REFERENCES colleges(id)`). Renames in admin propagate automatically. |
| Q7 | New `/admin/colleges` page (own admin tab). |
| Q8 | Pre-seeded with the standard UIUC undergrad/grad colleges; admin can edit/archive after. |
| follow-up | Analytics dashboard gains a "By college" breakdown and a `college_id` filter. |

Approach selected: **Approach 1 — ephemeral select view, then modal.**
True picker UX, atomic register write, prefill-friendly.

## Architecture

### Section 1 — Data model & migration

New table:

```sql
CREATE TABLE colleges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    archived_at TEXT
);
-- created in _migrate, AFTER table creation:
CREATE UNIQUE INDEX idx_colleges_name_active
    ON colleges(name) WHERE archived_at IS NULL;
```

Soft-delete via partial unique index (project convention; learnings.md
2026-04-22 — "Soft-delete + slug reuse needs partial index, not column-level
UNIQUE").

`users` table changes:

- Add `college_id INTEGER REFERENCES colleges(id)` (nullable; FK survives
  archive since archive ≠ delete).
- Drop legacy `college TEXT`. Modern Python's bundled SQLite (≥3.37)
  supports `ALTER TABLE … DROP COLUMN`, so a true drop is safe in `_migrate`.
- Re-signup wipe: `_migrate` runs once per upgrade — `UPDATE users SET
  registered = 0 WHERE registered = 1`. Idempotent because once flipped to 0
  the row no longer matches the predicate. `full_name`, `email`, `major`,
  `graduation_year` are preserved so the modal can prefill them.

Seed via `_seed_colleges` helper called from `init_db` after
`_seed_machines` (per learnings.md 2026-04-22 — "Backfill migrations must
run AFTER seed steps"). Idempotent `INSERT … WHERE NOT EXISTS`. Initial
list:

1. Grainger College of Engineering
2. Gies College of Business
3. College of Liberal Arts and Sciences
4. College of Agricultural, Consumer and Environmental Sciences
5. College of Education
6. College of Fine and Applied Arts
7. College of Media
8. School of Information Sciences
9. College of Applied Health Sciences
10. Division of General Studies
11. School of Social Work
12. School of Labor and Employment Relations
13. Carle Illinois College of Medicine
14. College of Veterinary Medicine
15. College of Law

Cascade rules:

- `archive_college` — always allowed; FK kept on user records. Archived
  rows do not appear in the StringSelect.
- `purge_college` (admin hard-delete) — blocked with 409 if any
  `users.college_id` references it.

### Section 2 — Discord flow

```
Join Queue press
    │
    ▼
[unregistered or college_id IS NULL]?
    │ yes
    ▼
interaction.response.send_message(
    "Pick your UIUC college:",
    view=CollegeSelectView(bot, user_id, machine_id, prefill),
    ephemeral=True,
)
    │
    ▼ (user picks)
StringSelect.callback
    │  send_modal(SignupModal(bot, user_id, machine_id,
    │                         college_id, prefill))
    ▼
SignupModal.on_submit
    │  validate email + grad_year (existing checks)
    │  models.register_user(... college_id=college_id ...)
    │  proceed to existing join_queue path
```

`CollegeSelectView`:

- `discord.ui.View(timeout=120)` (matches `MachinePicker` pattern in
  `bot/cogs/dm.py`; ephemeral views use bounded timeouts per learnings.md
  2026-04-02).
- Single `StringSelect(custom_id=f"signup_college:{user_id}:{machine_id}",
  placeholder="Select your college", min_values=1, max_values=1)`.
- Options built from `await models.list_active_colleges()`. Capped at 25
  (Discord limit); seed is 15. If somehow >25, slice and log warning.
- `select.callback` → `int(select.values[0])` is `college_id`, opens the
  modal.
- Empty list edge case → ephemeral "Sign-ups temporarily unavailable —
  please contact staff."
- Not persistent; no `bot.add_view()` registration needed (learnings.md
  2026-04-01).

`SignupModal` (revised):

- Loses the `college` `TextInput`. Now 4 inputs: `full_name`, `email`,
  `major`, `graduation_year`.
- New `__init__` params: `user_id, machine_id, college_id, prefill: dict | None`.
- `prefill` populates each `TextInput.default` for re-signup.
- Title becomes `"SCD Queue — Sign Up ({college_name})"`.
- `on_submit` calls `models.register_user(..., college_id=college_id, ...)`.

### Section 3 — API & models

`db/models.py` additions:

```python
async def list_active_colleges() -> list[dict]:           ...
async def list_all_colleges() -> list[dict]:              ...
async def get_college(college_id: int) -> dict | None:    ...
async def create_college(name: str) -> dict:              ...  # 409 on dup
async def update_college(college_id: int, *, name: str) -> dict | None: ...
async def archive_college(college_id: int) -> bool:       ...
async def restore_college(college_id: int) -> bool:       ...  # 409 if active twin
async def purge_college(college_id: int) -> bool:         ...  # raises if referenced
async def count_users_in_college(college_id: int) -> int: ...
```

Existing helpers updated:

- `register_user(user_id, *, full_name, email, major, college_id: int, graduation_year)` — drops `college: str`, adds `college_id: int`.
- `update_user_profile(...)` — same signature change.
- `get_user_by_discord_id` / `get_or_create_user` return dicts now exposing
  `college_id`.

New router `api/routes/colleges.py` (mirrors `api/routes/machines.py`):

| Method | Path                              | Auth         | Notes |
|--------|-----------------------------------|--------------|-------|
| GET    | `/api/colleges/`                  | public       | Active only; strips `archived_at` / `created_at`. |
| GET    | `/api/colleges/?include_archived=true` | staff   | All rows. |
| POST   | `/api/colleges/`                  | admin        | `{name}` → 201 / 409. |
| PATCH  | `/api/colleges/{id}`              | admin        | Rename, 409 on dup active. |
| DELETE | `/api/colleges/{id}`              | admin        | Soft-archive. |
| POST   | `/api/colleges/{id}/restore`      | admin        | Restore archived. |
| DELETE | `/api/colleges/{id}?purge=true`   | admin        | Hard delete; body `{confirm_name}` must match; 409 if `count_users_in_college > 0`. |

Mounted in `api/main.py` next to existing routers. CORS unchanged.

### Section 4 — Admin web UI

New page `web/src/pages/admin/Colleges.tsx`, route registered in `App.tsx`
under `<RequireAdmin>`. New "Colleges" tab in the admin sub-tab strip
(sibling of Machines / Staff / Settings).

Layout mirrors `Machines.tsx`:

- Add form (single text field).
- Table: Name (inline-edit on click → PATCH), user count badge, Edit /
  Archive buttons.
- "Show archived" toggle reveals archived rows with Restore + red Purge
  buttons.
- Purge modal requires admin to retype the college name verbatim
  (`confirm_name` pattern, mirrors `confirm_slug` from machines).

API client additions in `web/src/api/admin.ts`:
`listColleges`, `listAllColleges`, `createCollege`, `updateCollege`,
`archiveCollege`, `restoreCollege`, `purgeCollege`. Types
`AdminCollege` / `CollegeSummary` in `web/src/api/types.ts`.

State management: existing `useState`/`useEffect` pattern; refetch after
each mutation (consistent with current admin pages, list is small).

### Section 5 — Analytics by college

`compute_analytics_response(period, start, end, machine_id=None)` (the
shared helper from the analytics chatbot work, 2026-04-26) gains:

- New parameter `college_id: int | None = None`. When set, every
  count/duration filters via `queue_entries.user_id IN (SELECT id FROM users
  WHERE college_id = ?)`.
- New aggregation block in the response:

```python
class CollegeStat(BaseModel):
    college_id: int
    college_name: str
    total_jobs: int
    completed_jobs: int
    unique_users: int
    avg_wait_mins: float | None
    avg_serve_mins: float | None

class AnalyticsResponse(BaseModel):
    ...
    colleges: list[CollegeStat]   # always returned, ordered by total_jobs desc
```

Built by JOINing `users` and grouping on `college_id` → `colleges.name`.
Users with `college_id IS NULL` bucket under a synthetic
`college_id=0, college_name="Unspecified"` row so totals reconcile across
the transition.

API:

- `GET /api/analytics/summary?period=week&college_id=3` — drill into a
  single college. Composes with `machine_id`.

Frontend (`web/src/pages/Analytics.tsx`):

- New "By college" card next to the existing "By machine" card. Same
  shape — bar chart of `total_jobs`, click a row to set `college_id` query
  param.
- New filter dropdown ("College: All / Grainger / Gies / …") populated
  from `GET /api/colleges/`.
- Active-filter chips at the top so users can clear individual filters.

Chatbot (`api/routes/chat.py`) automatically picks up the new dimension
because the system prompt embeds the same `compute_analytics_response`
output. One added test asserts the model receives the `colleges` array.

`analytics_snapshots` table is **not** extended with `college_id`. Live
queries hit `queue_entries`-with-`users` JOIN directly. Snapshots stay a
per-machine cache. If college-grouped historical perf becomes slow at
scale, add a `college_snapshots` table later (YAGNI).

### Section 6 — Error handling & testing

| Surface                                         | Failure                       | Behavior |
|-------------------------------------------------|-------------------------------|----------|
| Discord — empty colleges list                   | Admin archived everything     | Ephemeral "Sign-ups temporarily unavailable — please contact staff." |
| Discord — select view timeout (120s)            | User idles                    | View self-disables; user re-presses Join Queue. |
| Discord — modal submit, college archived in race| Race                          | `register_user` finds row exists; proceeds. Archive ≠ delete. |
| Discord — invalid email / grad year             | Existing validation           | Existing ephemeral error responses. |
| Discord — `register_user` raises                | DB outage                     | Ephemeral "Something went wrong, try again." Logged. |
| API — POST dup name                             | Concurrent admin add          | 409 `"College already exists"`. |
| API — PATCH rename to active dup                | Conflict                      | 409. |
| API — DELETE `?purge=true` with users           | Guard                         | 409 `"N users reference this college"`. |
| API — analytics with bad `college_id`           | Bad input                     | 404. |
| Migration                                       | Idempotent                    | `WHERE registered = 1` predicate ensures no-op on re-run. |

New tests:

1. `tests/test_colleges_db.py` (~10) — CRUD + partial-index dup +
   `count_users_in_college` + purge guard.
2. `tests/test_colleges_api.py` (~12) — full route coverage; auth gates;
   409 dup; 409 purge with users; `confirm_name` mismatch → 400.
3. `tests/test_signup_flow.py` (~8) — Discord-side. Mocks `Interaction`.
   Asserts: send_message-with-view (not send_modal); select callback opens
   modal with correct `college_id`; modal submit calls
   `register_user(college_id=...)`; re-signup prefill matches stored
   values; empty list path.
4. `tests/test_analytics_api.py` (extend, +3) — `college_id` filter
   narrows; `colleges` block present; "Unspecified" bucket aggregates
   `college_id IS NULL`.
5. `tests/test_chat_api.py` (extend, +1) — system prompt analytics blob
   contains `colleges` array.
6. `tests/test_db.py` (extend) — fresh DB has seeded colleges; upgrade
   path flips `registered=1` → `registered=0` and prefill values are
   intact.

Test budget: 172 → ~205.

Manual smoke checklist:

- Fresh DB → bot → Join Queue → select view appears with seeded colleges.
- Pick Grainger → modal opens (prefilled empty for new user).
- Submit → joins queue, embed updates, DM received.
- Archive Grainger via `/admin/colleges` → user re-joins → Grainger absent
  from list.
- Analytics → filter by college → numbers update; bar chart shows by-college
  breakdown.

## Out of scope

- Non-UIUC users (Q1 = A).
- Granular per-college admin permissions.
- Migrating `analytics_snapshots` with `college_id` dimension.
- Discriminating undergrad vs grad colleges in the picker (admin can
  edit list).
- Historical retroactive backfill of `college_id` for archived users —
  legacy free-text values are dropped, not parsed.
