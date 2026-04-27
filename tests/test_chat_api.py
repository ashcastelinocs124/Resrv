"""POST /api/analytics/chat with a mocked OpenAI client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import hash_password
from api.main import app
from config import settings as cfg

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _admin_headers(client):
    r = await client.post(
        "/api/auth/login",
        json={"username": cfg.staff_username, "password": cfg.staff_password},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def mock_openai(monkeypatch):
    """Replace _make_openai_client with a stub returning a canned reply."""
    captured: dict = {}

    def _fake_client_factory():
        client_obj = MagicMock()

        async def _create(**kwargs):
            captured["call"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Mock answer.", tool_calls=None,
                        )
                    )
                ]
            )

        client_obj.chat.completions.create = _create
        return client_obj

    from api.routes import chat as chat_mod
    monkeypatch.setattr(chat_mod, "_make_openai_client", _fake_client_factory)
    return captured


async def test_post_chat_creates_conversation_and_returns_reply(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "Summarize this period.", "period": "week"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation_id"] > 0
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "Mock answer."


async def test_post_chat_persists_user_and_assistant_messages(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "What was the busiest day?"},
    )
    cid = r.json()["conversation_id"]
    full = await client.get(
        f"/api/analytics/chat/conversations/{cid}", headers=h
    )
    msgs = full.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "What was the busiest day?"
    assert msgs[1]["content"] == "Mock answer."


async def test_post_chat_appends_to_existing_conversation(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h, json={"message": "first"}
    )
    cid = r.json()["conversation_id"]
    await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"conversation_id": cid, "message": "second"},
    )
    full = await client.get(
        f"/api/analytics/chat/conversations/{cid}", headers=h
    )
    contents = [m["content"] for m in full.json()["messages"]]
    assert contents == ["first", "Mock answer.", "second", "Mock answer."]


async def test_post_chat_caps_history_at_8(client, db, mock_openai):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h, json={"message": "m1"}
    )
    cid = r.json()["conversation_id"]
    for i in range(2, 11):
        await client.post(
            "/api/analytics/chat",
            headers=h,
            json={"conversation_id": cid, "message": f"m{i}"},
        )
    sent_messages = mock_openai["call"]["messages"]
    assert len(sent_messages) <= 10  # 1 system + up to 8 history + 1 latest
    user_contents = [m["content"] for m in sent_messages if m["role"] == "user"]
    assert "m1" not in user_contents


async def test_post_chat_includes_analytics_in_system_prompt(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "anything", "period": "week"},
    )
    sys_msg = mock_openai["call"]["messages"][0]
    assert sys_msg["role"] == "system"
    assert "period: week" in sys_msg["content"]
    assert "data:" in sys_msg["content"]


async def test_chat_system_prompt_contains_colleges(
    client, db, mock_openai
):
    """Sanity check that the analytics blob fed to the model includes
    the colleges dimension so the chatbot can answer college questions."""
    h = await _admin_headers(client)
    res = await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "ignored, mock returns canned reply"},
    )
    assert res.status_code == 200
    sent_messages = mock_openai["call"]["messages"]
    system_prompt = next(
        m["content"] for m in sent_messages if m["role"] == "system"
    )
    assert "\"colleges\"" in system_prompt


async def test_chat_system_prompt_contains_avg_rating(
    client, db, mock_openai
):
    """The analytics blob fed to the model must include avg_rating
    so the chatbot can answer 'which machine has the highest rating?'."""
    h = await _admin_headers(client)
    res = await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "ignored, mock returns canned reply"},
    )
    assert res.status_code == 200
    sent_messages = mock_openai["call"]["messages"]
    system_prompt = next(
        m["content"] for m in sent_messages if m["role"] == "system"
    )
    assert "\"avg_rating\"" in system_prompt


async def test_post_chat_requires_staff(client, db):
    r = await client.post("/api/analytics/chat", json={"message": "hi"})
    assert r.status_code == 401


async def test_post_chat_rejects_empty_message(client, db, mock_openai):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h, json={"message": "   "}
    )
    assert r.status_code == 400


async def test_get_models_returns_allowlist(client, db):
    h = await _admin_headers(client)
    r = await client.get("/api/analytics/chat/models", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "gpt-5.4-mini"
    ids = [m["id"] for m in body["models"]]
    assert ids == ["gpt-5.4", "gpt-5.4-mini", "gpt-4o"]


async def test_post_chat_uses_default_model_when_unspecified(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    await client.post(
        "/api/analytics/chat", headers=h, json={"message": "hi"}
    )
    assert mock_openai["call"]["model"] == "gpt-5.4-mini"


async def test_post_chat_passes_through_allowed_model(
    client, db, mock_openai
):
    h = await _admin_headers(client)
    await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "hi", "model": "gpt-4o"},
    )
    assert mock_openai["call"]["model"] == "gpt-4o"


async def test_post_chat_rejects_unknown_model(client, db, mock_openai):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat",
        headers=h,
        json={"message": "hi", "model": "gpt-evil"},
    )
    assert r.status_code == 400


async def test_post_chat_503_when_openai_key_missing(client, db, monkeypatch):
    from api.routes import chat as chat_mod
    monkeypatch.setattr(chat_mod, "_make_openai_client", lambda: None)
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h, json={"message": "hi"}
    )
    assert r.status_code == 503


# ── Cross-user isolation ─────────────────────────────────────────────────


async def _seed_eve(db, client):
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("eve", hash_password("pw"), "staff"),
    )
    await db.commit()
    r = await client.post(
        "/api/auth/login", json={"username": "eve", "password": "pw"}
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_list_conversations_only_returns_own(client, db, mock_openai):
    h_admin = await _admin_headers(client)
    await client.post(
        "/api/analytics/chat", headers=h_admin, json={"message": "admin q"}
    )

    h_eve = await _seed_eve(db, client)
    eve_list = await client.get(
        "/api/analytics/chat/conversations", headers=h_eve
    )
    assert eve_list.status_code == 200
    assert eve_list.json() == []


async def test_get_conversation_404_for_other_owner(client, db, mock_openai):
    h_admin = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h_admin, json={"message": "secret"}
    )
    cid = r.json()["conversation_id"]

    h_eve = await _seed_eve(db, client)
    r = await client.get(
        f"/api/analytics/chat/conversations/{cid}", headers=h_eve
    )
    assert r.status_code == 404


async def test_delete_conversation_owner_succeeds(client, db, mock_openai):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h, json={"message": "delete me"}
    )
    cid = r.json()["conversation_id"]

    r = await client.delete(
        f"/api/analytics/chat/conversations/{cid}", headers=h
    )
    assert r.status_code == 200

    r = await client.get(
        f"/api/analytics/chat/conversations/{cid}", headers=h
    )
    assert r.status_code == 404


@pytest.fixture
def mock_openai_stream(monkeypatch):
    """Stub OpenAI client that returns an async-iterable stream of deltas."""
    captured: dict = {}

    def _fake_factory():
        client_obj = MagicMock()

        async def _create(**kwargs):
            captured["call"] = kwargs
            assert kwargs.get("stream") is True

            async def _aiter():
                for piece in ["Hello", ", ", "world", "."]:
                    yield SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content=piece)
                            )
                        ]
                    )

            class _Stream:
                def __aiter__(self):
                    return _aiter()

            return _Stream()

        client_obj.chat.completions.create = _create
        return client_obj

    from api.routes import chat as chat_mod
    monkeypatch.setattr(chat_mod, "_make_openai_client", _fake_factory)
    return captured


async def test_chat_stream_emits_sse_events_and_persists(
    client, db, mock_openai_stream
):
    h = await _admin_headers(client)
    async with client.stream(
        "POST",
        "/api/analytics/chat/stream",
        headers=h,
        json={"message": "hi", "period": "week"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert "\"type\": \"meta\"" in text
    assert "\"type\": \"delta\"" in text
    assert "Hello" in text
    assert "\"type\": \"done\"" in text

    # The full message was persisted at end-of-stream.
    convs = await client.get(
        "/api/analytics/chat/conversations", headers=h
    )
    cid = convs.json()[0]["id"]
    full = await client.get(
        f"/api/analytics/chat/conversations/{cid}", headers=h
    )
    msgs = full.json()["messages"]
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "Hello, world."


async def test_delete_conversation_404_for_other_owner(client, db, mock_openai):
    h_admin = await _admin_headers(client)
    r = await client.post(
        "/api/analytics/chat", headers=h_admin, json={"message": "x"}
    )
    cid = r.json()["conversation_id"]

    h_eve = await _seed_eve(db, client)
    r = await client.delete(
        f"/api/analytics/chat/conversations/{cid}", headers=h_eve
    )
    assert r.status_code == 404
