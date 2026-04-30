"""Tests for the queue-channel lock that keeps the dashboard at the bottom."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.bot import ReservBot
from config import settings


def _make_bot_with_channel(
    *, is_text: bool = True
) -> tuple[ReservBot, MagicMock]:
    bot = ReservBot.__new__(ReservBot)
    channel = MagicMock(spec=discord.TextChannel) if is_text else MagicMock()
    channel.id = 12345
    default_role = MagicMock()
    channel.guild = MagicMock()
    channel.guild.default_role = default_role
    channel.set_permissions = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot, channel


@pytest.mark.asyncio
async def test_lock_sets_default_role_perms(monkeypatch):
    monkeypatch.setattr(settings, "lock_queue_channel", True)
    monkeypatch.setattr(settings, "queue_channel_id", 12345)
    bot, channel = _make_bot_with_channel()

    await bot._lock_queue_channel()

    channel.set_permissions.assert_awaited_once_with(
        channel.guild.default_role,
        send_messages=False,
        add_reactions=False,
        send_messages_in_threads=False,
    )


@pytest.mark.asyncio
async def test_lock_skipped_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "lock_queue_channel", False)
    monkeypatch.setattr(settings, "queue_channel_id", 12345)
    bot, channel = _make_bot_with_channel()

    await bot._lock_queue_channel()

    channel.set_permissions.assert_not_called()


@pytest.mark.asyncio
async def test_lock_swallows_forbidden(monkeypatch):
    monkeypatch.setattr(settings, "lock_queue_channel", True)
    monkeypatch.setattr(settings, "queue_channel_id", 12345)
    bot, channel = _make_bot_with_channel()
    channel.set_permissions.side_effect = discord.Forbidden(
        MagicMock(status=403, reason="forbidden"), "missing perms"
    )

    # Should not raise
    await bot._lock_queue_channel()

    channel.set_permissions.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_noop_when_channel_missing(monkeypatch):
    monkeypatch.setattr(settings, "lock_queue_channel", True)
    monkeypatch.setattr(settings, "queue_channel_id", 12345)
    bot = ReservBot.__new__(ReservBot)
    bot.get_channel = MagicMock(return_value=None)

    await bot._lock_queue_channel()  # no exception, no work


@pytest.mark.asyncio
async def test_lock_noop_when_channel_not_text(monkeypatch):
    monkeypatch.setattr(settings, "lock_queue_channel", True)
    monkeypatch.setattr(settings, "queue_channel_id", 12345)
    bot, channel = _make_bot_with_channel(is_text=False)

    await bot._lock_queue_channel()

    channel.set_permissions.assert_not_called()
