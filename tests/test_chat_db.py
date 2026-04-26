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
