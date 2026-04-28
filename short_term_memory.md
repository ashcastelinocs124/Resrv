# Short-term Memory

## 2026-04-27 — Self-Service Staff Tooling
Shipped on `feat/customizable-admin`. 294 tests passing, `npx tsc -b` clean.

**Schema (commits 4b25efd + 6dc1d66):**
- `agent_conversations` (id, staff_user_id FK, title, created_at, updated_at).
- `agent_messages` (FK ON DELETE CASCADE, role CHECK, content,
  `tool_call_id`, `tool_calls_json`, `chart_spec_json`).
- `pinned_charts` (id, chart_spec_json, title, created_by FK, pin_order, created_at).
- `staff_users.onboarded_at TEXT NULL`; existing rows backfilled to
  `datetime('now')` so only NEW staff trigger the first-login tour.
- Seeded settings: `data_analyst_enabled=false`,
  `data_analyst_visible_to_staff=false`.
- Indexes `idx_agent_msgs_conv` and `idx_pinned_charts_order` created
  post-CREATE in `_migrate`.

**Endpoints (commits c39f8d2 + 0f1b4e5 + 84313f6):**
- `GET /api/me/features` → `{data_analyst_visible}`. Admin sees true when
  master is on; staff sees true only when both master + visibility flags on.
- `POST /api/auth/me/onboarded` stamps `onboarded_at`. Idempotent (200 even
  if already stamped). `/api/auth/me` now exposes `onboarded_at`.
- `require_data_analyst` dependency in `api/auth.py`: 503 when master off,
  403 when visibility off and caller is staff.
- `POST/GET/DELETE /api/analytics/agent` + `/conversations` + `/models` +
  SSE `/stream`. OpenAI tool-call loop, 4 round-trip cap (forced fallback
  with `tool_choice="none"` after cap), 1000-row tool cap. Cross-user reads
  return 404 (CLAUDE.md convention).
- `GET/POST/DELETE /api/pinned-charts` + `POST /{id}/refresh`. Refresh
  re-runs the chart's saved `context.{filter, group_by, metric, period}`
  via `query_jobs` and rewrites `data`.
- `data_analyst_enabled` + `data_analyst_visible_to_staff` added to
  `ALLOWED_KEYS` in `api/routes/settings.py`.

**Agent tools (commit 847d8b5, `api/routes/agent_tools.py`):**
- `query_jobs(filter, group_by, metric, period)` — group_by ∈
  {machine, college, status, day, hour, user}; metric ∈ {count,
  completed_count, no_show_count, cancelled_count, failure_count,
  unique_users, avg_wait_mins, avg_serve_mins, avg_rating}.
- `query_feedback(filter, group_by, period)` — group_by ∈
  {machine, college, rating}; emits avg_rating + count.
- `query_funnel(filter, period)` — joined/served/completed/no_show/
  cancelled/failure scalars.
- `top_n(filter, group_by, metric, n, period)` — caps n at 100.
- `compare_periods(filter, metric, period_a, period_b)` — returns
  `{a, b, delta_abs, delta_pct}` with named windows
  (today/yesterday/this_week/last_week/this_month/last_month).
- `make_chart(data, type, x, y, title, context?)` — pure formatter; type
  ∈ {bar, line, pie, table}; `context` preserved for refresh.
- All tools cap at 1000 rows + emit `truncated: bool`.

**SSE protocol (`/api/analytics/agent/stream`):**
`meta` → `tool_call*` → `chart?` → `delta` (single, full final text) →
`done`. `error` short-circuits. Inner OpenAI calls are non-streaming;
SSE frames *loop progress* rather than token deltas (simpler than the
chat streaming path).

**Frontend (commits 84313f6 + 2db2918 + da81803 + 1ba2503):**
- `<ChartFromSpec>` dispatches bar/line/pie/table to Recharts.
- `<CustomCharts>` section below `MachineTable` on `/analytics`; lists
  `listPinnedCharts()` results, hidden when empty. Refresh + Unpin
  per card with confirm.
- `<AnalystAgent>` floating panel, **bottom-LEFT** (existing
  `<AnalyticsChat>` is bottom-RIGHT). Uses `postAgentStream` with
  tool_call/chart event handlers. Inline chart rendering + Pin button
  with title-edit row → `pinChart(spec, title)`. Mounted only when
  `fetchFeatures().data_analyst_visible` is true.
- `<AdminSettings>` gains a "Data analyst agent" section with two
  checkboxes; `visible_to_staff` is disabled when the master is off.
- Onboarding tour: `driver.js`, 11 steps, `requiresAdmin` filter for
  Staff/Settings stops. `runTour(navigate, isAdmin)` waits up to 1.5s
  for each anchor to render after `navigateTo`. Auto-runs in
  `<OnboardingGate>` (lives inside `<AuthProvider>` + Router) when
  `onboarded_at` is null; calls `markOnboarded()` after the tour
  completes. NavBar gains a "Replay tour" button that re-runs without
  re-stamping.
- Auth context tracks `onboardedAt` and exposes `markOnboardedLocal()`.

**Conventions reinforced:**
- Settings cache (`api/settings_store._cache`) is module-level — leaks
  across in-memory DB resets. `tests/conftest.py` now invalidates it on
  every test (`_use_in_memory_db` autouse fixture).
- Server-side model allowlist mirrored in
  `api/routes/agent.py::ALLOWED_MODELS`; default `gpt-5.4-mini`.
- `mark_staff_onboarded` UPDATE is idempotent (`WHERE onboarded_at IS NULL`).

## 2026-04-27 — Post-Visit Feedback Form
Shipped on `feat/customizable-admin`. 231 tests passing, tsc clean.

**Backend:**
- New `feedback` table (id, queue_entry_id UNIQUE FK ON DELETE CASCADE,
  rating CHECK(1..5), comment TEXT NULL, created_at) + `idx_feedback_created_at`
  (created in `_migrate` post-CREATE).
- `analytics_snapshots` gains `avg_rating REAL NULL` and
  `rating_count INTEGER NOT NULL DEFAULT 0`.
- `db/models.py`: `create_feedback`, `get_feedback_by_entry`, `list_feedback`
  (joined w/ users + machines + colleges, composable filters), aggregate
  helpers (overall, by_machine, by_college). `FeedbackAlreadyExistsError` on dup.
- `api/routes/feedback.py`: staff GET `/api/feedback/` with filters
  (`limit`, `machine_id`, `college_id`, `min_rating`, `max_rating`).
  No write/edit endpoints — bot is the only writer.
- `compute_analytics_response`: summary, machines, colleges blocks all gain
  `avg_rating` + `rating_count`. Aggregates honor existing filters.
- Daily snapshot (`agent/loop.py::_compute_daily_analytics`) LEFT JOINs feedback
  for per-machine rating; `insert_analytics_snapshot` accepts the new kwargs.
- Date-range filter fix: aggregate helpers use `date(f.created_at) BETWEEN
  date(?) AND date(?)` so same-day timestamps aren't excluded.

**Discord:**
- `bot/cogs/dm.py`: new `RatingView` (`timeout=600`, 5 star buttons + Skip)
  and `FeedbackModal` (one optional paragraph TextInput, max 500 chars).
- `send_rating_dm` invoked once on the user-acknowledged completion path
  (`intent="done"` + `status="serving"` in `_do_action`); not from
  agent-driven or staff-completed paths.
- `FeedbackAlreadyExistsError` → ephemeral "already submitted"; missing entry
  → "Visit no longer found".

**Frontend:**
- Types: `FeedbackRow`, `avg_rating`/`rating_count` on `AnalyticsSummary`,
  `MachineStat`, `CollegeStat`.
- `listFeedback` admin client.
- `/admin/feedback` page (staff-only): filter by machine/college/rating,
  paginated list of joined rows with full attribution.
- Analytics dashboard: 5th KPI "Avg Rating ★ X.X", new "Rating" column in
  `MachineTable`, per-row accents on `MachineUtilization` and
  `CollegeUtilization` charts.

**Auth & scope:** GET `/api/feedback/` is staff-gated (`require_staff`); no write
endpoints exist. Rating DMs only fire on the user-acknowledged completion path.

**Docs:**
- Design: `docs/plans/2026-04-27-feedback-form-design.md`.
- Plan: `docs/plans/2026-04-27-feedback-form.md`.

**Commits:** Task 1 `93fe832`, Task 2 `e72ddc0`, Task 3 `39975bf`, Task 4 `1ec13e2`,
Task 5 `3eaf0a8`, Task 6 `0518689`, Task 7 `1eed447`, Task 8 `1231ce0`,
Task 9 `1da4ccd`.

## 2026-04-26 — College Signup + Analytics-by-College
Shipped on `feat/customizable-admin`. 200 tests passing, tsc clean.

**Backend:**
- New `colleges` table (id, name, archived_at) + partial unique index `idx_colleges_name_active` on `(name) WHERE archived_at IS NULL` (created in `_migrate` post-CREATE TABLE per learnings.md 2026-04-22).
- `users.college_id INTEGER REFERENCES colleges(id)` replaces freeform `users.college` (DROPped via SQLite ≥3.35 ALTER).
- `_seed_colleges` idempotently inserts the 15 standard UIUC colleges; called from `init_db` after `_seed_machines`.
- Re-signup migration: `UPDATE users SET registered=0 WHERE registered=1` runs once in `_migrate` so existing users re-pick a college on next Join Queue press. `full_name`/`email`/`major`/`graduation_year` preserved for prefill.
- `db/models.py`: create/list_active/list_all/get/update/archive/restore/purge_college, count_users_in_college; `DuplicateCollegeError` + `CollegeInUseError`.
- `api/routes/colleges.py`: public GET active, staff GET include_archived (single handler with conditional bearer check), admin POST/PATCH/DELETE/restore/purge with `confirm_name` body.
- `compute_analytics_response` accepts `college_id` filter and always returns a `colleges: list[CollegeStat]` block ("Unspecified" bucket aggregates `college_id IS NULL`). When `college_id` is set, summary/machines/daily_breakdown are computed live (snapshots have no per-user dim).
- `bot/cogs/admin.py` `/profile` modal updated: dropped freeform `college` TextInput, switched to `update_user_profile(college_id=...)` preserving the user's existing `college_id`.

**Discord:**
- New `CollegeSelectView` (ephemeral, `timeout=120`) shown before the modal. `_CollegeSelect` subclass with writable `values` for tests. Builds options from `list_active_colleges()`, capped at 25.
- `SignupModal` drops the `college` `TextInput`; takes `college_id` + `prefill` kwargs. Prefill populates `TextInput.default` for re-signup.
- `_handle_join` registration branch sends the select view ephemerally; empty colleges list → "Sign-ups temporarily unavailable" message.

**Frontend:**
- `/admin/colleges` page (admin-only) mirrors `/admin/machines`: add form, inline-rename table, archive/restore, red purge button with name-retype modal.
- Analytics dashboard: college dropdown filter (from `listColleges()`), violet active filter chip, new `CollegeUtilization` bar-chart card paired with PeakHours.
- `useAnalytics` hook bypasses the today-stats merge when a college filter is active (the `/today` endpoint has no college dim — acceptable trade-off).
- `web/src/api/types.ts` exports `CollegeSummary`, `AdminCollege`, `CollegeStat`. `web/src/api/admin.ts` has `listAllColleges`/`createCollege`/`patchCollege`/`archiveCollege`/`restoreCollege`/`purgeCollege`. `web/src/api/client.ts` exports public `listColleges`.

**Auth & scope:** All college mutations require admin (`Depends(require_admin)`); GET `?include_archived=true` requires staff. Public GET strips `archived_at` from the response.

**Docs:**
- Design: `docs/plans/2026-04-26-college-signup-design.md`.
- Plan: `docs/plans/2026-04-26-college-signup.md`.

**Commits:** Task 1 `987b9f5`, Task 2 `ba976be`, Task 3 `5955c2c`, Task 4 `f844591`, admin /profile fix `72bd82a`, Task 5 `67e9601`, Task 6 `a8525bc`, Task 7 `ec063bf`, Task 8 `e26e1af`, Task 9 `6a34aa3`.

## 2026-04-26 — Analytics Chatbot
Shipped on `feat/customizable-admin`. 162 tests passing, tsc clean.

**Backend:**
- New tables `chat_conversations` (id, staff_user_id FK, title, timestamps) and `chat_messages` (id, conversation_id FK ON DELETE CASCADE, role CHECK, content, tool_call_id, tool_calls_json — last two are scaffolding for v2 tool use). Index `idx_chat_msgs_conv` on `(conversation_id, id)`.
- `db/models.py` chat helpers: `create_conversation`, `list_conversations`, `get_conversation`, `get_conversation_messages` (returns None for non-owners — distinct from "empty"), `get_recent_messages` (last N oldest-first), `append_message` (bumps `updated_at`), `delete_conversation` (returns bool).
- `api/routes/analytics.py` refactored: extracted `compute_analytics_response(period, start, end, machine_id=None)` so chat and dashboard GETs share one source of truth.
- `api/routes/chat.py` (new): `POST /api/analytics/chat`, `GET /chat/conversations`, `GET /chat/conversations/{id}`, `DELETE /chat/conversations/{id}`. All gated by `require_staff`. OpenAI `gpt-4o-mini`, `_make_openai_client()` lazy factory (mirrors `agent/loop.py::_generate_ai_summary` — missing key returns 503 instead of crashing). System prompt embeds the analytics blob; conversation history capped at last 8 messages. Oversized blob → drop `daily_breakdown` → drop per-machine `ai_summary` → 413.

**Frontend:**
- `ChatMessage`/`ChatConversationSummary`/`ChatConversationDetail`/`ChatPostRequest`/`ChatPostResponse` types in `web/src/api/types.ts`. `postChat` / `listChatConversations` / `getChatConversation` / `deleteChatConversation` in `web/src/api/client.ts`.
- New dep `react-markdown` for assistant replies.
- New `web/src/components/analytics/AnalyticsChat.tsx`: floating "Ask the data" pill (bottom-right) → 380×560 panel. Conversation list (☰), New chat (+), suggested prompts on empty state, optimistic user bubble, three-dot pulse while waiting, scoped-to-period header. Mounted in `web/src/pages/Analytics.tsx`.

**Auth & scope:** Conversations strictly per-`staff_user_id`. Other-owner reads/deletes return 404 (not 403) to avoid leaking existence.

**Docs:**
- Design: `docs/plans/2026-04-26-analytics-chatbot-design.md`.
- Plan: `docs/plans/2026-04-26-analytics-chatbot.md` (8 tasks, all done).

## 2026-04-22 — Multi-Unit Machines
Shipped on `feat/customizable-admin`. 138 tests passing, tsc clean.

**Backend:**
- New `machine_units` table (`id, machine_id, label, status, created_at, archived_at`). Partial unique index `idx_machine_units_label_active` on `(machine_id, label) WHERE archived_at IS NULL` — created in `_migrate` post-ALTER per the partial-index rule.
- `queue_entries.unit_id INTEGER REFERENCES machine_units(id)` (nullable; populated only when `status='serving'`).
- Every existing/seeded machine backfilled with one "Main" unit via `_backfill_main_units` (called from `_migrate` for upgrades and from `init_db` after `_seed_machines` for fresh DBs). `create_machine` also seeds a "Main" unit in the same transaction; `purge_machine` now cascades `machine_units` rows too.
- `db/models.py`: `list_units`, `get_unit`, `create_unit`, `update_unit`, `archive_unit`, `restore_unit`, `purge_unit`, plus capacity helpers `count_active_units`, `count_serving_on_machine`, `first_available_unit`. Archive/purge blocked when a unit has an active serving entry.
- `agent/loop.py::_process_machines`: promotes up to `count_active_units(mid)` simultaneously, auto-assigning the first free active unit. DM copy includes unit label unless it's "Main".
- `api/routes/units.py`: nested CRUD under `/api/machines/{mid}/units/` — public GET, staff POST/PATCH, admin DELETE/restore/purge. `/api/machines/` + `/api/queue/` responses now embed a `units` summary array; `QueueEntryOut` exposes `unit_id`.

**Frontend:**
- `AdminUnit` / `UnitSummary` types + full unit admin API client in `web/src/api/admin.ts`.
- `/admin/machines`: expandable chevron per row reveals a nested units section (add, rename, toggle active↔maintenance, archive, purge with label-retype modal). Unit section hidden on archived machines.
- Public queue: each `MachineColumn` renders a chip strip above the pause button — green = available, blue = in-use (with display name), gray = maintenance. Chip strip hidden for single-"Main" machines (back-compat).

**Docs:**
- Design: `docs/plans/2026-04-22-multi-unit-machines-design.md`.
- Plan: `docs/plans/2026-04-22-multi-unit-machines.md` (11 tasks, all done).

## 2026-04-22 — Customizable Admin (machines, staff, settings)
Shipped on `feat/customizable-admin` (14 commits ahead of `main`). All 109 tests pass, tsc clean, backend/Vite restarted and smoke-tested.

**Backend:**
- Migration: `archived_at` on `machines`, `role` on `staff_users` (with "last admin backfill" invariant), new `settings` table with 6 seeded keys. Partial unique index `idx_machines_slug_active` enforces slug uniqueness only among non-archived rows (created in `_migrate`, not `_create_tables`).
- `api/settings_store.py`: PBKDF2-free `get_setting[_int|_bool]` + `set_setting` with 10-second TTL cache. Agent reads `reminder_minutes` / `grace_minutes` through it so admin edits take effect within 10s without restart.
- Routes:
  - `POST/PATCH /api/machines/{id}` (staff), `DELETE` + `POST .../restore` (admin). Hard-delete = `DELETE ?purge=true` with `{confirm_slug}` body; cascades queue_entries + analytics_snapshots.
  - `GET/POST/PATCH/DELETE /api/staff/` (admin). Last-admin guard on DELETE and role-change PATCH.
  - `GET/PATCH /api/settings/` (admin) + `GET /api/public-settings/` (public, returns only `public_mode` + `maintenance_banner`).
- Bot: new `create_queue_embed` / `delete_queue_embed` methods; `on_ready` reconciles archived machines by deleting lingering embeds. API bridges `notify_embed_create` / `notify_embed_delete` added to `api/deps.py`.

**Frontend:**
- `AuthContext` now tracks `role`. `RequireAdmin` wraps `/admin/staff` and `/admin/settings`.
- New pages: `/admin/machines` (table + add form + archive + restore + red destructive purge modal with slug retype), `/admin/staff` (CRUD + reset-password modal), `/admin/settings` (grouped form with dirty-state Save button).
- `MaintenanceBanner` polls `/api/public-settings/` every 60s and renders a yellow strip when non-empty.
- NavBar shows Admin link only when signed in; sub-tabs (Machines / Staff / Settings) surface when in `/admin/*`; admin-only tabs hidden for regular staff.

**Defaults & conventions:**
- Seeded admin: `admin` / `changeme` (override via `STAFF_USERNAME` / `STAFF_PASSWORD` / `AUTH_SECRET`).
- Slug validation regex: `^[a-z0-9]+(-[a-z0-9]+)*$`.
- Archive blocked while active queue entries exist (409 with message).
- Purge requires typed slug confirmation (400 on mismatch).

**Docs:**
- Design: `docs/plans/2026-04-22-customizable-admin-design.md`.
- Plan: `docs/plans/2026-04-22-customizable-admin.md` (16 tasks, all done).
