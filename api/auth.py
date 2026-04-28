"""Staff authentication: PBKDF2 password hashing + HMAC-signed tokens.

Stdlib only — no external crypto deps. Tokens encode ``staff_id`` and an
expiry timestamp, signed with ``settings.auth_secret``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from config import settings
from db.database import get_db

_PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALGO = "sha256"
_SALT_BYTES = 16


# ── Password hashing ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return ``pbkdf2$<iters>$<salt_b64>$<hash_b64>``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
    expected = base64.urlsafe_b64decode(hash_b64 + "=" * (-len(hash_b64) % 4))
    got = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, int(iters_s)
    )
    return hmac.compare_digest(got, expected)


# ── Signed tokens ────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(staff_id: int, username: str, role: str = "staff") -> str:
    payload = {
        "sub": staff_id,
        "usr": username,
        "rol": role,
        "exp": int(time.time()) + settings.auth_token_ttl_hours * 3600,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(
        settings.auth_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body}.{_b64url(sig)}"


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(
        settings.auth_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64url_decode(sig), expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# ── FastAPI dependency ───────────────────────────────────────────────────

async def require_staff(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload


async def require_admin(
    payload: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    if payload.get("rol") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return payload


async def require_data_analyst(
    payload: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    """Gate for /api/analytics/agent/*.

    503 when the master flag is off; 403 when staff visibility is off and the
    caller isn't an admin; otherwise returns the JWT payload unchanged.
    """
    from api.settings_store import get_setting_bool

    if not await get_setting_bool("data_analyst_enabled", default=False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data-analyst agent not enabled",
        )
    if payload.get("rol") == "admin":
        return payload
    if not await get_setting_bool(
        "data_analyst_visible_to_staff", default=False
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not available to staff",
        )
    return payload


# ── Staff user helpers ───────────────────────────────────────────────────

async def get_staff_by_username(username: str) -> dict[str, Any] | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM staff_users WHERE username = ?", (username,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None
