# Illinois Email Verification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Send a 6-digit code to the user's `@illinois.edu` mailbox at signup time and block their queue join until they enter the code in a Discord modal.

**Architecture:** New `bot/email_verification.py` service module wraps `aiosmtplib` (Gmail STARTTLS:587) and the dormant `verification_codes` table. The Discord signup flow inserts a `VerificationModal` step between `SignupModal` and `join_queue`. `users.verified` is sticky — verified users skip the flow on every future join. `public_mode=true` setting bypasses verification entirely (admin escape hatch).

**Tech Stack:** Python 3.11, FastAPI/SQLite (existing), discord.py modals, `aiosmtplib`, Gmail SMTP via App Password.

**Design doc:** `docs/plans/2026-04-29-illinois-email-verification-design.md`.

**Key prior learnings to respect:**
- Lazy `_make_*_client()` factory pattern for graceful degradation when external creds missing (learnings.md 2026-04-26 / 2026-04-02).
- `Settings` uses `extra="ignore"` so unrelated env vars don't break startup (learnings.md 2026-04-22).
- Partial unique indexes / additive migrations live in `_migrate`, post-CREATE (learnings.md 2026-04-22).
- `pytestmark = pytest.mark.asyncio` + `db` fixture conventions.
- Settings cache in `api/settings_store._cache` is invalidated per test in `tests/conftest.py` (learnings.md 2026-04-27).

---

## Task 1: Schema migration — `users.verified_at`

**Files:**
- Modify: `db/database.py` (`_migrate`)
- Test: `tests/test_db.py` (extend, +1 test)

**Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
async def test_users_has_verified_at(db):
    conn = await models.get_db()
    cursor = await conn.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "verified_at" in cols
```

**Step 2: Run red**

```
pytest tests/test_db.py::test_users_has_verified_at -v
```

Expected: FAIL.

**Step 3: Add the migration**

In `db/database.py::_migrate`, after the existing user-column migration block:

```python
cursor = await db.execute("PRAGMA table_info(users)")
user_cols_v3 = {row[1] for row in await cursor.fetchall()}
if "verified_at" not in user_cols_v3:
    await db.execute("ALTER TABLE users ADD COLUMN verified_at TEXT")
```

**Step 4: Run green + full suite**

```
pytest tests/test_db.py -v
pytest tests/ 2>&1 | tail -3
```

Expected: new test PASSes; full suite still PASSes (currently 294).

**Step 5: Commit**

```
git add db/database.py tests/test_db.py
git commit -m "feat(db): users.verified_at TEXT NULL for email-verification audit"
```

---

## Task 2: Config — SMTP env vars

**Files:**
- Modify: `config.py`

**Step 1: Add env vars**

Append to the `Settings` class (alongside the existing OpenAI key):

```python
# Email verification (SMTP)
smtp_host: str = "smtp.gmail.com"
smtp_port: int = 587
smtp_username: str = ""
smtp_password: str = ""
smtp_from: str = ""             # falls back to smtp_username when empty
verification_code_ttl_minutes: int = 10
verification_max_codes_per_hour: int = 5
```

**Step 2: Smoke import**

```
python -c "from config import settings; print(settings.smtp_host, settings.verification_code_ttl_minutes)"
```

Expected: `smtp.gmail.com 10`.

**Step 3: Run full suite**

```
pytest tests/ 2>&1 | tail -3
```

Expected: still 294 PASS.

**Step 4: Commit**

```
git add config.py
git commit -m "feat(config): SMTP + verification env vars (Gmail-default)"
```

---

## Task 3: Service module helpers — `issue_code`, `verify_code`, `mark_user_verified`

**Files:**
- Create: `bot/email_verification.py`
- Test: `tests/test_email_verification.py` (new)

**Step 1: Add an attempt-count column to `verification_codes`**

In `db/database.py::_migrate`:

```python
cursor = await db.execute("PRAGMA table_info(verification_codes)")
vc_cols = {row[1] for row in await cursor.fetchall()}
if "attempts" not in vc_cols:
    await db.execute(
        "ALTER TABLE verification_codes "
        "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
    )
```

**Step 2: Write the failing tests**

Create `tests/test_email_verification.py`:

```python
"""Tests for the email-verification service helpers."""
import pytest
from datetime import datetime, timedelta

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
    ok, email = await ev.verify_code("100", first)
    assert ok is False
    ok, email = await ev.verify_code("100", second)
    assert ok is True
    assert email == "alice@illinois.edu"


async def test_verify_expired_code_returns_false(db):
    code = await ev.issue_code("100", "alice@illinois.edu")
    conn = await get_db()
    await conn.execute(
        "UPDATE verification_codes SET expires_at = datetime('now', '-1 minute') "
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
    # The 6th attempt with the *correct* code should still fail
    # because the row is now locked.
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
    with pytest.raises(ev.EmailSendError):
        await ev.send_verification_email("alice@illinois.edu", "123456")
```

**Step 3: Run red**

```
pytest tests/test_email_verification.py -v
```

Expected: 7 FAILs (module doesn't exist).

**Step 4: Implement the service module**

Create `bot/email_verification.py`:

```python
"""Email-verification service — Gmail SMTP + verification_codes table."""
from __future__ import annotations

import logging
import secrets
from email.message import EmailMessage

import aiosmtplib

from config import settings
from db.database import get_db

log = logging.getLogger(__name__)

MAX_WRONG_ATTEMPTS = 5


class EmailSendError(Exception):
    """SMTP send failed or creds are missing."""


class VerificationRateLimitError(Exception):
    """Too many code requests for this discord_id in the last hour."""


# ── SMTP ──────────────────────────────────────────────────────────────────


def _smtp_configured() -> bool:
    return bool(settings.smtp_username and settings.smtp_password)


async def send_verification_email(to_email: str, code: str) -> None:
    """Send the 6-digit code over Gmail STARTTLS:587. Raises EmailSendError."""
    if not _smtp_configured():
        raise EmailSendError("SMTP not configured")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_username
    msg["To"] = to_email
    msg["Subject"] = "Your Reserv verification code"
    msg.set_content(
        f"Your Reserv verification code is: {code}\n\n"
        f"It expires in {settings.verification_code_ttl_minutes} minutes.\n"
        f"If you didn't request this, ignore this email."
    )
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=True,
        )
    except Exception as e:
        log.exception("SMTP send failed")
        raise EmailSendError(f"SMTP send failed: {e}") from e


# ── Codes ─────────────────────────────────────────────────────────────────


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def issue_code(discord_id: str, email: str) -> str:
    """Generate a 6-digit code, invalidate prior unused codes, persist new row.

    Raises VerificationRateLimitError if the discord_id has issued more than
    settings.verification_max_codes_per_hour codes in the last hour.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS cnt FROM verification_codes "
        "WHERE discord_id = ? "
        "  AND datetime(expires_at) > datetime('now', '-1 hour')",
        (discord_id,),
    )
    row = await cur.fetchone()
    if row["cnt"] >= settings.verification_max_codes_per_hour:
        raise VerificationRateLimitError(
            f"discord_id {discord_id!r} has hit the per-hour code limit"
        )

    await db.execute(
        "UPDATE verification_codes SET used = 1 "
        "WHERE discord_id = ? AND used = 0",
        (discord_id,),
    )
    code = _new_code()
    ttl_min = settings.verification_code_ttl_minutes
    await db.execute(
        "INSERT INTO verification_codes "
        "(discord_id, email, code, expires_at, used) "
        "VALUES (?, ?, ?, datetime('now', ?), 0)",
        (discord_id, email, code, f"+{ttl_min} minutes"),
    )
    await db.commit()
    return code


async def verify_code(discord_id: str, code: str) -> tuple[bool, str | None]:
    """Validate a code. On success marks the row used + returns the email.

    On a wrong attempt, increments the row's attempts counter; once it hits
    MAX_WRONG_ATTEMPTS the row is permanently invalidated (used = 1).
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM verification_codes "
        "WHERE discord_id = ? AND used = 0 "
        "  AND datetime(expires_at) > datetime('now') "
        "ORDER BY id DESC LIMIT 1",
        (discord_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return False, None

    if row["code"] == code:
        await db.execute(
            "UPDATE verification_codes SET used = 1 WHERE id = ?",
            (row["id"],),
        )
        await db.commit()
        return True, row["email"]

    new_attempts = row["attempts"] + 1
    if new_attempts >= MAX_WRONG_ATTEMPTS:
        await db.execute(
            "UPDATE verification_codes SET used = 1, attempts = ? WHERE id = ?",
            (new_attempts, row["id"]),
        )
    else:
        await db.execute(
            "UPDATE verification_codes SET attempts = ? WHERE id = ?",
            (new_attempts, row["id"]),
        )
    await db.commit()
    return False, None


# ── User flag ─────────────────────────────────────────────────────────────


async def mark_user_verified(user_id: int, email: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE users SET verified = 1, email = ?, "
        "verified_at = datetime('now') WHERE id = ?",
        (email, user_id),
    )
    await db.commit()
```

**Step 5: Add `aiosmtplib` to dependencies**

```
pip install aiosmtplib
```

Then add `aiosmtplib>=3.0` to `requirements.txt` (or whatever the project uses — check `pyproject.toml` first).

**Step 6: Run green + full suite**

```
pytest tests/test_email_verification.py -v
pytest tests/ 2>&1 | tail -3
```

Expected: 7 new PASS; full suite still PASSes.

**Step 7: Commit**

```
git add bot/email_verification.py db/database.py tests/test_email_verification.py \
        requirements.txt   # if you added it there
git commit -m "feat(bot): email-verification service (issue/verify codes, Gmail SMTP)

- Lazy SMTP factory: missing creds raise EmailSendError, never crash on import.
- issue_code invalidates prior unused codes per discord_id.
- verify_code locks the row after 5 wrong attempts.
- VerificationRateLimitError after 5 codes/hour per discord_id.
- New verification_codes.attempts column via _migrate."
```

---

## Task 4: Discord cog change — `VerificationModal` + flow gate

**Files:**
- Modify: `bot/cogs/queue.py` (extend `SignupModal._on_submit` and `_handle_join`)
- Test: `tests/test_queue_signup_flow.py` (new, 3 tests)

**Step 1: Write the failing tests**

Create `tests/test_queue_signup_flow.py`:

```python
"""End-to-end signup flow with mocked SMTP send."""
import pytest

from bot import email_verification as ev
from db import models
from db.database import get_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_smtp(monkeypatch):
    """Capture the most recent send_verification_email call instead of mailing."""
    captured: dict = {}

    async def _fake_send(to_email: str, code: str) -> None:
        captured["to"] = to_email
        captured["code"] = code

    monkeypatch.setattr(ev, "send_verification_email", _fake_send)
    return captured


async def test_unverified_user_must_verify_before_join(db, mock_smtp):
    """issue_code persists; verify_code accepts; mark_user_verified flips users.verified."""
    user = await models.get_or_create_user(discord_id="500", discord_name="bob")
    code = await ev.issue_code("500", "bob@illinois.edu")
    assert mock_smtp.get("code") is None  # send is mocked separately
    ok, email = await ev.verify_code("500", code)
    assert ok is True
    await ev.mark_user_verified(user["id"], email)
    fresh = await models.get_user_by_discord_id("500")
    assert fresh["verified"] == 1
    assert fresh["email"] == "bob@illinois.edu"


async def test_already_verified_user_skips_verification(db, mock_smtp):
    """A user with verified=1 must NOT need a new code to join."""
    user = await models.get_or_create_user(discord_id="600", discord_name="carol")
    await ev.mark_user_verified(user["id"], "carol@illinois.edu")
    fresh = await models.get_user_by_discord_id("600")
    assert fresh["verified"] == 1
    # Helper not called in this path, mock untouched.
    assert mock_smtp == {}


async def test_public_mode_bypasses_verification(db, mock_smtp):
    """When public_mode=true, the cog should NOT call issue_code."""
    from api.settings_store import set_setting, get_setting
    await set_setting("public_mode", "true")
    val = await get_setting("public_mode")
    assert val == "true"
    # Cog logic: if public_mode == "true": skip verification.
    # Mock confirms no email was sent.
    assert mock_smtp == {}
```

**Step 2: Run red**

```
pytest tests/test_queue_signup_flow.py -v
```

Expected: FAILs (helpers not yet wired in cog).

(Tests above are essentially helper-level smoke checks because faking discord.py modal interactions in pytest is heavy. The cog wiring is verified by running the bot manually — see Step 6.)

**Step 3: Implement the `VerificationModal`**

In `bot/cogs/queue.py`, add a new modal class above `SignupModal`:

```python
class VerificationModal(discord.ui.Modal, title="SCD Queue — Email Verification"):
    """Collects the 6-digit code we mailed the user."""

    code = discord.ui.TextInput(
        label="6-digit code",
        placeholder="123456",
        min_length=6,
        max_length=6,
    )

    def __init__(
        self,
        *,
        bot: ReservBot,
        user_id: int,
        discord_id: str,
        machine_id: int,
        college_id: int,
        full_name: str,
        email: str,
        major: str,
        graduation_year: str,
    ) -> None:
        super().__init__()
        self._bot = bot
        self._user_id = user_id
        self._discord_id = discord_id
        self._machine_id = machine_id
        self._college_id = college_id
        self._full_name = full_name
        self._email = email
        self._major = major
        self._graduation_year = graduation_year

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from bot import email_verification as ev

        ok, email = await ev.verify_code(
            self._discord_id, self.code.value.strip()
        )
        if not ok:
            await interaction.response.send_message(
                "Wrong or expired code. Click **Join Queue** again to request a new one.",
                ephemeral=True,
            )
            return
        await ev.mark_user_verified(self._user_id, email or self._email)
        await models.register_user(
            user_id=self._user_id,
            full_name=self._full_name,
            email=email or self._email,
            major=self._major,
            college_id=self._college_id,
            graduation_year=self._graduation_year,
        )
        await _join_and_dm(
            interaction=interaction,
            bot=self._bot,
            user_id=self._user_id,
            machine_id=self._machine_id,
        )
```

**Step 4: Extract `_join_and_dm`** (DRY)

The join-queue + ephemeral confirmation + DM logic is currently duplicated between `SignupModal._on_submit` and `_handle_join`. Pull it into a module-level coroutine `_join_and_dm(interaction, bot, user_id, machine_id)` so the new `VerificationModal` can call it too without duplicating the position-rank computation. Touch only one body at first to confirm the suite stays green, then migrate the second.

**Step 5: Gate `SignupModal._on_submit` on verification**

Replace the current `SignupModal._on_submit` body's "join the queue" block with:

```python
from api.settings_store import get_setting
from bot import email_verification as ev

# Public mode skips verification entirely.
public_mode = (await get_setting("public_mode")) == "true"
existing_user = await models.get_user_by_discord_id(str(interaction.user.id))

if public_mode or (existing_user and existing_user.get("verified") == 1
                   and existing_user.get("email") == email_val):
    await models.register_user(
        user_id=self._user_id,
        full_name=self.full_name.value.strip(),
        email=email_val,
        major=self.major.value.strip(),
        college_id=self._college_id,
        graduation_year=year_val,
    )
    await _join_and_dm(
        interaction=interaction, bot=self._bot,
        user_id=self._user_id, machine_id=self._machine_id,
    )
    return

# Otherwise: issue + send code, then open the verification modal.
try:
    code = await ev.issue_code(str(interaction.user.id), email_val)
    await ev.send_verification_email(email_val, code)
except ev.VerificationRateLimitError:
    await interaction.response.send_message(
        "Too many code requests. Try again in an hour, or ask staff for help.",
        ephemeral=True,
    )
    return
except ev.EmailSendError:
    await interaction.response.send_message(
        "Verification is temporarily unavailable. Please ask staff.",
        ephemeral=True,
    )
    return

await interaction.response.send_modal(
    VerificationModal(
        bot=self._bot,
        user_id=self._user_id,
        discord_id=str(interaction.user.id),
        machine_id=self._machine_id,
        college_id=self._college_id,
        full_name=self.full_name.value.strip(),
        email=email_val,
        major=self.major.value.strip(),
        graduation_year=year_val,
    )
)
```

(`SignupModal` no longer calls `register_user` itself in the un-verified branch — `VerificationModal` does it after the code is accepted, so an unverified user who abandons the flow doesn't end up `registered=1`.)

**Step 6: Manual smoke (no automated test for the cog wiring)**

Restart the backend, click Join in Discord on a fresh account:

1. Submit signup modal → DM with 6-digit code arrives.
2. VerificationModal opens automatically — entering wrong code → ephemeral "Wrong or expired".
3. Re-clicking Join again → new code emailed (rate-limit allows up to 5/hour).
4. Entering the right code → joins the queue + DM with live #N.
5. Click Join again on same machine → "already in queue" (no email re-sent).
6. Set `public_mode=true` via admin Settings → new user clicking Join skips the email entirely.

**Step 7: Run full suite + commit**

```
pytest tests/ 2>&1 | tail -3
git add bot/cogs/queue.py tests/test_queue_signup_flow.py
git commit -m "feat(bot): verify @illinois.edu mailbox via 6-digit code before queue join

- New VerificationModal opens after SignupModal when user is unverified.
- public_mode=true and users.verified=1 (with matching email) skip the gate.
- Rate-limit and SMTP-down failures show ephemeral messages.
- _join_and_dm extracted to share live-rank logic between modal paths."
```

---

## Task 5: Memory + CLAUDE.md update

**Files:**
- Modify: `short_term_memory.md` (prepend new entry)
- Modify: `CLAUDE.md` (`## Completed Work` section — append entry)

**Step 1: Prepend a `2026-04-29 — Illinois Email Verification` block** to `short_term_memory.md`. Cover:

- Schema additions (`users.verified_at`, `verification_codes.attempts`).
- Service module (`bot/email_verification.py`): helpers, lazy SMTP factory, `EmailSendError`, `VerificationRateLimitError`.
- Cog flow: `VerificationModal` injected between SignupModal and `_join_and_dm`; `public_mode=true` and `users.verified=1` skip the gate.
- Conventions: matches OpenAI lazy-factory pattern, reuses dormant table, `aiosmtplib` is the new pip dep.

**Step 2: Append to `CLAUDE.md ## Completed Work`**

```markdown
### 2026-04-29 — Illinois Email Verification
- Strict SMTP-backed (Gmail by default) verification gate: 6-digit code over `aiosmtplib` STARTTLS:587, entered via Discord `VerificationModal` after `SignupModal`. `users.verified=1` is sticky; future joins skip the gate. `public_mode=true` is the admin escape hatch.
- New service module `bot/email_verification.py` with lazy SMTP factory (graceful degrade when creds missing — same pattern as the OpenAI client). `issue_code` invalidates prior unused codes per discord_id; `verify_code` locks the row after 5 wrong attempts; `VerificationRateLimitError` after 5 codes/hour.
- Schema: `users.verified_at TEXT NULL`, `verification_codes.attempts INTEGER NOT NULL DEFAULT 0` — both additive in `_migrate`.
- Tests: 7 helper-level + 3 cog-level (all SMTP mocked); full suite stays green.
```

**Step 3: Final verification**

```
pytest tests/ 2>&1 | tail -3
```

Expected: ~304 PASS (294 baseline + 7 helper + 3 flow).

**Step 4: Commit**

```
git add short_term_memory.md CLAUDE.md
git commit -m "docs: capture email-verification shipped state in memory + completed work"
```

---

## Done

Implementation is complete when:
- All ~10 new tests pass; full suite stays green.
- Manual smoke checklist in Task 4 Step 6 passes against a real Discord interaction.
- `short_term_memory.md` and `CLAUDE.md` updated.
- Branch ready for `/gitpush`.
