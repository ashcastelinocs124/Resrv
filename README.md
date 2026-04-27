# Resrv

Custom queue management system for the SCD (Student Creative Design) facility at the University of Illinois. Replaces Waitwhile with an autonomous, Discord-native queue with a staff web panel, AI analytics, and a structured feedback loop.

## Why

The existing system (Waitwhile) is expensive per-queue, manually operated, and progressively paywalls analytics and branding. Staff are forced to juggle queue management, user training, and safety monitoring simultaneously. Time-based reservations were tried and abandoned — users either overrun or leave machines idle.

Resrv eliminates the manual queue work with an autonomous FIFO agent, gives staff a full admin web panel with override controls, and feeds usage data into both a charting dashboard and a multi-turn analytics chatbot.

## Features

### For students (Discord)
- **One-button queue join** — pinned per-machine embeds with **Join Queue / Check Position / Leave Queue**.
- **Two-step signup** — first time joining: pick your UIUC college from a dropdown, then fill a short modal (name, `@illinois.edu` email, major, expected graduation year). Re-signup prefills prior values.
- **Natural-language DMs** — DM the bot ("I'm done", "more time", etc.); an OpenAI classifier routes the intent to the right action, with button fallback if classification fails.
- **Smart Leave Queue** — leaving while currently serving asks: **Finish early** (treated as a real completion, triggers rating prompt) or **Cancel session** (no rating).
- **Post-visit rating** — after a completed visit, the bot DMs a 1-5 star rating prompt with an optional free-text comment. One feedback per visit.
- **30-min reminders + grace expiry** — agent DMs you "Still using it?" while serving; auto-completes if you don't reply within the configured grace window.

### For staff (web panel + Discord slash commands)
- **Public queue view** — `/` shows all machines, live queue states, and unit chips for multi-unit machines.
- **`/admin/machines`** — CRUD machines (name, slug, status). Soft-delete with archive/restore; hard-delete (purge) requires retyping the slug. Cascades through `queue_entries`, `machine_units`, `analytics_snapshots`.
- **Per-machine units** — every machine can have N units (e.g. two laser cutters as Unit A / Unit B); the agent promotes up to `count_active_units(machine)` users in parallel. Each machine starts with one "Main" unit.
- **`/admin/staff`** — admin-only CRUD for staff users. Last-admin guard prevents demoting the only admin.
- **`/admin/colleges`** — admin-managed UIUC college list (15 seeded). Used by the signup picker and analytics grouping. Same archive/restore/purge pattern (purge requires retyping the name; blocked when users reference the row).
- **`/admin/settings`** — admin-only key/value form for runtime config (`reminder_minutes`, `grace_minutes`, `public_mode`, `maintenance_banner`, etc.). Settings have a 10s TTL cache so changes apply without restart.
- **`/admin/feedback`** — staff-readable list of all submitted ratings, filterable by machine, college, and rating bucket (Any / 1-5 / Below 3 / Below 4). Shows full attribution (`full_name (college)`), star rating, and comment.
- **`/admin/analytics`** — period filter (Day / Week / Month), KPI tiles (incl. avg rating), per-machine and per-college bar charts, peak-hours chart, attendance breakdown, machine table, and an AI-generated written summary. Filter by college and click bars to drill down.
- **Analytics chatbot** — floating "Ask the data" panel mounted on `/admin/analytics`. Multi-turn, SSE-streamed, scoped to the signed-in staff user. Server-side model allowlist (`gpt-5.4`, `gpt-5.4-mini`, `gpt-4o`); selection persists per browser. The chatbot's system prompt embeds the same JSON the dashboard renders, so its answers can't drift from the visible data.
- **CSV / PDF export** — two buttons in the Analytics header download a single CSV (Summary / Machines / Colleges sections) or a one-page PDF (titled tables) honoring the currently selected filters.
- **Slash commands** (admin channel only) — `/bump @user`, `/remove @user`, `/skip @user`, `/pause <machine>`, `/status`, `/profile` (per-user profile editor).
- **Maintenance banner** — yellow strip across the public queue page driven by a single setting; polled every 60s.

### Behind the scenes
- **Autonomous FIFO agent** — single tick loop (`agent/loop.py`) advancing entries waiting → serving → completed/no-show, sending reminders, expiring graces, computing daily snapshots.
- **Daily snapshots** — per-machine rollup (jobs, completion rate, wait/serve times, peak hour, AI summary, **avg rating + count**) written to `analytics_snapshots`. Live JOIN to `feedback` adds rating fields.
- **Stdlib-only auth** — PBKDF2 password hashing + HMAC-signed Bearer tokens (no JWT/bcrypt deps). `require_staff` / `require_admin` FastAPI dependencies.
- **Lazy OpenAI clients** — every OpenAI integration uses a `_make_openai_client()` factory, so a missing key degrades to graceful 503 / "unknown intent" rather than crashing the bot.
- **Soft-delete via partial unique indexes** — every archivable table (`machines`, `colleges`, `machine_units`) uses `archived_at` + `CREATE UNIQUE INDEX ... WHERE archived_at IS NULL` so admins can rename and reuse slugs/labels after archive.
- **Cross-user access returns 404, not 403** — hides the existence of resources owned by other staff users (chat conversations, etc.).

## Architecture

Single Python monolith on a small VPS:

```
┌─────────────────────────────────────────────────┐
│                Python Monolith                  │
│                                                 │
│  ┌──────────────┐       ┌────────────────────┐ │
│  │  Discord Bot │       │  FastAPI Server     │ │
│  │  discord.py  │       │  (JSON API + SSE)   │ │
│  └──────┬───────┘       └────────┬───────────┘ │
│         │                        │              │
│  ┌──────┴────────────────────────┴───────────┐ │
│  │           Queue Agent (FIFO)              │ │
│  │      Background task loop (~10s tick)     │ │
│  └──────────────────┬────────────────────────┘ │
│                     │                           │
│  ┌──────────────────┴────────────────────────┐ │
│  │             SQLite (WAL mode)             │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │  OpenAI integrations                      │ │
│  │  - DM intent classifier                   │ │
│  │  - Daily AI summary generator             │ │
│  │  - Multi-turn analytics chatbot (SSE)     │ │
│  └───────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Discord bot | `discord.py >= 2.3` |
| API server | FastAPI |
| Database | SQLite (WAL mode) via `aiosqlite` |
| Frontend | React + Vite + Tailwind CSS |
| Charts | Recharts |
| Markdown rendering | `react-markdown` (chat replies) |
| Streaming | Server-Sent Events (fetch + ReadableStream on the client; carries Bearer auth) |
| AI | OpenAI API (intent classification, daily summaries, multi-turn chat) |
| PDF export | `fpdf2` |
| Auth | Stdlib-only (PBKDF2 + HMAC tokens, stored in `localStorage` as `reserv.auth.token`) |
| Hosting | VPS (DigitalOcean / Hetzner) |

## Queue Flow

```
User clicks [Join Queue] on Discord embed
       │
       ▼
  ┌─ registered? ─┐
  │ no             │ yes
  ▼                ▼
StringSelect      Added to queue
of UIUC          (DM confirms position)
colleges
  │
  ▼
Modal (name, email, major, grad year)
  │
  ▼
Added to queue
       │
       ▼
Agent auto-serves next user when a unit is free
       │
       ▼
Bot DMs: "You're up! Head to [machine] (Unit A)"
       │
       ▼
30-min reminder: "Still using it?"
       │
       ├─ "More time"  → reset reminder
       ├─ "I'm done"   → status='completed', job_successful=1
       │   └─ Rating DM (1-5★ + optional comment modal)
       ├─ "Leave Queue" while serving:
       │   ├─ Finish early  → completed → rating DM
       │   └─ Cancel session → cancelled, no rating
       └─ no response (grace minutes) → auto no_show
```

## Machines (default seed)

- Large Format Printer
- Laser Cutter
- CNC Router
- Water Jet
- 3D Printer
- Sewing Machine

Each starts with one "Main" unit; staff can add/rename/archive units per machine via the admin panel.

## Colleges (default seed)

15 standard UIUC colleges (Grainger, Gies, LAS, ACES, Education, Fine and Applied Arts, Media, iSchool, Applied Health Sciences, DGS, Social Work, LER, Carle Illinois Med, Vet Med, Law). Admin can edit/archive/add via `/admin/colleges`.

## Slash Commands (Discord)

| Command | Audience | Description |
|---------|----------|-------------|
| `/profile` | everyone | View / edit your saved profile (full name, email, major, grad year). |
| `/bump @user` | staff | Move user to top of queue. |
| `/remove @user` | staff | Remove from queue. |
| `/skip @user` | staff | Mark as no-show, advance. |
| `/pause <machine>` | staff | Toggle paused state for a machine. |
| `/status` | staff | Print a status summary in the channel. |

Slash commands are gated to the configured admin channel.

## Project Structure

```
Reserv/
├── bot/                  # Discord bot (discord.py)
│   ├── bot.py            # ReservBot — setup_hook, on_ready, embed mgmt
│   ├── embeds.py         # QueueButtonView + colour-coded machine embeds
│   └── cogs/
│       ├── queue.py      # Join / Check / Leave + signup picker + leave-flow split
│       ├── dm.py         # NL classifier + DM ack flow + rating prompt
│       └── admin.py      # Slash commands + /profile modal
├── api/                  # FastAPI backend
│   ├── main.py           # App + routers + CORS + lifespan
│   ├── auth.py           # PBKDF2 + HMAC tokens, require_staff / require_admin
│   ├── settings_store.py # TTL-cached runtime settings
│   └── routes/
│       ├── queue.py
│       ├── machines.py
│       ├── units.py
│       ├── staff.py
│       ├── settings.py
│       ├── colleges.py
│       ├── feedback.py
│       ├── analytics.py  # /summary, /today, /export (CSV+PDF), /chat (SSE)
│       └── auth.py
├── agent/
│   └── loop.py           # FIFO tick loop; daily snapshots; AI summaries
├── db/
│   ├── database.py       # init_db, _create_tables, _migrate, seeds
│   └── models.py         # all CRUD + aggregate helpers (no ORM)
├── web/                  # React frontend (Vite + Tailwind)
│   └── src/
│       ├── pages/        # Public queue, Login, Analytics, /admin/*
│       │   └── admin/    # Machines, Staff, Settings, Colleges, Feedback
│       ├── components/   # NavBar, MaintenanceBanner, analytics widgets
│       ├── hooks/        # useAnalytics, useAuth, etc.
│       └── api/          # client.ts, admin.ts, types.ts
├── tests/                # 240+ pytest tests (DB, API, agent, bot, chat)
├── docs/plans/           # Design docs + bite-sized implementation plans
├── main.py               # Entrypoint (bot main loop + uvicorn daemon thread)
├── config.py             # Pydantic Settings (env-based)
└── requirements.txt
```

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
  - Enable the **MESSAGE CONTENT** intent (DM cog needs it)
  - Add the bot to your server with `applications.commands` + `bot` scopes
- An OpenAI API key (optional — without it, intent classification falls back to button picker, daily summaries are skipped, and the chatbot returns 503)

### Installation

```bash
git clone https://github.com/Agentic-AI-UIUC/Resrv.git
cd Resrv

# Backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Frontend
cd web
npm install
npm run build      # production build
# OR `npm run dev` for hot reload during development
cd ..

# Configure environment
cp .env.example .env  # if .env.example exists; otherwise create .env
# Edit .env with your Discord token, OpenAI key, etc. (see table below)

# Run
python main.py
```

The bot owns the main loop; FastAPI runs in a daemon thread on port 8000. Vite (in dev) serves on 5173 and proxies `/api` to the backend.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | — | Bot token (required). |
| `DISCORD_GUILD_ID` | — | Guild to sync slash commands to. |
| `QUEUE_CHANNEL_ID` | — | Channel for pinned per-machine embeds. |
| `ADMIN_CHANNEL_ID` | — | Channel where staff slash commands are accepted. |
| `OPENAI_API_KEY` | — | Optional. Without it, AI features no-op gracefully. |
| `STAFF_USERNAME` | `admin` | Seeded admin username (only used on first DB init). |
| `STAFF_PASSWORD` | `changeme` | Seeded admin password. **Change in production.** |
| `AUTH_SECRET` | random-per-process | HMAC secret for token signing. Set in prod so tokens survive restart. |
| `DATABASE_PATH` | `./reserv.db` | SQLite file path. |
| `REMINDER_MINUTES` | `30` | Per-machine "still using it?" reminder window. |
| `GRACE_MINUTES` | `10` | Grace before auto no-show after no reminder reply. |
| `AGENT_TICK_SECONDS` | `10` | Queue agent loop interval. |
| `QUEUE_RESET_HOUR` | `0` | Daily UTC hour to clear the queues. |

Most runtime knobs (reminder window, grace, public_mode, banner) can be edited live from `/admin/settings` — those changes take effect within 10 seconds without a restart.

## API Surface (high level)

All endpoints under `/api/`. Auth-required endpoints expect `Authorization: Bearer <token>` (obtain via `POST /api/auth/login`).

| Group | Public | Staff | Admin |
|-------|--------|-------|-------|
| Queue | `GET /queue/*` | `POST /queue/{id}/serve\|complete\|leave\|bump` | — |
| Machines | `GET /machines/` | `PATCH /machines/{id}/status` | full CRUD + archive/restore/purge |
| Units | `GET` (embedded in machines) | `POST/PATCH /machines/{id}/units/...` | `DELETE` + restore/purge |
| Staff | — | — | `GET/POST/PATCH/DELETE /staff/` |
| Settings | `GET /public-settings/` | — | `GET/PATCH /settings/` |
| Colleges | `GET /colleges/` | `GET ?include_archived=true` | `POST/PATCH/DELETE` + restore/purge |
| Feedback | — | `GET /feedback/` (filterable) | — |
| Analytics | — | `GET /analytics/{summary,today,/{machine_id}, export}` | — |
| Chat | — | `POST /analytics/chat` (JSON), `POST /analytics/chat/stream` (SSE), `GET /analytics/chat/conversations` | — |

## Testing

```bash
pytest tests/                      # 240+ tests across DB, API, agent, bot, chat
cd web && npx tsc -b               # TypeScript build (no emit) — exit 0 means clean
```

The test suite uses an in-memory SQLite DB per test (`db` fixture in `tests/conftest.py`) and `httpx.AsyncClient` against the FastAPI app via `ASGITransport`. Discord interactions are mocked via `unittest.mock.MagicMock` + `AsyncMock`.

## Cost

| Item | Monthly |
|------|---------|
| VPS (DigitalOcean / Hetzner) | ~$5-6 |
| OpenAI API (analytics + chatbot) | ~$5-15 |
| **Total** | **~$10-21** |

## License

MIT
