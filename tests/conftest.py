"""Shared test fixtures — in-memory SQLite, monkeypatched config."""

from __future__ import annotations

import pytest
import aiosqlite

import db.database as database_mod
from config import settings


@pytest.fixture(autouse=True)
def _use_in_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force all tests to use ':memory:' instead of a real file."""
    monkeypatch.setattr(settings, "database_path", ":memory:")
    # Settings cache is module-level; in-memory DB resets per test, so the
    # cache must reset too or earlier-test writes leak through as stale reads.
    from api import settings_store
    settings_store.invalidate_settings_cache()


@pytest.fixture
async def db() -> aiosqlite.Connection:
    """Initialise a fresh in-memory database for each test."""
    conn = await database_mod.init_db()
    yield conn
    await database_mod.close_db()


@pytest.fixture
async def registered_user_in_college(db) -> int:
    """Create a college named 'Has Users' and register one user against it."""
    from db import models

    college = await models.create_college("Has Users")
    user = await models.get_or_create_user(discord_id="9000", discord_name="x")
    await models.register_user(
        user["id"],
        full_name="X",
        email="x@illinois.edu",
        major="CS",
        college_id=college["id"],
        graduation_year="2027",
    )
    return college["id"]
