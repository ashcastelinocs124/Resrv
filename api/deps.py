"""Shared dependencies for the API layer.

Holds references that may be needed across routes (e.g. the Discord bot
instance for sending WebSocket notifications later).  Keep this minimal
for the MVP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Placeholder for the Discord bot reference — will be set by the main
# entrypoint once the bot is running, enabling routes to trigger bot
# actions (e.g. DM a user when they're called to a machine).
bot: Any | None = None


def notify_embed_update(machine_id: int) -> None:
    """Schedule a Discord embed refresh from the API thread.

    The bot runs on a different event loop, so we use
    run_coroutine_threadsafe to bridge the two.
    """
    if bot is None or bot.loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            bot.update_queue_embeds(machine_id), bot.loop
        )
    except Exception:
        log.warning("Failed to schedule embed update for machine %d", machine_id)
