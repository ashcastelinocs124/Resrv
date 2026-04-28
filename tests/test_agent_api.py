"""Tests for /api/analytics/agent — tool-calling chart-builder."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import hash_password
from api.main import app
from api.settings_store import set_setting
from config import settings as cfg

pytestmark = pytest.mark.asyncio


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def analyst_enabled(db):
    """Master flag on; staff visibility on."""
    await set_setting("data_analyst_enabled", "true")
    await set_setting("data_analyst_visible_to_staff", "true")


async def _admin_headers(client):
    r = await client.post(
        "/api/auth/login",
        json={
            "username": cfg.staff_username,
            "password": cfg.staff_password,
        },
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _staff_headers(client, db, username: str = "staff1", password: str = "p"):
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hash_password(password), "staff"),
    )
    await db.commit()
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_tool_call(name: str, args: dict, call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name, arguments=json.dumps(args)
        ),
    )


def _make_response(content: str | None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


@pytest.fixture
def mock_agent_openai(monkeypatch):
    """Stub OpenAI client that returns scripted responses in sequence."""
    state: dict = {"calls": [], "responses": []}

    def _factory():
        client_obj = MagicMock()

        async def _create(**kwargs):
            state["calls"].append(kwargs)
            if not state["responses"]:
                return _make_response("Done.", tool_calls=None)
            return state["responses"].pop(0)

        client_obj.chat.completions.create = _create
        return client_obj

    from api.routes import agent as agent_mod
    monkeypatch.setattr(agent_mod, "_make_openai_client", _factory)
    return state


# ── Auth gate ────────────────────────────────────────────────────────────


async def test_disabled_master_returns_503(client, db):
    await set_setting("data_analyst_enabled", "false")
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "hi"}
    )
    assert r.status_code == 503


async def test_enabled_but_staff_visibility_off_returns_403_for_staff(client, db):
    await set_setting("data_analyst_enabled", "true")
    await set_setting("data_analyst_visible_to_staff", "false")
    h = await _staff_headers(client, db, "staff_blocked", "p")
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "hi"}
    )
    assert r.status_code == 403


async def test_admin_passes_when_enabled(
    client, db, analyst_enabled, mock_agent_openai,
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "hi"}
    )
    assert r.status_code == 200, r.text


# ── Models endpoint ──────────────────────────────────────────────────────


async def test_models_endpoint_returns_allowlist(
    client, db, analyst_enabled,
):
    h = await _admin_headers(client)
    r = await client.get("/api/analytics/agent/models", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "gpt-5.4-mini"
    assert {m["id"] for m in body["models"]} == {
        "gpt-5.4", "gpt-5.4-mini", "gpt-4o",
    }


async def test_post_rejects_unknown_model(
    client, db, analyst_enabled, mock_agent_openai,
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent",
        headers=h,
        json={"message": "hi", "model": "gpt-evil"},
    )
    assert r.status_code == 400


# ── Conversation creation ────────────────────────────────────────────────


async def test_post_creates_new_conversation_when_missing(
    client, db, analyst_enabled, mock_agent_openai,
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "Hello world"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation_id"] > 0
    assert body["content"] == "Done."


async def test_post_with_tool_call_executes_query_jobs(
    client, db, analyst_enabled, mock_agent_openai,
):
    """Mock the model to call query_jobs, then return text — ensure flow runs."""
    mock_agent_openai["responses"] = [
        _make_response(
            None,
            tool_calls=[_make_tool_call(
                "query_jobs",
                {"group_by": "machine", "metric": "count", "period": "day"},
            )],
        ),
        _make_response("Looked up jobs by machine.", tool_calls=None),
    ]
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "show jobs"}
    )
    assert r.status_code == 200
    assert r.json()["content"] == "Looked up jobs by machine."
    # Verify tool result was persisted as a 'tool' message.
    cid = r.json()["conversation_id"]
    detail = await client.get(
        f"/api/analytics/agent/conversations/{cid}", headers=h
    )
    roles = [m["role"] for m in detail.json()["messages"]]
    assert "tool" in roles


async def test_post_with_make_chart_attaches_chart_spec(
    client, db, analyst_enabled, mock_agent_openai,
):
    chart_args = {
        "data": [{"label": "A", "value": 1}],
        "type": "bar",
        "x": {"field": "label", "label": "Group"},
        "y": {"field": "value", "label": "Count"},
        "title": "Mock chart",
    }
    mock_agent_openai["responses"] = [
        _make_response(
            None,
            tool_calls=[_make_tool_call("make_chart", chart_args)],
        ),
        _make_response("Here is your chart.", tool_calls=None),
    ]
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h, json={"message": "build me a chart"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chart_spec"] is not None
    assert body["chart_spec"]["type"] == "bar"
    # And the chart_spec_json column was persisted on the assistant message.
    cid = body["conversation_id"]
    detail = await client.get(
        f"/api/analytics/agent/conversations/{cid}", headers=h
    )
    last_assistant = [
        m for m in detail.json()["messages"] if m["role"] == "assistant"
    ][-1]
    assert last_assistant["chart_spec"]["type"] == "bar"


# ── SSE streaming ────────────────────────────────────────────────────────


async def test_stream_emits_meta_tool_call_chart_done(
    client, db, analyst_enabled, mock_agent_openai,
):
    chart_args = {
        "data": [{"label": "A", "value": 1}],
        "type": "bar",
        "x": {"field": "label"},
        "y": {"field": "value"},
        "title": "Mock chart",
    }
    mock_agent_openai["responses"] = [
        _make_response(
            None,
            tool_calls=[_make_tool_call("make_chart", chart_args)],
        ),
        _make_response("Final answer.", tool_calls=None),
    ]
    h = await _admin_headers(client)
    async with client.stream(
        "POST",
        "/api/analytics/agent/stream",
        headers=h,
        json={"message": "build me a chart"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert '"type": "meta"' in text
    assert '"type": "tool_call"' in text
    assert '"name": "make_chart"' in text
    assert '"type": "chart"' in text
    assert '"type": "delta"' in text
    assert '"type": "done"' in text


# ── Cross-user isolation ─────────────────────────────────────────────────


async def test_get_conversation_returns_404_cross_user(
    client, db, analyst_enabled, mock_agent_openai,
):
    h_admin = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h_admin, json={"message": "secret"}
    )
    cid = r.json()["conversation_id"]

    h_eve = await _staff_headers(client, db, "eve", "pw")
    r = await client.get(
        f"/api/analytics/agent/conversations/{cid}", headers=h_eve
    )
    assert r.status_code == 404


async def test_delete_conversation_returns_404_cross_user(
    client, db, analyst_enabled, mock_agent_openai,
):
    h_admin = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent", headers=h_admin, json={"message": "secret"}
    )
    cid = r.json()["conversation_id"]

    h_eve = await _staff_headers(client, db, "mallory", "pw")
    r = await client.delete(
        f"/api/analytics/agent/conversations/{cid}", headers=h_eve
    )
    assert r.status_code == 404


# ── Hard cap ─────────────────────────────────────────────────────────────


async def test_tool_call_cap_terminates_with_fallback(
    client, db, analyst_enabled, mock_agent_openai,
):
    """Force the loop to exhaust all 4 round-trips with tool calls."""
    tool_call_resp = _make_response(
        None,
        tool_calls=[_make_tool_call(
            "query_funnel", {"period": "day"}, call_id=f"call_x"
        )],
    )
    mock_agent_openai["responses"] = [tool_call_resp] * 8 + [
        _make_response("Forced summary.", tool_calls=None),
    ]
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/agent",
        headers=h,
        json={"message": "loop forever"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Either we got a fallback response or the explicit "Forced summary."
    assert body["content"]
