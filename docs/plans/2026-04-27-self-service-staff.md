# Self-Service Staff Tooling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a separate data-analyst agent (OpenAI tool-calling → on-demand chart generation, gated by two admin-controlled feature flags) and a first-login guided tour, plus a "Custom charts" section on the analytics page where pinned charts persist.

**Architecture:** A new `/api/analytics/agent` endpoint mirrors the existing analytics chatbot but adds tool-calling. Six read-only tools (`query_jobs`, `query_feedback`, `query_funnel`, `top_n`, `compare_periods`, `make_chart`) operate on the existing DB through `db/models.py` helpers; `make_chart` produces a `chart_spec` JSON the frontend renders via Recharts. Pinned charts persist in a new `pinned_charts` table; "Custom charts" section on `/admin/analytics` renders them. Onboarding uses `driver.js` keyed off a new `staff_users.onboarded_at` column.

**Tech Stack:** FastAPI, aiosqlite, OpenAI SDK (tool-calling), discord.py, React + Vite + Tailwind, Recharts, `driver.js`.

**Design doc:** `docs/plans/2026-04-27-self-service-staff-design.md`.

**Key prior learnings to respect:**
- Lazy `_make_openai_client()` factory (learnings.md 2026-04-02 / 2026-04-26).
- Cross-user reads return 404, not 403 (CLAUDE.md).
- Server-side allowlist for user-selectable models (learnings.md 2026-04-26).
- SSE chat streaming uses fetch + ReadableStream + Bearer header (learnings.md 2026-04-26).
- Mocking OpenAI streaming requires `__aiter__` stub (learnings.md 2026-04-26).
- Partial unique indexes / additive migrations live in `_migrate`, post-CREATE.
- Backfill data migrations run AFTER seeds (learnings.md 2026-04-22).
- `pytestmark = pytest.mark.asyncio` + `db` fixture conventions.

---

## Task 1: Schema additions — agent tables, pinned_charts, staff_users.onboarded_at, settings rows

**Files:**
- Modify: `db/database.py` (`_create_tables`, `_migrate`, `_seed_settings`)
- Test: `tests/test_db.py` (extend, +4 tests)

**Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
async def test_agent_tables_exist(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(agent_conversations)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "staff_user_id", "title", "created_at", "updated_at"} <= cols
    cursor = await conn.execute("PRAGMA table_info(agent_messages)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "conversation_id", "role", "content",
             "tool_call_id", "tool_calls_json", "chart_spec_json",
             "created_at"} <= cols


async def test_pinned_charts_table_exists(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(pinned_charts)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert {"id", "chart_spec_json", "title", "created_by",
             "pin_order", "created_at"} <= cols


async def test_staff_users_has_onboarded_at(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(staff_users)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "onboarded_at" in cols


async def test_data_analyst_settings_seeded(db):
    val = await models.get_setting("data_analyst_enabled")
    assert val == "false"
    val = await models.get_setting("data_analyst_visible_to_staff")
    assert val == "false"
```

**Step 2: Run tests red**

```
pytest tests/test_db.py -v -k "agent or pinned or onboarded or data_analyst"
```

Expected: 4 FAILs.

**Step 3: Add tables to `_create_tables`**

Append inside the `executescript` block (after `feedback`):

```sql
CREATE TABLE IF NOT EXISTS agent_conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_user_id INTEGER NOT NULL REFERENCES staff_users(id),
    title         TEXT NOT NULL DEFAULT 'New analysis',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content         TEXT NOT NULL,
    tool_call_id    TEXT,
    tool_calls_json TEXT,
    chart_spec_json TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pinned_charts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chart_spec_json TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_by      INTEGER NOT NULL REFERENCES staff_users(id),
    pin_order       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**Step 4: Add migrations to `_migrate`**

Append (after the feedback block, before defensive chat-table CREATE IF NOT EXISTS):

```python
# Agent tables — additive on upgrade.
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS agent_conversations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        staff_user_id INTEGER NOT NULL REFERENCES staff_users(id),
        title         TEXT NOT NULL DEFAULT 'New analysis',
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS agent_messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
        content         TEXT NOT NULL,
        tool_call_id    TEXT,
        tool_calls_json TEXT,
        chart_spec_json TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
await db.execute(
    "CREATE INDEX IF NOT EXISTS idx_agent_msgs_conv "
    "ON agent_messages(conversation_id, id)"
)

# Pinned charts table.
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS pinned_charts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        chart_spec_json TEXT NOT NULL,
        title           TEXT NOT NULL,
        created_by      INTEGER NOT NULL REFERENCES staff_users(id),
        pin_order       INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
await db.execute(
    "CREATE INDEX IF NOT EXISTS idx_pinned_charts_order "
    "ON pinned_charts(pin_order, id)"
)

# staff_users.onboarded_at — backfill existing users so they don't
# see the tour on next login.
cursor = await db.execute("PRAGMA table_info(staff_users)")
staff_cols_v4 = {row[1] for row in await cursor.fetchall()}
if "onboarded_at" not in staff_cols_v4:
    await db.execute("ALTER TABLE staff_users ADD COLUMN onboarded_at TEXT")
    await db.execute(
        "UPDATE staff_users SET onboarded_at = datetime('now') "
        "WHERE onboarded_at IS NULL"
    )
```

**Step 5: Add `_seed_settings` rows**

In `_seed_settings`, extend the `defaults` dict:

```python
defaults = {
    ...,
    "data_analyst_enabled":          "false",
    "data_analyst_visible_to_staff": "false",
}
```

`INSERT OR IGNORE` semantics already in place — won't overwrite admin edits.

**Step 6: Run tests green**

```
pytest tests/test_db.py -v
pytest tests/ 2>&1 | tail -5
```

Expected: 4 new PASS, full suite still PASSes.

**Step 7: Commit**

```bash
git add db/database.py tests/test_db.py
git commit -m "feat(db): agent + pinned_charts tables, staff_users.onboarded_at, data-analyst settings

- agent_conversations + agent_messages mirror chat schema; chart_spec_json on assistant rows.
- pinned_charts (chart_spec_json, title, created_by FK, pin_order, created_at).
- idx_agent_msgs_conv + idx_pinned_charts_order created post-CREATE.
- staff_users.onboarded_at TEXT NULL; existing rows backfilled to datetime('now').
- Seeded settings: data_analyst_enabled=false, data_analyst_visible_to_staff=false.

Refs design: docs/plans/2026-04-27-self-service-staff-design.md"
```

---

## Task 2: DB models — agent + pinned_charts helpers

**Files:**
- Modify: `db/models.py` (append helpers)
- Test: `tests/test_agent_db.py` (new, ~6 tests)
- Test: `tests/test_pinned_charts_db.py` (new, ~5 tests)

### `tests/test_agent_db.py`

```python
"""DB-layer tests for agent_conversations + agent_messages."""
import pytest
import json
from db import models

pytestmark = pytest.mark.asyncio


async def _seed_staff(username: str = "agent-user") -> int:
    return (await models.create_staff_user(
        username=username, password="x", role="admin"
    ))["id"]


async def test_create_agent_conversation(db):
    sid = await _seed_staff("agent-create")
    conv = await models.create_agent_conversation(staff_user_id=sid, title="t1")
    assert conv["id"] > 0
    assert conv["staff_user_id"] == sid
    assert conv["title"] == "t1"


async def test_append_agent_message_and_get(db):
    sid = await _seed_staff("agent-append")
    conv = await models.create_agent_conversation(staff_user_id=sid, title="t")
    await models.append_agent_message(
        conversation_id=conv["id"], role="user", content="hello",
    )
    msgs = await models.get_agent_messages(conv["id"])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"


async def test_append_assistant_message_with_chart_spec(db):
    sid = await _seed_staff("agent-chart")
    conv = await models.create_agent_conversation(staff_user_id=sid, title="t")
    spec = {"type": "bar", "title": "x", "x": {"field": "g"}, "y": {"field": "v"},
             "data": [{"g": "A", "v": 1}]}
    await models.append_agent_message(
        conversation_id=conv["id"], role="assistant",
        content="here you go", chart_spec_json=json.dumps(spec),
    )
    msgs = await models.get_agent_messages(conv["id"])
    saved = json.loads(msgs[0]["chart_spec_json"])
    assert saved["type"] == "bar"


async def test_append_tool_message_with_tool_calls_json(db):
    sid = await _seed_staff("agent-tool")
    conv = await models.create_agent_conversation(staff_user_id=sid, title="t")
    await models.append_agent_message(
        conversation_id=conv["id"], role="tool",
        content='{"rows":[]}', tool_call_id="call_1",
    )
    msgs = await models.get_agent_messages(conv["id"])
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_call_id"] == "call_1"


async def test_list_agent_conversations_per_user(db):
    s1 = await _seed_staff("agent-u1")
    s2 = await _seed_staff("agent-u2")
    await models.create_agent_conversation(staff_user_id=s1, title="a")
    await models.create_agent_conversation(staff_user_id=s2, title="b")
    rows1 = await models.list_agent_conversations(s1)
    rows2 = await models.list_agent_conversations(s2)
    assert {r["title"] for r in rows1} == {"a"}
    assert {r["title"] for r in rows2} == {"b"}


async def test_delete_agent_conversation_cascades_messages(db):
    sid = await _seed_staff("agent-del")
    conv = await models.create_agent_conversation(staff_user_id=sid, title="t")
    await models.append_agent_message(
        conversation_id=conv["id"], role="user", content="hi",
    )
    await models.delete_agent_conversation(conv["id"])
    msgs = await models.get_agent_messages(conv["id"])
    assert msgs == []
```

### `tests/test_pinned_charts_db.py`

```python
"""DB-layer tests for pinned_charts."""
import pytest
import json
from db import models

pytestmark = pytest.mark.asyncio


async def _seed_staff(username: str = "pin-user") -> int:
    return (await models.create_staff_user(
        username=username, password="x", role="admin"
    ))["id"]


async def test_create_pinned_chart(db):
    sid = await _seed_staff("pin-create")
    spec = {"type": "bar", "title": "x", "x": {"field": "g"}, "y": {"field": "v"},
             "data": []}
    row = await models.create_pinned_chart(
        chart_spec=spec, title="My chart", created_by=sid,
    )
    assert row["id"] > 0
    assert row["title"] == "My chart"
    assert json.loads(row["chart_spec_json"])["type"] == "bar"


async def test_pin_order_auto_increments(db):
    sid = await _seed_staff("pin-order")
    a = await models.create_pinned_chart(
        chart_spec={"type": "bar"}, title="A", created_by=sid,
    )
    b = await models.create_pinned_chart(
        chart_spec={"type": "line"}, title="B", created_by=sid,
    )
    assert b["pin_order"] > a["pin_order"]


async def test_list_pinned_charts_ordered(db):
    sid = await _seed_staff("pin-list")
    await models.create_pinned_chart(chart_spec={"type": "bar"}, title="A",
                                       created_by=sid)
    await models.create_pinned_chart(chart_spec={"type": "bar"}, title="B",
                                       created_by=sid)
    rows = await models.list_pinned_charts()
    titles = [r["title"] for r in rows]
    assert titles.index("A") < titles.index("B")


async def test_delete_pinned_chart(db):
    sid = await _seed_staff("pin-del")
    row = await models.create_pinned_chart(
        chart_spec={"type": "bar"}, title="A", created_by=sid,
    )
    deleted = await models.delete_pinned_chart(row["id"])
    assert deleted is True
    rows = await models.list_pinned_charts()
    assert all(r["id"] != row["id"] for r in rows)


async def test_delete_pinned_chart_missing_returns_false(db):
    deleted = await models.delete_pinned_chart(99999)
    assert deleted is False
```

### Implementation in `db/models.py` (append)

```python
import json as _json


async def create_agent_conversation(*, staff_user_id: int, title: str) -> dict:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO agent_conversations (staff_user_id, title) "
        "VALUES (?, ?) RETURNING *",
        (staff_user_id, title),
    )
    row = await cursor.fetchone()
    await db.commit()
    return dict(row)


async def get_agent_conversation(conversation_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM agent_conversations WHERE id = ?", (conversation_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_agent_conversations(staff_user_id: int) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM agent_conversations WHERE staff_user_id = ? "
        "ORDER BY updated_at DESC",
        (staff_user_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def append_agent_message(
    *, conversation_id: int, role: str, content: str,
    tool_call_id: str | None = None,
    tool_calls_json: str | None = None,
    chart_spec_json: str | None = None,
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """
        INSERT INTO agent_messages
            (conversation_id, role, content, tool_call_id,
             tool_calls_json, chart_spec_json)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING *
        """,
        (conversation_id, role, content, tool_call_id,
         tool_calls_json, chart_spec_json),
    )
    row = await cursor.fetchone()
    await db.execute(
        "UPDATE agent_conversations SET updated_at = datetime('now') "
        "WHERE id = ?",
        (conversation_id,),
    )
    await db.commit()
    return dict(row)


async def get_agent_messages(conversation_id: int) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM agent_messages WHERE conversation_id = ? "
        "ORDER BY id ASC",
        (conversation_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


async def delete_agent_conversation(conversation_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM agent_conversations WHERE id = ?", (conversation_id,)
    )
    await db.commit()
    return cursor.rowcount > 0


async def create_pinned_chart(
    *, chart_spec: dict, title: str, created_by: int,
) -> dict:
    db = await get_db()
    # Compute next pin_order.
    cursor = await db.execute("SELECT COALESCE(MAX(pin_order), 0) + 1 FROM pinned_charts")
    next_order = (await cursor.fetchone())[0]
    cursor = await db.execute(
        """
        INSERT INTO pinned_charts (chart_spec_json, title, created_by, pin_order)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (_json.dumps(chart_spec), title, created_by, next_order),
    )
    row = await cursor.fetchone()
    await db.commit()
    return dict(row)


async def list_pinned_charts() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM pinned_charts ORDER BY pin_order ASC, id ASC"
    )
    return [dict(r) for r in await cursor.fetchall()]


async def get_pinned_chart(chart_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM pinned_charts WHERE id = ?", (chart_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_pinned_chart(chart_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM pinned_charts WHERE id = ?", (chart_id,)
    )
    await db.commit()
    return cursor.rowcount > 0
```

### Run + commit

```
pytest tests/test_agent_db.py tests/test_pinned_charts_db.py -v
pytest tests/ 2>&1 | tail -5
git add db/models.py tests/test_agent_db.py tests/test_pinned_charts_db.py
git commit -m "feat(db): agent + pinned_charts helpers

- create/list/get/append/delete for agent conversations + messages.
- create/list/get/delete for pinned_charts; pin_order auto-increments via MAX+1."
```

---

## Task 3: Onboarding endpoint + features endpoint + visibility helper

**Files:**
- Modify: `db/models.py` (append `mark_staff_onboarded`)
- Modify: `api/auth.py` (append `require_data_analyst` helper)
- Create: `api/routes/me.py` (new — `/api/me/features` and `/api/auth/me/onboarded`)
- Modify: `api/main.py` (mount router)
- Test: `tests/test_features_api.py` (new, ~3 tests)
- Test: `tests/test_onboarding_api.py` (new, ~4 tests)

### Tests — `tests/test_features_api.py`

```python
"""Tests for /api/me/features."""
import pytest
from httpx import ASGITransport, AsyncClient
from api.main import app
from config import settings as cfg
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _login(client, username, password):
    r = await client.post("/api/auth/login",
                          json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_features_admin_sees_data_analyst_when_enabled(client):
    await models.set_setting("data_analyst_enabled", "true")
    h = await _login(client, cfg.staff_username, cfg.staff_password)
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is True


async def test_features_staff_hidden_when_visibility_off(client):
    await models.set_setting("data_analyst_enabled", "true")
    await models.set_setting("data_analyst_visible_to_staff", "false")
    await models.create_staff_user(username="reg", password="r", role="staff")
    h = await _login(client, "reg", "r")
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is False


async def test_features_staff_sees_when_visibility_on(client):
    await models.set_setting("data_analyst_enabled", "true")
    await models.set_setting("data_analyst_visible_to_staff", "true")
    await models.create_staff_user(username="reg2", password="r", role="staff")
    h = await _login(client, "reg2", "r")
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is True
```

### Tests — `tests/test_onboarding_api.py`

```python
"""Tests for /api/auth/me/onboarded."""
import pytest
from httpx import ASGITransport, AsyncClient
from api.main import app
from config import settings as cfg
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _admin_headers(client):
    r = await client.post("/api/auth/login",
        json={"username": cfg.staff_username, "password": cfg.staff_password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_onboarded_requires_auth(client):
    res = await client.post("/api/auth/me/onboarded")
    assert res.status_code == 401


async def test_onboarded_stamps_timestamp(client):
    # Force onboarded_at NULL on the seeded admin
    conn = await models.get_db()
    await conn.execute("UPDATE staff_users SET onboarded_at = NULL")
    await conn.commit()
    h = await _admin_headers(client)
    res = await client.post("/api/auth/me/onboarded", headers=h)
    assert res.status_code == 200
    cursor = await conn.execute(
        "SELECT onboarded_at FROM staff_users WHERE username = ?",
        (cfg.staff_username,),
    )
    row = await cursor.fetchone()
    assert row["onboarded_at"] is not None


async def test_onboarded_idempotent(client):
    h = await _admin_headers(client)
    res1 = await client.post("/api/auth/me/onboarded", headers=h)
    res2 = await client.post("/api/auth/me/onboarded", headers=h)
    assert res1.status_code == 200
    assert res2.status_code == 200


async def test_features_returns_onboarded_at(client):
    """Auth /me endpoint should expose onboarded_at so frontend can decide."""
    h = await _admin_headers(client)
    res = await client.get("/api/auth/me", headers=h)
    body = res.json()
    assert "onboarded_at" in body
```

### Implementation

**`db/models.py`** — append:

```python
async def mark_staff_onboarded(staff_user_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE staff_users SET onboarded_at = datetime('now') "
        "WHERE id = ? AND onboarded_at IS NULL",
        (staff_user_id,),
    )
    await db.commit()


async def is_data_analyst_enabled() -> bool:
    return (await get_setting("data_analyst_enabled")) == "true"


async def is_data_analyst_visible_to_staff() -> bool:
    return (await get_setting("data_analyst_visible_to_staff")) == "true"
```

**`api/routes/me.py`** (new):

```python
"""Per-user feature flags + onboarding stamp."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_staff
from db import models

router = APIRouter(tags=["me"])


class FeatureFlags(BaseModel):
    data_analyst_visible: bool


@router.get("/api/me/features", response_model=FeatureFlags,
            dependencies=[Depends(require_staff)])
async def my_features(current=Depends(require_staff)):
    enabled = await models.is_data_analyst_enabled()
    if not enabled:
        return FeatureFlags(data_analyst_visible=False)
    if current["role"] == "admin":
        return FeatureFlags(data_analyst_visible=True)
    visible = await models.is_data_analyst_visible_to_staff()
    return FeatureFlags(data_analyst_visible=visible)


@router.post("/api/auth/me/onboarded", dependencies=[Depends(require_staff)])
async def mark_onboarded(current=Depends(require_staff)):
    await models.mark_staff_onboarded(current["id"])
    return {"status": "ok"}
```

(`require_staff` returns the staff user dict — verify the existing helper's return shape and adapt the parameter accordingly.)

**`api/auth.py`** — append:

```python
async def require_data_analyst(
    current=Depends(require_staff),
):
    """Gate for /api/analytics/agent/*. Returns staff user dict.

    503 if disabled, 403 if visibility off and caller is not admin.
    """
    if not await models.is_data_analyst_enabled():
        raise HTTPException(503, detail="Data-analyst agent not enabled")
    if current["role"] == "admin":
        return current
    if not await models.is_data_analyst_visible_to_staff():
        raise HTTPException(403, detail="Not available to staff")
    return current
```

**`api/main.py`** — mount:

```python
from api.routes import me as me_routes
app.include_router(me_routes.router)
```

**Existing `/api/auth/me` endpoint** — extend its response model to include `onboarded_at` from the staff record. Look at `api/routes/auth.py` for the current shape.

### Run + commit

```
pytest tests/test_features_api.py tests/test_onboarding_api.py tests/ -v 2>&1 | tail -10
git add db/models.py api/auth.py api/routes/me.py api/routes/auth.py api/main.py \
        tests/test_features_api.py tests/test_onboarding_api.py
git commit -m "feat(api): /api/me/features, /api/auth/me/onboarded, require_data_analyst gate

- mark_staff_onboarded helper; idempotent UPDATE.
- /api/me/features returns {data_analyst_visible} based on role + settings.
- /api/auth/me/onboarded stamps onboarded_at; 200 even if already stamped.
- require_data_analyst dependency for agent routes (503 / 403 / pass).
- /api/auth/me response now includes onboarded_at for the tour gate."
```

---

## Task 4: Data-analyst agent — tools

**Files:**
- Create: `api/routes/agent_tools.py` (new — pure tool-call functions)
- Test: `tests/test_agent_tools.py` (new, ~10 tests)

### Tool surface

Six functions, each `async`, each returning a dict (JSON-serializable):

```python
async def query_jobs(
    *, filter: dict, group_by: str, metric: str, period: str | None,
) -> dict:
    """Returns {rows: [{group_value, group_label, value}], truncated: bool}."""

async def query_feedback(
    *, filter: dict, group_by: str, period: str | None,
) -> dict:
    """Returns {rows: [{group_value, group_label, avg_rating, count}],
               truncated: bool}."""

async def query_funnel(*, filter: dict, period: str | None) -> dict:
    """Returns {joined, served, completed, no_show, cancelled, failure}."""

async def top_n(
    *, filter: dict, group_by: str, metric: str, n: int,
    period: str | None,
) -> dict:
    """Returns {rows: [...]} sorted desc by value, capped at n."""

async def compare_periods(
    *, filter: dict, metric: str, period_a: str, period_b: str,
) -> dict:
    """Returns {a, b, delta_abs, delta_pct}."""

def make_chart(
    *, data: list[dict], type: str, x: dict, y: dict, title: str,
    context: dict | None = None,
) -> dict:
    """Pure formatter. Returns chart_spec JSON."""
```

Hard caps:
- All `query_*` tools cap rows at 1000 and set `truncated: True` if more would have been returned.
- `period` accepts `"day"|"week"|"month"` or `None` (defaults to `"week"`); resolves to a `(start, end)` window using the existing `_date_range` from `api/routes/analytics.py`.

Implement each by JOINing `queue_entries` with `users`, `machines`, `colleges`, `feedback` as appropriate. Use `db/models.py` helpers where they already exist (e.g. `feedback_aggregates_*`).

### Tests — `tests/test_agent_tools.py`

Each test seeds the DB and calls a tool directly. Cover:
1. `query_jobs` with `group_by="machine", metric="count"` returns one row per machine.
2. `query_jobs` filter by `college_id` narrows results.
3. `query_jobs` `metric="avg_rating"` joins feedback correctly.
4. `query_feedback` returns avg + count per machine.
5. `query_funnel` sums counts across statuses.
6. `top_n` returns the top N sorted desc.
7. `compare_periods` returns sane delta numbers.
8. `make_chart` produces a well-formed `chart_spec` (no DB hit).
9. `query_jobs` with empty data returns `rows=[]`, `truncated=False`.
10. `query_jobs` row cap: seed 1100 entries, assert `truncated=True`, `len(rows) == 1000`.

### Run + commit

```
pytest tests/test_agent_tools.py -v
git add api/routes/agent_tools.py tests/test_agent_tools.py
git commit -m "feat(api): data-analyst agent tools (query_jobs/feedback/funnel, top_n, compare_periods, make_chart)

- All tools read-only via db/models.py + direct JOINs.
- Row cap: 1000 (truncated flag).
- make_chart is a pure formatter; produces chart_spec JSON for frontend rendering."
```

---

## Task 5: Data-analyst agent — `/api/analytics/agent` endpoints

**Files:**
- Create: `api/routes/agent.py` (new)
- Modify: `api/main.py` (mount router)
- Test: `tests/test_agent_api.py` (new, ~12 tests)

### Endpoint surface

| Method | Path | Behavior |
|---|---|---|
| POST | `/api/analytics/agent` | Non-streaming tool-calling. Body: `{message, conversation_id?, model?}`. Returns `{conversation_id, message_id, content, chart_spec?}`. |
| POST | `/api/analytics/agent/stream` | SSE. Same body. Emits `meta`, `tool_call`, `delta`, `chart`, `done`, `error`. |
| GET | `/api/analytics/agent/conversations` | Per-staff list. |
| GET | `/api/analytics/agent/conversations/{id}` | Detail (404 cross-user). |
| DELETE | `/api/analytics/agent/conversations/{id}` | Delete (404 cross-user). |
| GET | `/api/analytics/agent/models` | Allowlisted models. |

All gated by `require_data_analyst`.

### Implementation skeleton (`api/routes/agent.py`)

```python
"""Data-analyst agent — tool-calling chat."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import require_data_analyst
from api.routes import agent_tools as T
from db import models

router = APIRouter(prefix="/api/analytics/agent", tags=["agent"])


ALLOWED_MODELS = [
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini (default)"},
    {"id": "gpt-5.4",      "label": "GPT-5.4"},
    {"id": "gpt-4o",       "label": "GPT-4o"},
]
DEFAULT_MODEL = "gpt-5.4-mini"
MAX_TOOL_ROUND_TRIPS = 4


# Tool registry for OpenAI tool-call schema.
def _tool_schemas() -> list[dict]:
    """OpenAI tool definitions matching agent_tools.py."""
    return [
        # one entry per tool — full JSON schema for inputs
        # (omitted here for brevity; see implementation)
    ]


async def _execute_tool(name: str, args: dict) -> dict:
    fn = {
        "query_jobs":      T.query_jobs,
        "query_feedback":  T.query_feedback,
        "query_funnel":    T.query_funnel,
        "top_n":           T.top_n,
        "compare_periods": T.compare_periods,
        "make_chart":      T.make_chart,
    }.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool: {name}")
    return await fn(**args) if callable(fn) else {}


# ... endpoints (POST, POST /stream, GET conversations, GET {id}, DELETE {id}, GET models)
```

### SSE streaming

Mirror the existing `chat_stream` in `api/routes/chat.py`. Adapt event encoding:

```python
async def _emit(event: dict) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")
```

Sequence per turn:
1. `meta` (conversation_id)
2. (for each tool_call round) `tool_call` events as the model picks tools, execute each tool, append `tool` messages
3. After final assistant content, emit `delta` events for streamed tokens (if streaming) or one `delta` with the full content
4. `chart` event if assistant message contains `chart_spec_json`
5. `done` (message_id)
6. `error` on exception, then close

### Tests cover

1. Auth gate: `data_analyst_enabled=false` → 503.
2. Auth gate: enabled but `visible_to_staff=false`, staff caller → 403.
3. Auth gate: enabled, admin caller → 200.
4. Models endpoint returns allowlist with `default=true` flag on default.
5. POST without `conversation_id` creates a new conversation.
6. POST with bad `model` returns 400.
7. POST with prompt that triggers `query_jobs` → tool executed, assistant message persisted.
8. POST that triggers `make_chart` → response includes `chart_spec`; chart_spec_json saved in `agent_messages`.
9. SSE endpoint emits `meta` → `tool_call` → `chart` → `done` in order (mock OpenAI streaming with `__aiter__` per learnings.md 2026-04-26).
10. Cross-user GET on conversation returns 404.
11. DELETE cross-user returns 404.
12. Hard-cap test: model that always wants more tools forced to terminate after 4 round-trips with a fallback assistant message.

(Mock OpenAI for all tests — never call the real API.)

### Run + commit

```
pytest tests/test_agent_api.py -v
pytest tests/ 2>&1 | tail -5
git add api/routes/agent.py api/main.py tests/test_agent_api.py
git commit -m "feat(api): /api/analytics/agent — tool-calling chart-builder agent

- Endpoint set mirrors chat: POST, /stream (SSE), GET/DELETE conversations, /models.
- Six tools wired via _tool_schemas + _execute_tool; OpenAI tool-call loop.
- Hard caps: 4 round-trips per turn, 1000 rows per tool result.
- SSE event vocab extended: tool_call, chart.
- Per-staff scoping + 404 cross-user (CLAUDE.md convention).
- Mocked OpenAI in tests via __aiter__ stub (learnings.md 2026-04-26)."
```

---

## Task 6: Pinned charts — API + frontend section

**Files:**
- Create: `api/routes/pinned_charts.py` (new)
- Modify: `api/main.py` (mount)
- Test: `tests/test_pinned_charts_api.py` (new, ~6 tests)
- Modify: `web/src/api/types.ts` — `PinnedChart`, `ChartSpec`
- Modify: `web/src/api/client.ts` — `listPinnedCharts`, `pinChart`, `unpinChart`, `refreshPinnedChart`
- Create: `web/src/components/analytics/CustomCharts.tsx`
- Create: `web/src/components/analytics/ChartFromSpec.tsx`
- Modify: `web/src/pages/Analytics.tsx` — render `<CustomCharts />` below `MachineTable`

### `api/routes/pinned_charts.py`

```python
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_staff
from api.routes import agent_tools as T
from db import models

router = APIRouter(prefix="/api/pinned-charts", tags=["pinned-charts"])


class PinChartBody(BaseModel):
    chart_spec: dict
    title: str


class PinnedChartOut(BaseModel):
    id: int
    title: str
    chart_spec: dict
    pin_order: int
    created_at: str
    created_by_username: str | None


def _to_out(row: dict, username: str | None) -> PinnedChartOut:
    return PinnedChartOut(
        id=row["id"], title=row["title"],
        chart_spec=json.loads(row["chart_spec_json"]),
        pin_order=row["pin_order"], created_at=row["created_at"],
        created_by_username=username,
    )


@router.get("/", response_model=list[PinnedChartOut],
            dependencies=[Depends(require_staff)])
async def list_endpoint():
    rows = await models.list_pinned_charts()
    out = []
    for r in rows:
        creator = await models.get_staff_user_by_id(r["created_by"])
        out.append(_to_out(r, creator["username"] if creator else None))
    return out


@router.post("/", response_model=PinnedChartOut,
             dependencies=[Depends(require_staff)])
async def create_endpoint(body: PinChartBody, current=Depends(require_staff)):
    row = await models.create_pinned_chart(
        chart_spec=body.chart_spec, title=body.title,
        created_by=current["id"],
    )
    return _to_out(row, current["username"])


@router.post("/{chart_id}/refresh", response_model=PinnedChartOut,
             dependencies=[Depends(require_staff)])
async def refresh_endpoint(chart_id: int):
    row = await models.get_pinned_chart(chart_id)
    if row is None:
        raise HTTPException(404)
    spec = json.loads(row["chart_spec_json"])
    ctx = spec.get("context") or {}
    if ctx.get("group_by") and ctx.get("filter") is not None:
        # Re-run a query_jobs equivalent based on context.
        result = await T.query_jobs(
            filter=ctx.get("filter") or {},
            group_by=ctx["group_by"],
            metric=ctx.get("metric", "count"),
            period=ctx.get("period"),
        )
        spec["data"] = [
            {spec["x"]["field"]: r["group_label"], spec["y"]["field"]: r["value"]}
            for r in result["rows"]
        ]
    creator = await models.get_staff_user_by_id(row["created_by"])
    fresh = {**row, "chart_spec_json": json.dumps(spec)}
    return _to_out(fresh, creator["username"] if creator else None)


@router.delete("/{chart_id}", dependencies=[Depends(require_staff)])
async def unpin_endpoint(chart_id: int):
    deleted = await models.delete_pinned_chart(chart_id)
    if not deleted:
        raise HTTPException(404)
    return {"status": "ok"}
```

### Tests (`tests/test_pinned_charts_api.py`)

1. GET requires auth.
2. GET returns rows ordered by `pin_order`.
3. POST creates with current user as `created_by`.
4. POST returns response with `created_by_username`.
5. DELETE returns 200; second DELETE returns 404.
6. Refresh re-runs the query and returns updated `chart_spec`.

### Frontend

**`web/src/api/types.ts`** — append:

```ts
export type ChartType = "bar" | "line" | "pie" | "table";

export interface ChartSpec {
  type: ChartType;
  title: string;
  x: { field: string; label: string };
  y: { field: string; label: string };
  data: Array<Record<string, string | number | null>>;
  context?: { filter?: object; period?: string; group_by?: string; metric?: string };
}

export interface PinnedChart {
  id: number;
  title: string;
  chart_spec: ChartSpec;
  pin_order: number;
  created_at: string;
  created_by_username: string | null;
}
```

**`web/src/api/client.ts`** — append:

```ts
export const listPinnedCharts = () =>
  request<PinnedChart[]>("/pinned-charts/");

export const pinChart = (chart_spec: ChartSpec, title: string) =>
  request<PinnedChart>("/pinned-charts/", {
    method: "POST",
    body: JSON.stringify({ chart_spec, title }),
  });

export const refreshPinnedChart = (id: number) =>
  request<PinnedChart>(`/pinned-charts/${id}/refresh`, { method: "POST" });

export const unpinChart = (id: number) =>
  request<{ status: string }>(`/pinned-charts/${id}`, { method: "DELETE" });
```

**`web/src/components/analytics/ChartFromSpec.tsx`** — generic dispatcher rendering bar / line / pie / table from a `ChartSpec` using Recharts. Match the existing chart styling (`MachineUtilization` is a good reference).

**`web/src/components/analytics/CustomCharts.tsx`** — section renderer. On mount, calls `listPinnedCharts()`. If empty, renders nothing. Otherwise, "Custom charts" heading + vertical stack of cards, each with `<ChartFromSpec>`, the title, Refresh, Unpin (with confirm).

**`web/src/pages/Analytics.tsx`** — add `<CustomCharts />` below `<MachineTable />`:

```tsx
<MachineTable machines={data.machines} />
<CustomCharts />
```

### Run + commit

```
pytest tests/test_pinned_charts_api.py -v
cd web && npx tsc -b
git add api/routes/pinned_charts.py api/main.py tests/test_pinned_charts_api.py \
        web/src/api/types.ts web/src/api/client.ts \
        web/src/components/analytics/ChartFromSpec.tsx \
        web/src/components/analytics/CustomCharts.tsx \
        web/src/pages/Analytics.tsx
git commit -m "feat(charts): pinned charts API + Custom Charts section + ChartFromSpec renderer

- /api/pinned-charts CRUD (staff-readable).
- Refresh re-runs the chart's context query against current data.
- Generic ChartFromSpec dispatches bar/line/pie/table to Recharts.
- CustomCharts section renders below MachineTable; hidden when empty."
```

---

## Task 7: Data-analyst agent — frontend panel

**Files:**
- Create: `web/src/components/analytics/AnalystAgent.tsx` (new floating panel, separate from existing AnalyticsChat)
- Modify: `web/src/api/types.ts` — agent message + conversation types
- Modify: `web/src/api/client.ts` — agent API + SSE helper
- Modify: `web/src/pages/Analytics.tsx` — mount panel when `data_analyst_visible`

### Types (`web/src/api/types.ts`)

```ts
export interface AgentMessage {
  id: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  chart_spec?: ChartSpec;
  created_at: string;
}

export interface AgentConversationSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface AgentConversationDetail extends AgentConversationSummary {
  messages: AgentMessage[];
}

export interface FeatureFlags {
  data_analyst_visible: boolean;
}
```

### Client (`web/src/api/client.ts`)

```ts
export const fetchFeatures = () => request<FeatureFlags>("/me/features");
export const markOnboarded = () =>
  request<{ status: string }>("/auth/me/onboarded", { method: "POST" });

export const listAgentConversations = () =>
  request<AgentConversationSummary[]>("/analytics/agent/conversations");
export const getAgentConversation = (id: number) =>
  request<AgentConversationDetail>(`/analytics/agent/conversations/${id}`);
export const deleteAgentConversation = (id: number) =>
  request<{ status: string }>(`/analytics/agent/conversations/${id}`, { method: "DELETE" });
export const listAgentModels = () =>
  request<{ models: { id: string; label: string }[]; default: string }>(
    "/analytics/agent/models"
  );

/** Stream agent reply via SSE — fetch + ReadableStream pattern. */
export async function postAgentStream(
  body: { message: string; conversation_id?: number; model?: string },
  handlers: {
    onMeta?: (conversationId: number) => void;
    onToolCall?: (name: string, args: any) => void;
    onDelta?: (content: string) => void;
    onChart?: (spec: ChartSpec) => void;
    onDone?: (messageId: number) => void;
    onError?: (detail: string) => void;
  }
): Promise<void> {
  // Mirror postChatStream from existing client.ts; just point at /analytics/agent/stream.
  // Add tool_call and chart event handling.
}
```

### Panel (`web/src/components/analytics/AnalystAgent.tsx`)

Mirror `AnalyticsChat.tsx` shape, but:
- Floating button bottom-LEFT (existing chat is bottom-right).
- Title "Build a chart".
- Message list renders charts inline (using `<ChartFromSpec>`) when message has `chart_spec`.
- Each chart card has a **Pin** button → opens a tiny title-edit modal → `pinChart(spec, title)`.
- "Tool call" indicator while `onToolCall` events are arriving (shows tool name in muted text).

### Mount in `Analytics.tsx`

```tsx
const [features, setFeatures] = useState<FeatureFlags | null>(null);
useEffect(() => {
  fetchFeatures().then(setFeatures).catch(() => setFeatures(null));
}, []);
...
{features?.data_analyst_visible && <AnalystAgent period={period} />}
```

### Run + commit

```
cd web && npx tsc -b
git add web/src/components/analytics/AnalystAgent.tsx \
        web/src/api/types.ts web/src/api/client.ts \
        web/src/pages/Analytics.tsx
git commit -m "feat(web): floating Analyst Agent panel (charts via tool-calling)

- Bottom-left panel, separate from existing analytics chat.
- SSE streaming with new tool_call + chart events.
- Inline chart rendering via ChartFromSpec; Pin button opens title modal.
- Mounted only when /api/me/features.data_analyst_visible is true."
```

---

## Task 8: Admin settings UI — data-analyst flags

**Files:**
- Modify: `web/src/pages/admin/Settings.tsx` — add "Data analyst agent" section with two toggles

### What to add

A new section under existing settings groups:

```tsx
<section>
  <h2>Data analyst agent</h2>
  <Toggle
    label="Enable data-analyst agent"
    settingKey="data_analyst_enabled"
  />
  <Toggle
    label="Visible to staff (uncheck = admin-only)"
    settingKey="data_analyst_visible_to_staff"
    disabled={!enabledValue}
  />
</section>
```

Match the existing `Settings.tsx` form pattern (dirty-state Save button, etc.). Both toggles map to the existing `PATCH /api/settings/` endpoint.

### Run + commit

```
cd web && npx tsc -b
git add web/src/pages/admin/Settings.tsx
git commit -m "feat(web): admin Settings page exposes data-analyst feature flags

- Two new toggles under a 'Data analyst agent' section.
- visible_to_staff disabled when master is off."
```

---

## Task 9: Onboarding tour

**Files:**
- Install: `driver.js` via `npm install driver.js`
- Create: `web/src/onboarding/tour-steps.ts` (the JSON content)
- Create: `web/src/onboarding/runTour.ts` (the wrapper that consumes steps + navigates)
- Modify: `web/src/hooks/useAuth.ts` — auto-run tour when `onboarded_at IS NULL`
- Modify: `web/src/components/NavBar.tsx` — add "Replay tour" menu item

### `tour-steps.ts`

Static array of `{element: string, popover: {title: string, description: string, side?: string}, navigateTo?: string, requiresAdmin?: boolean}` records. Eleven entries per the design doc, with `navigateTo` between steps that need a route change.

### `runTour.ts`

```ts
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import { tourSteps } from "./tour-steps";

export async function runTour(navigate: (path: string) => void,
                                isAdmin: boolean): Promise<void> {
  const steps = tourSteps.filter(s => !s.requiresAdmin || isAdmin);
  // For each step that has navigateTo, advance with navigation;
  // call driver.start() with the filtered steps array.
  // Use driver's onNextClick / onPrevClick hooks to navigate before
  // moving to the next anchor; sleep 200ms for the destination to render.
}
```

### Auto-run

In `useAuth.ts` (or wherever the auth state is hydrated), after fetching `/api/auth/me`:

```ts
if (me.onboarded_at == null) {
  await runTour(navigate, me.role === "admin");
  await markOnboarded();
}
```

### Replay menu item

In NavBar dropdown:

```tsx
<button onClick={() => runTour(navigate, isAdmin)}>Replay tour</button>
```

### Run + commit

```
cd web && npm install driver.js
npx tsc -b
git add web/package.json web/package-lock.json \
        web/src/onboarding/tour-steps.ts web/src/onboarding/runTour.ts \
        web/src/hooks/useAuth.ts web/src/components/NavBar.tsx
git commit -m "feat(web): first-login guided tour with replay (driver.js)

- 11-stop linear tour covering NavBar, public queue, admin sections, analytics, chat, agent.
- Auto-runs once when staff_users.onboarded_at IS NULL; POST /api/auth/me/onboarded stamps.
- 'Replay tour' menu item re-runs without re-stamping.
- Admin-only stops skipped for regular staff."
```

---

## Task 10: Final verification + memory + CLAUDE.md

**Files:**
- Modify: `short_term_memory.md` (prepend new entry)
- Modify: `CLAUDE.md` (`## Completed Work` — append entry)

### Verify

```
pytest tests/ 2>&1 | tail -5     # expect ~275 passing
cd web && npx tsc -b
```

### Manual smoke

- Toggle `data_analyst_enabled` on/off → panel appears/disappears within next poll.
- Toggle `data_analyst_visible_to_staff` on → non-admin staff sees panel after refresh.
- New staff login (or `UPDATE staff_users SET onboarded_at = NULL`) → tour auto-runs.
- "Replay tour" works without re-stamping.
- Build a chart via the agent, pin it, refresh page → it's in "Custom charts". Refresh → data updates. Unpin → removed.

### `short_term_memory.md` entry

Prepend a 2026-04-27 section summarizing:
- New tables / settings / column.
- Tool-use agent endpoint, tools, hard caps, SSE protocol.
- Pinned charts + Custom charts section.
- Onboarding tour (driver.js).
- All commit SHAs (filled in at execution time).

### `CLAUDE.md` Completed Work entry

```markdown
### 2026-04-27 — Self-Service Staff Tooling
- Separate data-analyst agent (`/api/analytics/agent`) with OpenAI tool-calling: 6 tools (query_jobs, query_feedback, query_funnel, top_n, compare_periods, make_chart), 4 round-trip cap, 1000-row tool cap, SSE protocol extended with tool_call + chart events. Gated by `data_analyst_enabled` + `data_analyst_visible_to_staff` settings.
- New "Custom charts" section on `/admin/analytics` renders pinned charts (immutable; refresh re-runs the saved query, unpin removes). `<ChartFromSpec>` dispatches bar/line/pie/table to Recharts.
- First-login guided tour using `driver.js`. `staff_users.onboarded_at` gates auto-run; "Replay tour" in NavBar.
- New tables: `agent_conversations`, `agent_messages`, `pinned_charts`. New endpoint groups: `/api/me/features`, `/api/auth/me/onboarded`, `/api/analytics/agent/*`, `/api/pinned-charts/*`. Admin Settings page gains the two flags.
```

### Commit

```
git add short_term_memory.md CLAUDE.md
git commit -m "docs: capture self-service-staff shipped state in memory + completed work"
```

---

## Done

Implementation is complete when:
- All ~275 tests pass.
- `npx tsc -b` clean.
- Manual smoke checklist passes.
- `short_term_memory.md` and `CLAUDE.md` updated with actual commit SHAs.
- Branch ready for `/gitpush`.
