# Self-Service Staff Tooling — Design

**Date:** 2026-04-27
**Branch:** `feat/customizable-admin` (or a fresh feature branch off `main`)
**Status:** Approved (sections 1-5)

## Goal

Once SCD has the software in their hands, vendor involvement should drop to ~zero. Two new capabilities support that:

1. **Data-analyst agent** — a separate AI panel that builds charts on demand via tool-calling. Admin-gated: a master switch + visibility toggle decide who can see it.
2. **Onboarding tour** — first-login guided walkthrough so new staff ramp up without us writing or sending docs.

Plus: pinned custom charts the agent emits get rendered in a new "Custom charts" section on the analytics page, persisting across reloads.

The existing analytics dashboard, chatbot, and admin pages stay unchanged. This work is purely additive.

## Decisions (from brainstorming)

| # | Decision |
|---|---|
| Q1 | Single tenant (SCD only). |
| Q2 | (drag-drop withdrawn after Q1 → not implemented) |
| Q5 | Tool-use approach for the agent (LLM calls structured tools; no SQL injection surface). |
| Q6 | Pinned charts render in a new "Custom charts" section on `/admin/analytics`, below the existing built-in charts. |
| Q7 | Onboarding = first-login guided tour with replay (no AI doc-bot in v1). |
| Q9 | Data-analyst agent is **separate** from the existing analytics chatbot. Different panel, different endpoint, different conversation tables. |
| Q10 | Admin enables the agent via two settings (`data_analyst_enabled`, `data_analyst_visible_to_staff`). No per-agent customization in v1. |

## Non-goals

- Multi-tenant / white-label (Q1=A).
- Drag-and-drop dashboard layout (withdrawn).
- AI-driven help bot (Q7=A picked the static tour).
- Per-agent system prompt configuration (Q10=A).
- SQL-generation by the agent (rejected in Q5; tool-use only).
- Chart editing post-pin (refresh + unpin only).

## Architecture

### Section 1 — Data-analyst agent (separate, feature-flagged)

Two AI surfaces remain distinct:

| | **Existing analytics chatbot** | **New data-analyst agent** |
|---|---|---|
| Panel | Floating "Ask the data" (bottom-right of `/admin/analytics`) | Floating "Build a chart" (bottom-left) |
| Endpoint | `POST /api/analytics/chat` (unchanged) | `POST /api/analytics/agent` (new) |
| Capability | Text Q&A on the analytics blob | Tool-calling → chart generation |
| History tables | `chat_conversations` + `chat_messages` | `agent_conversations` + `agent_messages` |
| Audience | Staff (existing) | Gated by feature flags |

**Feature flags** (rows in the existing `settings` table; admin edits via `/admin/settings`):

| Key | Default | Effect |
|---|---|---|
| `data_analyst_enabled` | `"false"` | Master switch. When `false`, the panel never renders; the API returns 503. |
| `data_analyst_visible_to_staff` | `"false"` | When master is on: `true` → all staff see it; `false` → admin-only. |

A new private `/api/me/features` endpoint returns `{data_analyst_visible: bool}` for the authenticated caller. The frontend reads this on login and on a refresh poll to decide whether to render the panel.

**Tools** (server-defined, OpenAI tool-call schema; all read-only via `db/models.py`):

| Tool | Inputs | Output |
|---|---|---|
| `query_jobs` | `filter` (`{machine_id?, college_id?, status?}`), `group_by` (`machine\|college\|day\|hour\|day_of_week`), `metric` (`count\|avg_wait\|avg_serve\|completion_rate\|avg_rating`), `period` | `[{group_value, group_label, value}]` |
| `query_feedback` | same shape, but always returns `avg_rating + count` | `[{group_value, group_label, avg_rating, count}]` |
| `query_funnel` | filter, period | `{joined, served, completed, no_show, cancelled, failure}` |
| `top_n` | filter, group_by, metric, n | sorted top-N rows |
| `compare_periods` | filter, metric, period_a, period_b | `{a, b, delta_abs, delta_pct}` |
| `make_chart` | data, type (`bar\|line\|pie\|table`), x, y, title | `chart_spec` JSON |

`chart_spec` shape:

```ts
type ChartSpec = {
  type: "bar" | "line" | "pie" | "table";
  title: string;
  x: { field: string; label: string };
  y: { field: string; label: string };
  data: Array<Record<string, string | number | null>>;
  context?: { filter?: object; period?: string; group_by?: string };
};
```

**OpenAI loop.** Same `_make_openai_client()` factory. The agent router builds `tools = [...]`, sends user message + system prompt + history; on `tool_calls`, executes each tool, appends `{role:"tool", tool_call_id, content:<json>}`, re-invokes. Hard cap: 4 tool round-trips per turn. Tool result rows capped at 1000 (with `truncated=true` flag).

**SSE events** (extending the existing `meta`/`delta`/`done`/`error`):
- `{type: "tool_call", name, args}` — emitted when the model invokes a tool ("Building chart…" indicator).
- `{type: "chart", spec}` — emitted when the assistant message contains a chart.

**Persistence** (new tables; mirror `chat_conversations` / `chat_messages`):

```sql
CREATE TABLE agent_conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_user_id INTEGER NOT NULL REFERENCES staff_users(id),
    title         TEXT NOT NULL DEFAULT 'New analysis',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE agent_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content         TEXT NOT NULL,
    tool_call_id    TEXT,
    tool_calls_json TEXT,
    chart_spec_json TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_agent_msgs_conv ON agent_messages(conversation_id, id);
```

Per-staff scoping: cross-user reads/deletes return 404 (CLAUDE.md convention).

**Visibility gating helper** (`api/auth.py::require_data_analyst`):

1. If `data_analyst_enabled != "true"` → 503.
2. Else if `data_analyst_visible_to_staff == "true"` → require_staff.
3. Else require_admin.

Used as a FastAPI dependency on every `/api/analytics/agent/*` route.

### Section 2 — Pinned custom charts

```sql
CREATE TABLE pinned_charts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chart_spec_json TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_by      INTEGER NOT NULL REFERENCES staff_users(id),
    pin_order       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_pinned_charts_order ON pinned_charts(pin_order, id);
```

- Tenant-wide visibility (every staff user sees every pin); `created_by` for audit only.
- New pins land with `pin_order = MAX + 1`.
- `chart_spec_json` is the JSON the agent emitted — what was pinned is what's shown.
- The `context` inside the spec lets us re-run the same query on **Refresh** without re-asking the agent.

**Routes** (`api/routes/pinned_charts.py`):

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/pinned-charts/` | staff | List ordered by `pin_order, id`. |
| POST | `/api/pinned-charts/` | staff | Body `{chart_spec, title}`; creator stamped from auth. |
| POST | `/api/pinned-charts/{id}/refresh` | staff | Re-run `context` query, return updated spec (no DB write). |
| DELETE | `/api/pinned-charts/{id}` | staff | Unpin. Idempotent on missing. |

**Frontend.** `web/src/components/analytics/CustomCharts.tsx`:

- Loads `listPinnedCharts()` on mount.
- Renders nothing if empty.
- Otherwise, "Custom charts" section header below `MachineTable`, vertical stack of cards (chart + title + Refresh + Unpin).
- Generic `<ChartFromSpec spec={...} />` dispatches to Recharts primitives based on `spec.type`.

**Pin from chat.** When the agent emits a `chart` SSE event, the rendered chart shows a **Pin** button. Click → small modal lets the user edit the title, then `POST /api/pinned-charts/`.

**No editing.** Pinned charts are immutable except for refresh. To change, unpin and re-ask. Avoids spec-drift.

### Section 3 — Onboarding tour

**Library:** `driver.js` (~5 KB).

**Trigger:**

- New column `staff_users.onboarded_at TEXT NULL`. NULL = not yet completed.
- After login, the auth bootstrap reads the staff user record. If `onboarded_at IS NULL`, the tour auto-runs.
- On completion (or skip), the frontend calls `POST /api/auth/me/onboarded` which stamps `onboarded_at = datetime('now')`.
- "Replay tour" menu item in the user dropdown re-runs without nulling the timestamp.

**Migration backfill:** existing rows get `onboarded_at = datetime('now')` so they don't see the tour.

**Steps (linear, 11 stops):**

1. NavBar overview
2. Public queue page
3. Admin nav (skipped for non-admin staff)
4. Machines
5. Staff
6. Settings
7. Colleges
8. Feedback
9. Analytics
10. Analytics chatbot
11. Data-analyst agent (only shown if visible to this caller)

Tour content lives in a static module `web/src/onboarding/tour-steps.ts` (an array of `{element, popover}` records). Adding/editing steps is a content-only change.

**Routing:** a thin `runTour()` wrapper navigates between certain steps before driver.js advances; sleeps 200ms after each `navigate` so the destination renders before the popover anchors.

**Skip behavior:** "Close" still marks `onboarded_at`. No partial-completion tracking.

### Section 4 — Schema additions, settings, dependencies

**New tables** (Section 1 + Section 2 above): `agent_conversations`, `agent_messages`, `pinned_charts`. All created in `_create_tables` (fresh) and `_migrate` (`CREATE TABLE IF NOT EXISTS`); indexes also `IF NOT EXISTS`.

**`staff_users.onboarded_at TEXT NULL`** — added in `_migrate`, backfilled with `datetime('now')` for existing rows.

**Two new `settings` rows** seeded by `_seed_settings` via `INSERT OR IGNORE`:

```python
"data_analyst_enabled":          "false",
"data_analyst_visible_to_staff": "false",
```

**Frontend deps:** `driver.js`. (Recharts already present.)

**Backend deps:** none new.

**New routes summary:**

| Method | Path | Auth |
|---|---|---|
| GET | `/api/me/features` | staff |
| POST | `/api/auth/me/onboarded` | staff |
| POST | `/api/analytics/agent` | data-analyst gate |
| POST | `/api/analytics/agent/stream` | data-analyst gate |
| GET | `/api/analytics/agent/conversations` | data-analyst gate |
| GET | `/api/analytics/agent/conversations/{id}` | data-analyst gate (404 on cross-user) |
| DELETE | `/api/analytics/agent/conversations/{id}` | data-analyst gate (404 on cross-user) |
| GET | `/api/analytics/agent/models` | data-analyst gate |
| GET | `/api/pinned-charts/` | staff |
| POST | `/api/pinned-charts/` | staff |
| POST | `/api/pinned-charts/{id}/refresh` | staff |
| DELETE | `/api/pinned-charts/{id}` | staff |

### Section 5 — Error handling, edge cases, testing

| Surface | Failure | Behavior |
|---|---|---|
| Agent — feature flag off | `data_analyst_enabled = false` | 503 from any agent endpoint; frontend hides the panel via `/api/me/features`. |
| Agent — visibility off, staff caller | `visible_to_staff = false`, caller staff | 403 from API; frontend never renders the panel. |
| Agent — OpenAI key missing | factory returns `None` | 503 "AI not configured" (lazy pattern). |
| Agent — tool execution raises | bug or DB outage | Catch in tool runner; emit `{type:"error"}` SSE; close stream. |
| Agent — tool returns >1000 rows | row cap | Truncate, set `truncated=true`; system prompt instructs the model to mention it. |
| Agent — model loops (>4 round-trips) | runaway | Hard cap; force a final assistant message + warn-log. |
| Pinned chart — refresh on stale spec | filter references deleted resource | Empty data + `meta.warning`; frontend shows yellow tip. |
| Pinned chart — unpin missing row | concurrent unpin | 404; frontend silently removes. |
| Onboarding — tour anchor selector misses | UI rename | driver.js falls back to centered popover; warn-log. Tour still advances. |
| Onboarding — `POST /me/onboarded` fails | network blip | Frontend retries once, leaves NULL; tour replays next login. |
| Settings — admin disables mid-conversation | poll cycle | Next request 503; frontend closes the panel; conversations persist for audit. |
| Settings — visibility flip OFF mid-session for staff | mid-session demotion | Next request 403; panel closes; their history persists. |

**Tests** (~35 new):

1. `tests/test_agent_db.py` (~6) — `agent_conversations` / `agent_messages` CRUD, `tool_calls_json` / `chart_spec_json` round-trip, ON DELETE CASCADE.
2. `tests/test_pinned_charts_db.py` (~5) — create/list/delete, `pin_order` auto-increment, refresh helper returns new data.
3. `tests/test_pinned_charts_api.py` (~6) — auth gates, list shape, refresh recomputes, unpin idempotency, 404 on missing.
4. `tests/test_agent_api.py` (~12) — feature-flag matrix (3 audiences × 3 outcomes), each tool's happy path, 1000-row truncation, 4-round-trip cap, SSE event sequence, cross-user 404 on conversation read.
5. `tests/test_onboarding_api.py` (~4) — `POST /me/onboarded` stamps timestamp, idempotent, requires staff.
6. `tests/test_features_api.py` (~3) — `/api/me/features` matrix.
7. `tests/test_db.py` (extend, +1) — fresh DB has 3 new tables, `onboarded_at` column, 2 new settings rows.

**Manual smoke checklist** (in this doc; not automated):

- Toggle `data_analyst_enabled` off → panel disappears within next poll.
- Toggle `data_analyst_visible_to_staff` on → non-admin staff sees panel after refresh.
- New staff login (or `UPDATE staff_users SET onboarded_at = NULL`) triggers the tour.
- Replay tour from user menu works without re-stamping `onboarded_at`.
- Build a chart, pin it, refresh page → appears in "Custom charts". Refresh updates data. Unpin removes it.

**Test budget delta:** 240 → ~275.

## Out of scope (reaffirmed)

- Multi-tenant.
- Drag-drop dashboard.
- AI doc-bot for staff onboarding.
- Per-agent system prompt customization.
- Chart editing post-pin.
- Saved gallery beyond the analytics page.
- Reordering pinned charts via drag (admin can edit `pin_order` directly via SQL or a future admin UI).
