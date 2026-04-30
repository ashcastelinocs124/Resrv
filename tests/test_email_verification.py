"""Tests for the email-verification service helpers."""

from __future__ import annotations

import pytest

from bot import email_verification as ev
from db import models
from db.database import get_db

pytestmark = pytest.mark.asyncio


async def _seed_user(discord_id: str = "100") -> dict:
    return await models.get_or_create_user(
        discord_id=discord_id, discord_name=discord_id
    )


async def test_issue_code_returns_six_digit_string(db):
    code = await ev.issue_code("100", "alice@illinois.edu")
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isdigit()


async def test_new_code_invalidates_previous(db):
    first = await ev.issue_code("100", "alice@illinois.edu")
    second = await ev.issue_code("100", "alice@illinois.edu")
    assert first != second
    ok, _ = await ev.verify_code("100", first)
    assert ok is False
    ok, email = await ev.verify_code("100", second)
    assert ok is True
    assert email == "alice@illinois.edu"


async def test_verify_expired_code_returns_false(db):
    code = await ev.issue_code("100", "alice@illinois.edu")
    conn = await get_db()
    await conn.execute(
        "UPDATE verification_codes "
        "SET expires_at = datetime('now', '-1 minute') "
        "WHERE discord_id = ?",
        ("100",),
    )
    await conn.commit()
    ok, email = await ev.verify_code("100", code)
    assert ok is False
    assert email is None


async def test_verify_wrong_code_5_times_invalidates_row(db):
    code = await ev.issue_code("100", "alice@illinois.edu")
    for _ in range(5):
        ok, _ = await ev.verify_code("100", "000000")
        assert ok is False
    # Even the correct code now fails because the row is locked.
    ok, _ = await ev.verify_code("100", code)
    assert ok is False


async def test_rate_limit_raises_after_5_codes_in_hour(db):
    for _ in range(5):
        await ev.issue_code("100", "alice@illinois.edu")
    with pytest.raises(ev.VerificationRateLimitError):
        await ev.issue_code("100", "alice@illinois.edu")


async def test_mark_user_verified_sets_columns(db):
    user = await _seed_user("200")
    await ev.mark_user_verified(user["id"], "bob@illinois.edu")
    conn = await get_db()
    cur = await conn.execute(
        "SELECT verified, email, verified_at FROM users WHERE id = ?",
        (user["id"],),
    )
    row = await cur.fetchone()
    assert row["verified"] == 1
    assert row["email"] == "bob@illinois.edu"
    assert row["verified_at"] is not None


async def test_send_email_raises_when_smtp_not_configured(db, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "smtp_username", "")
    with pytest.raises(ev.EmailSendError):
        await ev.send_verification_email("alice@illinois.edu", "123456")
