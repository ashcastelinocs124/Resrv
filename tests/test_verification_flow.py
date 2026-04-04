"""Tests for the DM verification flow."""

from __future__ import annotations

import pytest

from config import settings
from db import models
from db.database import init_db, close_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db():
    conn = await init_db()
    yield conn
    await close_db()


async def test_is_email_detects_illinois_edu(db):
    """Email regex matches @illinois.edu addresses."""
    from bot.cogs.dm import _is_illinois_email
    assert _is_illinois_email("netid@illinois.edu") is True
    assert _is_illinois_email("NETID@ILLINOIS.EDU") is True
    assert _is_illinois_email("user@gmail.com") is False
    assert _is_illinois_email("hello world") is False
    assert _is_illinois_email("user@uillinois.edu") is False


async def test_is_verification_code_format(db):
    """6-digit string detection."""
    from bot.cogs.dm import _is_verification_code
    assert _is_verification_code("123456") is True
    assert _is_verification_code("12345") is False
    assert _is_verification_code("1234567") is False
    assert _is_verification_code("abcdef") is False
    assert _is_verification_code("  123456  ") is True


async def test_start_verification_creates_code(db):
    """Providing an email creates a verification code in the DB."""
    user = await models.get_or_create_user("vtest1", "VTest1")
    code_row = await models.create_verification_code("vtest1", "test@illinois.edu")
    assert len(code_row["code"]) == 6
    found = await models.verify_code("vtest1", code_row["code"])
    assert found is not None


async def test_complete_verification_marks_user(db):
    """Submitting correct code marks user as verified."""
    user = await models.get_or_create_user("vtest2", "VTest2")
    code_row = await models.create_verification_code("vtest2", "v@illinois.edu")
    found = await models.verify_code("vtest2", code_row["code"])
    assert found is not None
    await models.mark_code_used(found["id"])
    await models.mark_user_verified(user["id"], found["email"])
    updated = await models.get_user_by_discord_id("vtest2")
    assert updated["verified"] == 1
    assert updated["email"] == "v@illinois.edu"


async def test_wrong_code_rejected(db):
    """Wrong code does not verify the user."""
    user = await models.get_or_create_user("vtest3", "VTest3")
    await models.create_verification_code("vtest3", "w@illinois.edu")
    found = await models.verify_code("vtest3", "000000")
    assert found is None
    updated = await models.get_user_by_discord_id("vtest3")
    assert updated["verified"] == 0


async def test_unverified_user_blocked_when_public_mode_off(db, monkeypatch):
    """Unverified user cannot join queue when public_mode=False."""
    monkeypatch.setattr(settings, "public_mode", False)
    user = await models.get_or_create_user("block1", "BlockUser")
    assert (await models.get_user_by_discord_id("block1"))["verified"] == 0

    from bot.cogs.queue import _requires_verification
    assert _requires_verification(user) is True


async def test_verified_user_allowed_when_public_mode_off(db, monkeypatch):
    """Verified user can join queue when public_mode=False."""
    monkeypatch.setattr(settings, "public_mode", False)
    user = await models.get_or_create_user("allow1", "AllowUser")
    await models.mark_user_verified(user["id"], "allow@illinois.edu")
    user = await models.get_user_by_discord_id("allow1")

    from bot.cogs.queue import _requires_verification
    assert _requires_verification(user) is False


async def test_unverified_user_allowed_when_public_mode_on(db, monkeypatch):
    """Unverified user can join queue when public_mode=True."""
    monkeypatch.setattr(settings, "public_mode", True)
    user = await models.get_or_create_user("pub1", "PubUser")

    from bot.cogs.queue import _requires_verification
    assert _requires_verification(user) is False
