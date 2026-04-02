"""Autonomous FIFO queue agent -- runs as a discord.py background task."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from config import settings
from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)

# Module-level reference so we can start/stop cleanly.
_bot: ReservBot | None = None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def start_agent(bot: ReservBot) -> None:
    """Start the agent loop, binding it to the given bot instance."""
    global _bot
    _bot = bot
    if not _agent_tick.is_running():
        _agent_tick.start()
        log.info("Queue agent started (tick every %ds)", settings.agent_tick_seconds)


def stop_agent() -> None:
    """Stop the agent loop gracefully."""
    global _bot
    if _agent_tick.is_running():
        _agent_tick.cancel()
        log.info("Queue agent stopped")
    _bot = None


# --------------------------------------------------------------------------- #
# Core loop
# --------------------------------------------------------------------------- #

@tasks.loop(seconds=settings.agent_tick_seconds)
async def _agent_tick() -> None:
    """Single tick of the queue agent.

    For every active machine:
    1. If nobody is serving and someone is waiting  -> advance queue.
    2. Check for 30-min reminders                   -> DM the user.
    3. Check for grace-period expiry                -> auto-complete as no_show.
    4. Daily reset of stale entries.
    """
    try:
        await _process_machines()
        await _send_reminders()
        await _expire_grace_period()
        await _daily_reset()
    except Exception:
        log.exception("Agent tick failed")


@_agent_tick.before_loop
async def _before_agent_tick() -> None:
    """Wait until the bot is fully ready before starting the loop."""
    if _bot is not None:
        await _bot.wait_until_ready()


# --------------------------------------------------------------------------- #
# Per-machine queue advancement
# --------------------------------------------------------------------------- #

async def _process_machines() -> None:
    """For each active machine, advance the queue if no one is being served."""
    machines = await models.get_machines()
    for machine in machines:
        if machine["status"] != "active":
            continue

        serving = await models.get_serving_entry(machine["id"])
        if serving is not None:
            continue  # someone is already being served

        next_entry = await models.get_next_waiting(machine["id"])
        if next_entry is None:
            continue  # queue is empty

        await models.update_entry_status(next_entry["id"], "serving")
        log.info(
            "Advanced queue: %s now serving on %s",
            next_entry["discord_name"],
            machine["name"],
        )

        # DM the user
        await _dm_user(
            next_entry["discord_id"],
            f"You're up! Head to the **{machine['name']}** now. "
            f"You'll receive a reminder after {settings.reminder_minutes} minutes.",
        )

        # Update pinned embed
        if _bot is not None:
            await _bot.update_queue_embeds(machine["id"])


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #

async def _send_reminders() -> None:
    """DM users who have been serving longer than ``reminder_minutes``."""
    entries = await models.get_entries_needing_reminder(settings.reminder_minutes)
    for entry in entries:
        await models.mark_reminded(entry["id"])
        await _dm_user(
            entry["discord_id"],
            f"You've been using the machine for {settings.reminder_minutes} "
            f"minutes. Still working? If you don't respond within "
            f"{settings.grace_minutes} minutes you'll be marked as finished.",
        )
        log.info(
            "Sent reminder to %s (entry %d)", entry["discord_name"], entry["id"]
        )


# --------------------------------------------------------------------------- #
# Grace period expiry
# --------------------------------------------------------------------------- #

async def _expire_grace_period() -> None:
    """Auto-complete entries that were reminded but didn't respond in time."""
    entries = await models.get_entries_past_grace(
        settings.reminder_minutes, settings.grace_minutes
    )
    for entry in entries:
        await models.update_entry_status(entry["id"], "no_show")
        await _dm_user(
            entry["discord_id"],
            "Your session has been automatically ended because the grace period "
            "expired. If this was a mistake, please talk to staff.",
        )
        log.info(
            "Auto no-show for %s (entry %d)", entry["discord_name"], entry["id"]
        )

        # Update pinned embed for this machine
        if _bot is not None:
            await _bot.update_queue_embeds(entry["machine_id"])


# --------------------------------------------------------------------------- #
# Daily reset
# --------------------------------------------------------------------------- #

async def _daily_reset() -> None:
    """Cancel stale entries from previous days.

    Called every tick but ``reset_stale_queues`` is idempotent -- it only
    affects entries with ``joined_at`` before today.
    """
    count = await models.reset_stale_queues()
    if count > 0:
        log.info("Daily reset: cancelled %d stale entries", count)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _dm_user(discord_id: str, message: str) -> None:
    """Best-effort DM to a user by their Discord ID."""
    if _bot is None:
        return
    try:
        user = await _bot.fetch_user(int(discord_id))
        await user.send(message)
    except discord.NotFound:
        log.warning("User %s not found -- cannot DM", discord_id)
    except discord.Forbidden:
        log.warning("User %s has DMs disabled -- cannot DM", discord_id)
    except Exception:
        log.exception("Failed to DM user %s", discord_id)
