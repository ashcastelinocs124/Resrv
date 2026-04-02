"""Reserv — main entrypoint.

Starts both the Discord bot and the FastAPI server in a single process.
The FastAPI server runs in a background thread via uvicorn, while the
Discord bot runs on the main asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import uvicorn

from config import settings
from bot.bot import ReservBot
from api.main import app
from api import deps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("reserv")


def _run_api_server() -> None:
    """Run the FastAPI server in a separate thread."""
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


def main() -> None:
    log.info("Starting Reserv…")

    # Start the API server in a daemon thread
    api_thread = threading.Thread(target=_run_api_server, daemon=True)
    api_thread.start()
    log.info("FastAPI server started on http://0.0.0.0:8000")

    # Create and run the Discord bot on the main loop
    bot = ReservBot()

    # Share the bot reference with the API layer
    deps.bot = bot

    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
