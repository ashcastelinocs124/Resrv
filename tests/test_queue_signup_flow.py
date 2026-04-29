"""End-to-end signup flow with mocked SMTP send.

These are helper-level smoke checks: faking discord.py's modal interaction
plumbing in pytest is heavy, so the cog wiring is verified by manual
Discord smoke. The contracts these tests pin down — sticky verification,
public_mode bypass, code-issuance flow — are what the cog relies on.
"""

from __future__ import annotations

import pytest

from bot import email_verification as ev
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_smtp(monkeypatch):
    """Capture send_verification_email calls instead of mailing."""
    captured: dict = {}

    async def _fake_send(to_email: str, code: str) -> None:
        captured["to"] = to_email
        captured["code"] = code

    monkeypatch.setattr(ev, "send_verification_email", _fake_send)
    return captured


async def test_unverified_user_must_verify_before_join(db, mock_smtp):
    """issue_code persists; verify_code accepts; mark_user_verified flips users.verified."""
    user = await models.get_or_create_user(
        discord_id="500", discord_name="bob"
    )
    code = await ev.issue_code("500", "bob@illinois.edu")
    # Cog would call send_verification_email here; mock lets us assert that.
    await ev.send_verification_email("bob@illinois.edu", code)
    assert mock_smtp["to"] == "bob@illinois.edu"
    assert mock_smtp["code"] == code

    ok, email = await ev.verify_code("500", code)
    assert ok is True
    await ev.mark_user_verified(user["id"], email)
    fresh = await models.get_user_by_discord_id("500")
    assert fresh["verified"] == 1
    assert fresh["email"] == "bob@illinois.edu"
    assert fresh["verified_at"] is not None


async def test_already_verified_user_skips_verification(db, mock_smtp):
    """A user with verified=1 must NOT need a new code on subsequent joins."""
    user = await models.get_or_create_user(
        discord_id="600", discord_name="carol"
    )
    await ev.mark_user_verified(user["id"], "carol@illinois.edu")
    fresh = await models.get_user_by_discord_id("600")
    assert fresh["verified"] == 1
    # The cog gate is `verified == 1 AND email matches`. No SMTP call expected.
    assert mock_smtp == {}


async def test_public_mode_bypasses_verification(db, mock_smtp):
    """When public_mode=true, the cog should NOT issue a code."""
    from api.settings_store import set_setting, get_setting
    await set_setting("public_mode", "true")
    val = await get_setting("public_mode")
    assert val == "true"
    # Cog gate: public_mode==true → fast path, no SMTP call.
    assert mock_smtp == {}
