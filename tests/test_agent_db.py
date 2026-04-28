"""DB-layer tests for agent_conversations + agent_messages."""
import json
import pytest
from db import models
from api.auth import hash_password

pytestmark = pytest.mark.asyncio


async def _seed_staff(username: str = "agent-user") -> int:
    return (await models.create_staff(
        username, hash_password("x"), "admin"
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


async def test_append_tool_message_with_tool_call_id(db):
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
