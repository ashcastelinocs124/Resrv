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

### 2026-04-22 — Multi-Unit Machines
- New `machine_units` table; every existing machine backfilled with one "Main" unit. `queue_entries.unit_id` stamped on promotion.
- Agent now promotes up to `count_active_units(machine)` in parallel; auto-assigns the first active unit without a live serving entry. Maintenance units exclude themselves from capacity.
- Nested CRUD routes under `/api/machines/{id}/units/` (staff create/patch, admin archive/restore/purge). Duplicate labels return 409 via partial unique index `idx_machine_units_label_active`.
- Discord embed gains a Units block (icons for available / serving / maintenance); hidden when a machine has only a single "Main" unit to preserve the single-unit UX.
- Admin page has expandable per-machine units section (add/rename/toggle/archive/purge with label-retype modal); public queue shows a chip strip per machine.
