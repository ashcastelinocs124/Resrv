"""Tests for chat schema + DB helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_migration_creates_chat_conversations_table(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='chat_conversations'"
    )
    assert await cursor.fetchone() is not None


async def test_migration_creates_chat_messages_table(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='chat_messages'"
    )
    assert await cursor.fetchone() is not None


async def test_chat_messages_index_exists(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_chat_msgs_conv'"
    )
    assert await cursor.fetchone() is not None


async def test_chat_messages_role_check_constraint(db):
    cursor = await db.execute("SELECT id FROM staff_users LIMIT 1")
    staff_id = (await cursor.fetchone())["id"]
    cursor = await db.execute(
        "INSERT INTO chat_conversations (staff_user_id) VALUES (?) RETURNING id",
        (staff_id,),
    )
    conv_id = (await cursor.fetchone())["id"]
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO chat_messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            (conv_id, "garbage_role", "x"),
        )


async def test_create_conversation_returns_row_with_id(db):
    from db import models
    staff = (await models.list_staff())[0]
    conv = await models.create_conversation(
        staff_user_id=staff["id"], first_message="What was the busiest day?"
    )
    assert conv["id"] > 0
    assert conv["staff_user_id"] == staff["id"]
    assert conv["title"] == "What was the busiest day?"


async def test_create_conversation_truncates_long_title(db):
    from db import models
    staff = (await models.list_staff())[0]
    long_msg = "x" * 200
    conv = await models.create_conversation(
        staff_user_id=staff["id"], first_message=long_msg
    )
    assert len(conv["title"]) <= 60


async def test_append_message_persists_in_order(db):
    from db import models
    staff = (await models.list_staff())[0]
    conv = await models.create_conversation(
        staff_user_id=staff["id"], first_message="hi"
    )
    await models.append_message(conv["id"], role="user", content="first")
    await models.append_message(conv["id"], role="assistant", content="second")
    msgs = await models.get_conversation_messages(
        conv["id"], staff_user_id=staff["id"]
    )
    assert [m["content"] for m in msgs] == ["first", "second"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


async def test_list_conversations_scoped_to_staff(db):
    from db import models
    from api.auth import hash_password

    a = (await models.list_staff())[0]
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("eve", hash_password("pw"), "staff"),
    )
    await db.commit()
    b = await models.get_staff_by_username("eve")

    await models.create_conversation(staff_user_id=a["id"], first_message="A1")
    await models.create_conversation(staff_user_id=b["id"], first_message="B1")

    a_list = await models.list_conversations(a["id"])
    b_list = await models.list_conversations(b["id"])
    assert all(c["title"].startswith("A") for c in a_list)
    assert all(c["title"].startswith("B") for c in b_list)


async def test_get_conversation_returns_none_for_other_owner(db):
    from db import models
    from api.auth import hash_password

    a = (await models.list_staff())[0]
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("eve", hash_password("pw"), "staff"),
    )
    await db.commit()
    b = await models.get_staff_by_username("eve")

    conv = await models.create_conversation(
        staff_user_id=a["id"], first_message="secret"
    )
    assert (
        await models.get_conversation_messages(conv["id"], staff_user_id=b["id"])
    ) is None


async def test_delete_conversation_only_for_owner(db):
    from db import models
    from api.auth import hash_password

    a = (await models.list_staff())[0]
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) VALUES (?, ?, ?)",
        ("eve", hash_password("pw"), "staff"),
    )
    await db.commit()
    b = await models.get_staff_by_username("eve")

    conv = await models.create_conversation(
        staff_user_id=a["id"], first_message="x"
    )
    assert await models.delete_conversation(conv["id"], staff_user_id=b["id"]) is False
    assert await models.delete_conversation(conv["id"], staff_user_id=a["id"]) is True


async def test_get_recent_messages_caps_at_limit(db):
    from db import models
    staff = (await models.list_staff())[0]
    conv = await models.create_conversation(
        staff_user_id=staff["id"], first_message="x"
    )
    for i in range(10):
        await models.append_message(conv["id"], role="user", content=f"m{i}")
    recent = await models.get_recent_messages(conv["id"], limit=8)
    assert len(recent) == 8
    assert recent[0]["content"] == "m2"
    assert recent[-1]["content"] == "m9"


async def test_messages_cascade_when_conversation_deleted(db):
    cursor = await db.execute("SELECT id FROM staff_users LIMIT 1")
    staff_id = (await cursor.fetchone())["id"]
    cursor = await db.execute(
        "INSERT INTO chat_conversations (staff_user_id) VALUES (?) RETURNING id",
        (staff_id,),
    )
    conv_id = (await cursor.fetchone())["id"]
    await db.execute(
        "INSERT INTO chat_messages (conversation_id, role, content) "
        "VALUES (?, 'user', 'hi')",
        (conv_id,),
    )
    await db.commit()

    await db.execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
    await db.commit()

    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM chat_messages WHERE conversation_id = ?",
        (conv_id,),
    )
    assert (await cursor.fetchone())["cnt"] == 0
