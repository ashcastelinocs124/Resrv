# Reserv — Queue Management System

Custom queue management system for the SCD facility at the University of Illinois, replacing Waitwhile.

## Architecture

- **Monolith:** Single Python process (discord.py + FastAPI + background agent)
- **Database:** SQLite (WAL mode)
- **Frontend:** React + Vite + Tailwind CSS; `react-markdown` for assistant chat replies
- **Hosting:** Small VPS (DigitalOcean/Hetzner)
- **AI:** OpenAI API — daily analytics summaries, DM intent classification, and the multi-turn analytics chatbot (SSE-streamed). Lazy-instantiated `AsyncOpenAI` everywhere so a missing key degrades to 503 instead of crashing.
- **Auth:** Stdlib-only staff auth (PBKDF2 password hashing + HMAC-signed Bearer tokens, no JWT/bcrypt deps). `require_staff` / `require_admin` FastAPI dependencies; tokens stored client-side in `localStorage` under `reserv.auth.token`.
- **Current scale (2026-04-26):** 6 seeded machines, 172 tests, ~25 API routes across queue / machines / units / staff / settings / analytics / chat.

## Key Conventions

- Discord bot is user-facing; web panel is staff-facing
- Queue agent is FIFO, autonomous, with manual override capability
- No time-based reservations — pure queue
- Illinois email verification required (toggleable for public events)
- Non-sensitive data only (no UIN)
- **Soft-delete pattern:** any table that can be archived uses `archived_at TEXT` + a partial unique index `WHERE archived_at IS NULL`. Column-level UNIQUE blocks slug/label reuse after archive — always reach for the partial index instead.
- **Cross-user access returns 404, not 403** — avoids leaking the existence of resources owned by other staff users (chat conversations follow this rule).
- **AI model selection always goes through a server-side allowlist** (e.g. `ALLOWED_MODELS` in `api/routes/chat.py`). Frontend dropdowns are driven by a `GET …/models` route so the UI can't drift from what the server permits.
- **Display rank ≠ persisted position.** `queue_entries.position` is a join-time stamp; UI components rank waiting entries from the filtered list at render time, never from the raw column.

## Learnings

This project maintains a `learnings.md` file at the project root. Add entries whenever you:
- Fix a non-obvious bug (include root cause)
- Discover a library/API gotcha or version-specific quirk
- Make an architectural decision worth remembering
- Find a useful command, config, or file path that wasn't obvious

Use the `/capture-learnings` skill at the end of sessions to do this automatically.

## Memory

This project maintains a `memory.md` file at the project root. Use it to store persistent context that should survive across sessions:
- Current state of the codebase (what's built, what's in progress)
- Key architectural decisions and the reasoning behind them
- Patterns and conventions established for this project
- Gotchas, known issues, and workarounds

Update `memory.md` whenever something significant changes. Read it at the start of each session before doing anything else.

## Completed Work

### 2026-04-01 — MVP Core Queue System
- Built complete Discord bot + FastAPI API + autonomous queue agent + SQLite persistence
- 4 seeded machines (Large Format Printer, Laser Cutter, CNC Router, Water Jet)
- Bot: persistent button embeds, Join/Check/Leave interactions, staff slash commands (/bump, /remove, /skip, /pause, /status)
- Agent: 10s tick loop with FIFO advancement, 30-min reminders, grace period expiry, daily reset
- API: 7 queue endpoints + 3 machine endpoints + health check
- 51 tests passing across DB, API, and agent layers
- Deferred: email verification, AI analytics, React dashboard, WebSocket real-time

### 2026-04-26 — Analytics Chatbot
- New `chat_conversations` + `chat_messages` tables (FK + `ON DELETE CASCADE`, role CHECK constraint, scaffolding columns for future tool-use).
- Per-staff scoped multi-turn chat at `/api/analytics/chat` (POST + list/get/delete) gated by `require_staff`. Lazy-instantiated OpenAI client (degrades to 503 if key missing).
- System prompt embeds the same `compute_analytics_response` payload the dashboard renders, so chat answers can never diverge from the visible data. Last 8 messages reach the model; the full thread persists.
- Floating "Ask the data" panel mounted on `/admin/analytics`: conversation list, suggested prompts, optimistic UI, markdown-rendered assistant replies via `react-markdown`. Panel sized at min(560,viewport-3rem) × 80vh.
- SSE streaming via `POST /api/analytics/chat/stream` (fetch + ReadableStream because EventSource can't carry the Bearer token). Frontend renders deltas live, then re-fetches the canonical thread on `done`.
- Server-side model allowlist (`ALLOWED_MODELS` in `api/routes/chat.py`) — currently `gpt-5.4`, `gpt-5.4-mini` (default), `gpt-4o`. Frontend dropdown driven by `GET /chat/models`; selection persists to `localStorage` under `reserv.chat.model`. Unknown model strings → 400.
- 17 chat tests added (DB + non-streaming API + streaming SSE + cross-user isolation + model allowlist).

### 2026-04-26 — Queue display rank fix (web)
- `queue_entries.position` is a join-time stamp and was leaking into the public web card as a stale `#3` after earlier entries finished. Discord embed already renumbered.
- Fixed by computing display rank in `MachineColumn` (filter waiting + index) and passing `displayPosition` to `QueueCard`. Serving entries now render "serving" instead of a number. No DB writes per mutation.

### 2026-04-26 — College Signup (UIUC picker) + Analytics-by-College
- Replaced freeform `college` text field with admin-managed `colleges` table + FK on users. 15 standard UIUC colleges seeded; admin can add/rename/archive/purge via new `/admin/colleges` page (admin-only, mirrors machines page).
- Discord signup is now a two-step flow: ephemeral `StringSelect` view (`CollegeSelectView`, `timeout=120`) → 4-input `SignupModal` with college_id baked in. Existing registered users have `registered` flipped to 0 in `_migrate` so they re-pick on next Join Queue (modal prefilled with their prior name/email/major/grad year).
- Analytics dashboard + chatbot now group/filter by college. `compute_analytics_response` accepts `college_id`; the response always contains a `colleges` block with an "Unspecified" bucket for users with `college_id IS NULL`. Frontend gets a college filter dropdown, active filter chip, and a "By College" bar chart card.
- Conventions: `confirm_name` retype required for purge (mirrors `confirm_slug`); soft-delete via `archived_at` + partial unique index `idx_colleges_name_active`; `DuplicateCollegeError` (409) + `CollegeInUseError` (409) raised by helpers; admin /profile modal drops the college field (users re-pick via Join Queue).

### 2026-04-27 — Post-Visit Feedback (Discord rating + analytics rollup)
- After acknowledging a completion ("Did your job succeed? Yes/No"), the user gets a follow-up DM with a 5-star rating view; clicking a star opens an optional comment modal. One feedback per visit (UNIQUE on `queue_entry_id`); cascades on entry delete.
- New `feedback` table; `analytics_snapshots` gains `avg_rating` + `rating_count`. `compute_analytics_response` merges feedback aggregates into the summary, machines, and colleges blocks; chatbot picks up the dimension automatically.
- New `/admin/feedback` page lists recent ratings with full attribution (`full_name`, college, machine), filterable by machine / college / rating. Staff-readable, no admin write surface.
- Conventions: `FeedbackAlreadyExistsError` (modal-side ephemeral), `★ x.x (n)` accents on machine/college analytics cards, daily snapshot now includes feedback aggregates, `send_rating_dm` only fires on user-acknowledged completions.

### 2026-04-29 — Illinois Email Verification
- Strict SMTP-backed (Gmail-default) verification gate: 6-digit code over `aiosmtplib` STARTTLS:587, entered via Discord `VerificationModal` after `SignupModal`. `users.verified=1` is sticky — verified users skip the gate on every future join. `public_mode=true` is the admin escape hatch.
- New service module `bot/email_verification.py` with lazy SMTP factory (graceful degrade when creds missing — same pattern as the OpenAI client). `issue_code` invalidates prior unused codes per `discord_id`; `verify_code` locks the row after `MAX_WRONG_ATTEMPTS=5` wrong submissions; `VerificationRateLimitError` after 5 codes/hour.
- Schema: `users.verified_at TEXT NULL`, `verification_codes.attempts INTEGER NOT NULL DEFAULT 0` — both additive in `_migrate`. Reused the previously-dormant `verification_codes` table.
- Convention: `register_user` is **deferred** to `VerificationModal.on_submit` on the unverified path so abandoned signups don't leave registered ghost rows. `_join_and_dm` extracted as a module-level helper to share the live-rank join + DM logic across SignupModal (fast path), VerificationModal, and `_handle_join`.
- 10 new tests (1 schema + 7 service + 3 flow); full suite at 305 PASS, `npx tsc -b` clean (no frontend changes).

### 2026-04-27 — Self-Service Staff Tooling
- Separate data-analyst agent (`/api/analytics/agent`) with OpenAI tool-calling: 6 tools (`query_jobs`, `query_feedback`, `query_funnel`, `top_n`, `compare_periods`, `make_chart`), 4 round-trip cap (forced fallback with `tool_choice="none"`), 1000-row tool cap, SSE protocol extended with `tool_call` + `chart` events. Gated by `data_analyst_enabled` + `data_analyst_visible_to_staff` settings; `require_data_analyst` dependency returns 503/403/pass.
- New "Custom charts" section on `/admin/analytics` renders pinned charts (`/api/pinned-charts` CRUD + refresh re-runs the saved `query_jobs` context, unpin removes). `<ChartFromSpec>` dispatches bar/line/pie/table to Recharts.
- First-login guided tour using `driver.js` (11 steps with `requiresAdmin` skip-list). `staff_users.onboarded_at` gates auto-run via `<OnboardingGate>`; "Replay tour" in NavBar re-runs without re-stamping.
- New tables: `agent_conversations`, `agent_messages`, `pinned_charts`. New endpoint groups: `/api/me/features`, `/api/auth/me/onboarded`, `/api/analytics/agent/*`, `/api/pinned-charts/*`. Admin Settings page gains the two flags.
- Conventions: settings cache (`api/settings_store._cache`) is module-level — `tests/conftest.py` now invalidates it per test. Inner OpenAI calls in the agent loop are non-streaming; SSE frames *loop progress* (meta → tool_call* → chart? → delta → done).

### 2026-04-22 — Multi-Unit Machines
- New `machine_units` table; every existing machine backfilled with one "Main" unit. `queue_entries.unit_id` stamped on promotion.
- Agent now promotes up to `count_active_units(machine)` in parallel; auto-assigns the first active unit without a live serving entry. Maintenance units exclude themselves from capacity.
- Nested CRUD routes under `/api/machines/{id}/units/` (staff create/patch, admin archive/restore/purge). Duplicate labels return 409 via partial unique index `idx_machine_units_label_active`.
- Discord embed gains a Units block (icons for available / serving / maintenance); hidden when a machine has only a single "Main" unit to preserve the single-unit UX.
- Admin page has expandable per-machine units section (add/rename/toggle/archive/purge with label-retype modal); public queue shows a chip strip per machine.
