# Post-Visit Feedback Form — Design

**Date:** 2026-04-27
**Branch:** `feat/customizable-admin`
**Status:** Approved (sections 1-6)

## Problem

Once a user finishes using a machine and answers the existing
"Did your job succeed? Yes/No" DM, the system records the
completion outcome but captures no signal about how the visit
*felt*. We want an optional 1-5 star rating + free-text comment per
visit, and we want it to roll into both the analytics dashboard and
a dedicated admin browse page so staff can spot patterns and
follow up on bad experiences.

## Decisions (from brainstorming)

| # | Decision |
|---|---|
| Q1 | Discord-only (no web form). |
| Q2 | 1-5 stars + optional comment modal. Modal field is `required=False`. |
| Q3 | Trigger after the user answers the existing Yes/No success question (the "user-acknowledged" completion path). Skip the agent-expired and staff-completed paths. |
| Q4 | Both: analytics dashboard cards roll feedback into machine and college blocks AND a dedicated `/admin/feedback` page lists raw rows. |
| Q5 | Fully attributed in admin UI — full_name, college, machine, rating, comment all visible. |
| Q6 | One feedback per queue_entry (UNIQUE), not editable. |

Approach selected: **Approach 1 — follow-up DM with rating view.**
Cleanest separation from the success/failure flow, easiest to test, highest response rate.

## Architecture

### Section 1 — Schema + migration

```sql
CREATE TABLE feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_entry_id  INTEGER NOT NULL UNIQUE
                    REFERENCES queue_entries(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_feedback_created_at ON feedback(created_at DESC);
```

- `queue_entry_id UNIQUE` enforces one feedback per visit (Q6).
- `ON DELETE CASCADE` so a queue_entry purge takes its feedback with
  it; mirrors `chat_messages` cascade pattern.
- `CHECK (rating BETWEEN 1 AND 5)` defends against future non-Discord
  callers writing bad data.
- No denormalized columns. `user_id`, `machine_id`, `college_id` come
  from JOIN through `queue_entries → users → colleges` and
  `queue_entries → machines`. Renames propagate, consistent with the
  college-signup FK-only decision (2026-04-26).

Migration:
- `_create_tables` adds the table for fresh DBs.
- `_migrate` adds it idempotently with `CREATE TABLE IF NOT EXISTS`,
  then `CREATE INDEX IF NOT EXISTS` for `idx_feedback_created_at`
  (per project convention: indexes in `_migrate` post-CREATE).
- No backfill: existing completed entries don't get synthetic
  feedback.

Cascade auditing: existing `purge_machine` and friends already
`DELETE FROM queue_entries WHERE machine_id = ?` before the parent
delete; `feedback` rides on the new ON DELETE CASCADE automatically.
No `purge_*` helpers need updating.

### Section 2 — Discord flow

In `bot/cogs/dm.py`, both Yes (success) and No (failure-with-notes)
branches of the existing `FallbackActions` view, immediately after
the `update_entry_status(... "completed", ...)` call succeeds, send
a fresh DM:

```
"How was your experience using {machine_name}?"
[★ 1] [★ 2] [★ 3] [★ 4] [★ 5]   (row 0)
[Skip]                            (row 1)
```

Wrapped in `try/except discord.Forbidden` (existing pattern); failures
log and continue.

`RatingView`:
```python
class RatingView(discord.ui.View):
    def __init__(self, *, queue_entry_id: int, machine_name: str) -> None:
        super().__init__(timeout=600)  # 10 min; ephemeral pattern
        self._queue_entry_id = queue_entry_id
        self._machine_name = machine_name
```
Five star buttons with `custom_id="rate:{id}:{n}"` for n in 1..5; one
Skip button with `custom_id="rate:{id}:skip"`. Star callbacks open
`FeedbackModal(queue_entry_id, rating)`. Skip edits the message to
"Thanks anyway!" and disables buttons.

`FeedbackModal`:
```python
class FeedbackModal(discord.ui.Modal, title="Tell us more (optional)"):
    comment = discord.ui.TextInput(
        label="Your feedback",
        style=discord.TextStyle.paragraph,
        placeholder="What worked? What didn't? (optional)",
        required=False,
        max_length=500,
    )
```
`on_submit` calls `models.create_feedback(queue_entry_id, rating,
comment or None)`. On `FeedbackAlreadyExistsError`, ephemeral
"You've already submitted feedback for this visit." On success,
ephemeral "Thanks for the {n}★ rating!".

Idempotency: UNIQUE on `queue_entry_id` blocks dupes; no public
side-effects. Failure DMs (status='completed' but `job_successful=0`)
get the same rating prompt — bad outcomes are exactly the visits we
want feedback on.

### Section 3 — API + models

`db/models.py`:
```python
class FeedbackAlreadyExistsError(Exception): ...

async def create_feedback(*, queue_entry_id: int, rating: int,
                          comment: str | None) -> dict: ...
async def get_feedback_by_entry(queue_entry_id: int) -> dict | None: ...
async def list_feedback(*, limit: int = 50, machine_id: int | None = None,
                        college_id: int | None = None,
                        min_rating: int | None = None,
                        max_rating: int | None = None) -> list[dict]: ...
async def feedback_aggregates_overall(start: str, end: str, *,
                                      college_id: int | None = None,
                                      machine_id: int | None = None) -> dict: ...
async def feedback_aggregates_by_machine(start: str, end: str, *,
                                         college_id: int | None = None) -> dict[int, dict]: ...
async def feedback_aggregates_by_college(start: str, end: str, *,
                                         machine_id: int | None = None) -> dict[int, dict]: ...
```

`list_feedback` JOINs `feedback → queue_entries → users → colleges`
and `queue_entries → machines`, returns `id, queue_entry_id, rating,
comment, created_at, user_id, full_name, discord_name, machine_id,
machine_name, college_id, college_name` (with `'Unspecified'` for
NULL). Ordered `created_at DESC`. Filters compose.

Aggregate helpers return `{avg_rating, rating_count}`. Single GROUP
BY query each, no N+1.

`api/routes/feedback.py` (new):

| Method | Path | Auth | Behavior |
|---|---|---|---|
| GET | `/api/feedback/` | staff | Filtered list. Query params `limit`, `machine_id`, `college_id`, `min_rating`, `max_rating`. |

No POST/PATCH/DELETE — feedback is one-shot from Discord; admin
write would be a tamper surface. No public route. Bot writes
directly via `models.create_feedback`.

Mounted in `api/main.py` next to existing routers.

### Section 4 — Analytics aggregation

`compute_analytics_response` (the helper shared by dashboard and
chatbot system prompt) extends the existing summary, machines, and
colleges blocks:

```python
class AnalyticsSummary(BaseModel):
    ...
    avg_rating: float | None
    rating_count: int

class MachineStat(BaseModel):
    ...
    avg_rating: float | None
    rating_count: int

class CollegeStat(BaseModel):
    ...
    avg_rating: float | None
    rating_count: int
```

SQL: each block gains a LEFT JOIN onto `feedback` with `AVG(rating)`
and `COUNT(rating)`. LEFT JOIN preserves entries without feedback in
the existing `total_jobs`. `avg_rating` is `NULL` when
`rating_count = 0`; Pydantic surfaces it as `None`.

Filters compose: when `college_id=3` is set, the per-machine
`avg_rating` reflects only that college's feedback.

Snapshots: `analytics_snapshots` gains `avg_rating REAL NULL` and
`rating_count INTEGER NOT NULL DEFAULT 0`. The agent's
`_compute_daily_snapshots` re-runs to JOIN feedback and populate the
columns. Already-snapshotted days carry NULL/0 — acceptable.

`/api/analytics/today` is a live endpoint, so it picks up the new
fields automatically once the model adds them.

Chatbot needs no prompt change. One smoke test asserts
`"avg_rating"` appears in the system prompt JSON.

### Section 5 — Admin Feedback page

`/admin/feedback` route in `web/src/App.tsx`, wrapped in
`<RequireStaff>` (read-only browse). New "Feedback" sub-tab in the
admin nav alongside Machines / Colleges / Staff / Settings.

`web/src/pages/admin/Feedback.tsx`:

```
┌─ Feedback ─────────────────────────────────────────────────┐
│  Machine: [All ▾]   College: [All ▾]   Rating: [Any ▾]    │
│                                                             │
│  Time             User                Machine     Rating   │
│  ───────────────  ──────────────────  ─────────   ──────   │
│  2026-04-27 14:32 Alex Chen (Grainger) Laser Cut  ★★★★☆   │
│  └ "Great machine, queue moved fast."                       │
│                                                             │
│  [Load more]                                                │
└─────────────────────────────────────────────────────────────┘
```

Components:
- Filter row: Machine and College dropdowns sourced from existing
  public clients; Rating filter is local (`Any / 1 / 2 / 3 / 4 / 5
  / Below 3 / Below 4`). Filter changes refetch.
- Row: timestamp (relative + tooltip absolute), user as
  `{full_name} ({college_name})` per Q5, machine name, 5
  filled/empty stars, comment under the row in muted text.
  `(no comment)` placeholder for NULL.
- Pagination: `limit=50`, "Load more" appends. Cursor by
  `created_at`.
- Empty state: "No feedback yet for these filters."

State: `useState`/`useEffect`, refetch on filter change. Mirrors
`Colleges.tsx`.

Types in `web/src/api/types.ts`:
```typescript
export interface FeedbackRow {
  id: number;
  queue_entry_id: number;
  rating: number;
  comment: string | null;
  created_at: string;
  user_id: number;
  full_name: string;
  discord_name: string;
  machine_id: number;
  machine_name: string;
  college_id: number | null;
  college_name: string;
}
```

API client in `web/src/api/admin.ts`:
```typescript
export const listFeedback = (params: {
  machineId?: number; collegeId?: number;
  minRating?: number; maxRating?: number; limit?: number;
}): Promise<FeedbackRow[]> => request("/feedback/", { query: params });
```

Analytics dashboard accents: each machine/college card gets a
`★ 4.3 (87)` line under utilization. No new component.

No comment search, no CSV export — YAGNI.

### Section 6 — Error handling & testing

| Surface | Failure | Behavior |
|---|---|---|
| DM blocked | Forbidden | Log and continue (existing pattern). |
| RatingView timeout | Idle 10 min | Buttons stop responding. |
| FeedbackModal duplicate | Race / dupe | `FeedbackAlreadyExistsError` → ephemeral "already submitted". |
| FeedbackModal — entry purged | FK violation | Ephemeral "Visit no longer found — feedback discarded." |
| API bad filter | Validation | 422 via Pydantic. |
| Migration | Idempotent | IF NOT EXISTS guards. |
| Snapshot pre-existing days | New cols | NULL / 0 acceptable. |

Tests (~21 new, total ~221):

1. `tests/test_feedback_db.py` (~7) — CRUD, dup-error, filters,
   ordering, CHECK constraint, cascade on queue_entry delete.
2. `tests/test_feedback_api.py` (~5) — auth, joined-fields shape,
   filters, pagination, 422.
3. `tests/test_feedback_flow.py` (~5) — Discord-side: DM after
   completion, modal opens with rating, modal writes row, Skip
   writes nothing, duplicate ephemeral.
4. `tests/test_analytics_api.py` (extend, +3) — summary
   `avg_rating` None when empty, matches when present, per-machine
   and per-college blocks include rating fields.
5. `tests/test_chat_api.py` (extend, +1) — system prompt JSON
   contains `"avg_rating"`.
6. `tests/test_agent.py` (extend, +1) — daily snapshot populates
   `avg_rating` / `rating_count`.

Manual smoke:
- Complete a queue entry → Yes/No → rating DM arrives.
- Pick 4★ → modal opens → submit blank → row written.
- Re-rate same entry → "already submitted".
- Skip → no row written.
- `/admin/feedback` shows the row.
- Analytics card shows `★ 4.0 (1)`.

## Out of scope

- Editable feedback (Q6 = one-shot).
- Web feedback form / public feedback API write path.
- Per-visit anonymity toggle (Q5 = always attributed).
- Comment full-text search.
- CSV export.
- Re-prompt for users who skipped feedback.
- Feedback DM for staff-completed and agent-expired completions
  (Q3 limited to user-acknowledged path).
- Snapshot backfill for historical days.
