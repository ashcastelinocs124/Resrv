"""Autonomous FIFO queue agent -- runs as a discord.py background task."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from config import settings
from datetime import datetime, timedelta
from api.settings_store import get_setting_int
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
        await _compute_daily_analytics()
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
    """For each active machine, promote waiting users until capacity is full."""
    machines = await models.get_machines()
    for machine in machines:
        if machine["status"] != "active":
            continue

        capacity = await models.count_active_units(machine["id"])
        if capacity == 0:
            continue

        serving = await models.count_serving_on_machine(machine["id"])
        promoted_any = False
        while serving < capacity:
            next_entry = await models.get_next_waiting(machine["id"])
            if next_entry is None:
                break
            unit = await models.first_available_unit(machine["id"])
            if unit is None:
                break

            await models.update_entry_status(
                next_entry["id"], "serving", unit_id=unit["id"]
            )
            log.info(
                "Advanced queue: %s on %s / %s",
                next_entry["discord_name"], machine["name"], unit["label"],
            )

            unit_suffix = (
                "" if unit["label"] == "Main"
                else f" (use the **{unit['label']}**)"
            )
            reminder_minutes = await get_setting_int(
                "reminder_minutes", settings.reminder_minutes
            )
            await _dm_user(
                next_entry["discord_id"],
                f"You're up! Head to the **{machine['name']}**{unit_suffix} now. "
                f"You'll receive a reminder after {reminder_minutes} minutes.",
            )
            serving += 1
            promoted_any = True

        if promoted_any and _bot is not None:
            await _bot.update_queue_embeds(machine["id"])


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #

async def _send_reminders() -> None:
    """DM users who have been serving longer than ``reminder_minutes``."""
    reminder_minutes = await get_setting_int(
        "reminder_minutes", settings.reminder_minutes
    )
    grace_minutes = await get_setting_int(
        "grace_minutes", settings.grace_minutes
    )
    entries = await models.get_entries_needing_reminder(reminder_minutes)
    for entry in entries:
        await models.mark_reminded(entry["id"])
        await _dm_user(
            entry["discord_id"],
            f"You've been using the machine for {reminder_minutes} "
            f"minutes. Still working? If you don't respond within "
            f"{grace_minutes} minutes you'll be marked as finished.",
        )
        log.info(
            "Sent reminder to %s (entry %d)", entry["discord_name"], entry["id"]
        )


# --------------------------------------------------------------------------- #
# Grace period expiry
# --------------------------------------------------------------------------- #

async def _expire_grace_period() -> None:
    """Auto-complete entries that were reminded but didn't respond in time."""
    reminder_minutes = await get_setting_int(
        "reminder_minutes", settings.reminder_minutes
    )
    grace_minutes = await get_setting_int(
        "grace_minutes", settings.grace_minutes
    )
    entries = await models.get_entries_past_grace(reminder_minutes, grace_minutes)
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
# Daily analytics snapshot
# --------------------------------------------------------------------------- #

_last_snapshot_date: str | None = None


async def _compute_daily_analytics() -> None:
    """Compute and store analytics snapshot for yesterday (once per day)."""
    global _last_snapshot_date

    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    if _last_snapshot_date == yesterday:
        return
    _last_snapshot_date = yesterday

    existing = await models.get_analytics_snapshots(
        start_date=yesterday, end_date=yesterday
    )
    if existing:
        return

    from db.database import get_db

    db = await get_db()

    machines = await models.get_machines()
    for machine in machines:
        mid = machine["id"]

        cursor = await db.execute(
            """
            SELECT
                COUNT(*) as total_jobs,
                SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END) as completed_jobs,
                SUM(CASE WHEN qe.status = 'no_show' THEN 1 ELSE 0 END) as no_show_count,
                SUM(CASE WHEN qe.status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_count,
                SUM(CASE WHEN qe.job_successful = 0 THEN 1 ELSE 0 END) as failure_count,
                COUNT(DISTINCT qe.user_id) as unique_users,
                AVG(CASE
                    WHEN qe.serving_at IS NOT NULL
                    THEN (julianday(qe.serving_at) - julianday(qe.joined_at)) * 24 * 60
                END) as avg_wait_mins,
                AVG(CASE
                    WHEN qe.completed_at IS NOT NULL AND qe.serving_at IS NOT NULL
                    THEN (julianday(qe.completed_at) - julianday(qe.serving_at)) * 24 * 60
                END) as avg_serve_mins,
                AVG(f.rating)   as avg_rating,
                COUNT(f.rating) as rating_count
            FROM queue_entries qe
            LEFT JOIN feedback f ON f.queue_entry_id = qe.id
            WHERE qe.machine_id = ? AND date(qe.joined_at) = ?
            """,
            (mid, yesterday),
        )
        row = dict(await cursor.fetchone())

        if row["total_jobs"] == 0:
            continue

        peak_cursor = await db.execute(
            """
            SELECT CAST(strftime('%H', joined_at) AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM queue_entries
            WHERE machine_id = ? AND date(joined_at) = ?
            GROUP BY hour ORDER BY cnt DESC LIMIT 1
            """,
            (mid, yesterday),
        )
        peak_row = await peak_cursor.fetchone()
        peak_hour = dict(peak_row)["hour"] if peak_row else None

        ai_summary = await _generate_ai_summary(machine["name"], row, yesterday)

        await models.insert_analytics_snapshot(
            date=yesterday,
            machine_id=mid,
            total_jobs=row["total_jobs"],
            completed_jobs=row["completed_jobs"],
            avg_wait_mins=row["avg_wait_mins"],
            avg_serve_mins=row["avg_serve_mins"],
            peak_hour=peak_hour,
            ai_summary=ai_summary,
            no_show_count=row["no_show_count"],
            cancelled_count=row["cancelled_count"],
            unique_users=row["unique_users"],
            failure_count=row["failure_count"],
            avg_rating=row["avg_rating"],
            rating_count=row["rating_count"] or 0,
        )

    log.info("Analytics snapshots computed for %s", yesterday)


async def _generate_ai_summary(
    machine_name: str, stats: dict, date: str
) -> str | None:
    """Generate a natural-language analytics summary using OpenAI."""
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a concise analytics assistant for a university maker space. Write a 1-2 sentence summary of the machine usage stats provided.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Machine: {machine_name}, Date: {date}\n"
                        f"Total jobs: {stats['total_jobs']}, "
                        f"Completed: {stats['completed_jobs']}, "
                        f"No-shows: {stats['no_show_count']}, "
                        f"Cancelled: {stats['cancelled_count']}, "
                        f"Avg wait: {stats['avg_wait_mins']:.1f} min, "
                        f"Avg serve: {stats['avg_serve_mins']:.1f} min"
                    ),
                },
            ],
            max_tokens=100,
        )
        return response.choices[0].message.content
    except Exception:
        log.warning("AI summary generation failed for %s", machine_name)
        return None


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
