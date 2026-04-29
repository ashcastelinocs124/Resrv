"""Email-verification service — Gmail SMTP + ``verification_codes`` table.

Public surface:
    - ``issue_code(discord_id, email)`` -> ``str``
    - ``send_verification_email(to_email, code)`` -> ``None``
    - ``verify_code(discord_id, code)`` -> ``tuple[bool, str | None]``
    - ``mark_user_verified(user_id, email)`` -> ``None``

Lazy SMTP — missing creds raise ``EmailSendError`` instead of crashing on
import. Mirrors the OpenAI client factory pattern in
``api/routes/chat.py::_make_openai_client`` (learnings.md 2026-04-26).
"""

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
    """Send the 6-digit code over Gmail STARTTLS:587. Raises ``EmailSendError``."""
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

    Raises ``VerificationRateLimitError`` if the discord_id has issued more
    than ``settings.verification_max_codes_per_hour`` codes in the last hour.
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

    On a wrong attempt, increments the row's ``attempts`` counter; once it
    hits ``MAX_WRONG_ATTEMPTS`` the row is permanently invalidated (used=1).
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
    """Stamp ``users.verified=1``, store the verified email + timestamp."""
    db = await get_db()
    await db.execute(
        "UPDATE users SET verified = 1, email = ?, "
        "verified_at = datetime('now') WHERE id = ?",
        (email, user_id),
    )
    await db.commit()
