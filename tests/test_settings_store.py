"""Tests for the settings store helper."""

from __future__ import annotations

import pytest

from api.settings_store import (
    get_all_settings,
    get_setting,
    get_setting_bool,
    get_setting_int,
    invalidate_settings_cache,
    set_setting,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_settings_cache()
    yield
    invalidate_settings_cache()


async def test_get_setting_returns_seeded_default(db):
    val = await get_setting("reminder_minutes")
    assert val == "30"


async def test_set_setting_persists_and_invalidates(db):
    await set_setting("reminder_minutes", "45")
    assert await get_setting("reminder_minutes") == "45"


async def test_typed_helpers(db):
    await set_setting("reminder_minutes", "42")
    assert await get_setting_int("reminder_minutes") == 42

    await set_setting("public_mode", "true")
    assert await get_setting_bool("public_mode") is True

    await set_setting("public_mode", "false")
    assert await get_setting_bool("public_mode") is False


async def test_unknown_key_returns_none(db):
    assert await get_setting("does_not_exist") is None


async def test_get_all_settings(db):
    result = await get_all_settings()
    assert "reminder_minutes" in result
    assert "public_mode" in result
