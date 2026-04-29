"""ReservBot -- Discord bot setup, lifecycle hooks, and embed management."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import settings
from db.database import init_db, close_db
from db import models
from bot.embeds import build_machine_embed, QueueButtonView
from agent.loop import start_agent, stop_agent

log = logging.getLogger(__name__)


class ReservBot(commands.Bot):
    """Custom bot subclass with queue-embed bookkeeping.

    Attributes
    ----------
    embed_messages : dict[int, int]
        Mapping of ``machine_id -> message_id`` for the pinned queue embeds
        in the queue channel. Populated on startup; used by
        ``update_queue_embeds`` to edit in place.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True  # needed to read DM content

        super().__init__(
            command_prefix="!",  # prefix commands unused but required
            intents=intents,
        )

        self.embed_messages: dict[int, int] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def setup_hook(self) -> None:
        """Called once before the bot connects.

        Order of operations:
        1. Initialise the database
        2. Load cogs
        3. Register persistent views so buttons survive restarts
        4. Sync slash commands to the configured guild
        """
        # 0. Global error handler for slash commands
        self.tree.on_error = self.on_tree_error

        # 1. Database
        await init_db()
        log.info("Database initialised")

        # 2. Cogs
        await self.load_extension("bot.cogs.queue")
        await self.load_extension("bot.cogs.admin")
        await self.load_extension("bot.cogs.dm")
        log.info("Cogs loaded")

        # 3. Persistent views (one per machine)
        machines = await models.get_machines()
        for machine in machines:
            self.add_view(QueueButtonView(machine["id"]))
        log.info("Persistent views registered for %d machines", len(machines))

        # 4. Slash command sync (guild-scoped for instant availability)
        guild = discord.Object(id=settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %d", settings.discord_guild_id)

    async def on_ready(self) -> None:
        """Fires when the bot has connected and the cache is populated."""
        log.info("ReservBot ready as %s (ID: %s)", self.user, self.user.id)  # type: ignore[union-attr]

        # Post or refresh pinned queue embeds
        await self._post_queue_embeds()

        # Start the autonomous queue agent
        start_agent(self)
        log.info("Queue agent started")

    async def close(self) -> None:
        """Graceful shutdown: stop agent, close DB, then disconnect."""
        stop_agent()
        await close_db()
        await super().close()
        log.info("ReservBot shut down cleanly")

    # ------------------------------------------------------------------ #
    # App command error handler
    # ------------------------------------------------------------------ #

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Catch-all for slash command errors so Discord always gets a response."""
        cmd = interaction.command.name if interaction.command else "unknown"
        log.exception("Slash command error in /%s", cmd, exc_info=error)

        # If a check predicate already responded, don't double-respond
        if interaction.response.is_done():
            return

        try:
            await interaction.response.send_message(
                "Something went wrong. Please try again or contact staff.",
                ephemeral=True,
            )
        except Exception:
            log.exception("Failed to send error response for /%s", cmd)

    # ------------------------------------------------------------------ #
    # Embed management
    # ------------------------------------------------------------------ #

    async def _build_units_view(self, machine_id: int) -> list[dict]:
        """Fetch units + map each to its current serving user's display name."""
        units = await models.list_units(machine_id)
        entries = await models.get_queue_for_machine(machine_id)
        serving_map: dict[int, str] = {}
        for e in entries:
            if e["status"] == "serving" and e.get("unit_id"):
                serving_map[e["unit_id"]] = e["discord_name"]
        return [
            {
                "id": u["id"],
                "label": u["label"],
                "status": u["status"],
                "archived_at": u.get("archived_at"),
                "serving_name": serving_map.get(u["id"]),
            }
            for u in units
        ]

    async def _post_queue_embeds(self) -> None:
        """Post or reuse queue embeds for each machine in the queue channel.

        On startup, checks the database for previously saved message IDs and
        tries to edit those messages. Only posts a new message if the old one
        is missing or was never created.
        """
        channel = self.get_channel(settings.queue_channel_id)
        if channel is None:
            log.error(
                "Queue channel %d not found -- cannot post embeds",
                settings.queue_channel_id,
            )
            return

        if not isinstance(channel, discord.TextChannel):
            log.error("Queue channel is not a text channel")
            return

        machines = await models.get_machines()
        for machine in machines:
            queue = await models.get_queue_for_machine(machine["id"])
            units_view = await self._build_units_view(machine["id"])
            embed = build_machine_embed(machine, queue, units=units_view)
            view = QueueButtonView(machine["id"])
            mid = machine["id"]

            # Try to reuse the existing message
            saved_msg_id = machine.get("embed_message_id")
            if saved_msg_id:
                try:
                    msg = await channel.fetch_message(int(saved_msg_id))
                    await msg.edit(embed=embed, view=view)
                    self.embed_messages[mid] = msg.id
                    log.info("Reused embed for %s (msg %d)", machine["name"], msg.id)
                    continue
                except (discord.NotFound, discord.HTTPException):
                    log.info("Old embed for %s not found, posting new one", machine["name"])

            # Post a new message and save the ID
            msg = await channel.send(embed=embed, view=view)
            self.embed_messages[mid] = msg.id
            await models.update_machine_embed_message_id(mid, msg.id)
            log.info("Posted embed for %s (msg %d)", machine["name"], msg.id)

        # Reconcile archived machines: delete lingering embeds
        archived = [
            m for m in await models.list_machines(include_archived=True)
            if m.get("archived_at") is not None and m.get("embed_message_id")
        ]
        for machine in archived:
            try:
                msg = await channel.fetch_message(int(machine["embed_message_id"]))
                await msg.delete()
                log.info("Cleaned up stale embed for archived %s", machine["name"])
            except (discord.NotFound, discord.HTTPException):
                pass
            await models.update_machine_embed_message_id(machine["id"], None)

    async def create_queue_embed(self, machine_id: int) -> None:
        """Post a new embed for ``machine_id``, or update in place if one exists."""
        channel = self.get_channel(settings.queue_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        machine = await models.get_machine(machine_id)
        if machine is None or machine.get("archived_at") is not None:
            return
        # If we already know about an embed for this machine, update instead
        if machine_id in self.embed_messages or machine.get("embed_message_id"):
            await self.update_queue_embeds(machine_id)
            return
        queue = await models.get_queue_for_machine(machine_id)
        units_view = await self._build_units_view(machine_id)
        embed = build_machine_embed(machine, queue, units=units_view)
        view = QueueButtonView(machine_id)
        msg = await channel.send(embed=embed, view=view)
        self.embed_messages[machine_id] = msg.id
        self.add_view(view)
        await models.update_machine_embed_message_id(machine_id, msg.id)
        log.info("Created embed for %s (msg %d)", machine["name"], msg.id)

    async def delete_queue_embed(self, message_id: int) -> None:
        """Delete a previously posted embed message, tolerant to 404."""
        channel = self.get_channel(settings.queue_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(message_id)
            await msg.delete()
        except discord.NotFound:
            pass
        except Exception:
            log.exception("Failed to delete embed message %d", message_id)
        # Drop from our in-memory map
        for mid, mid_msg in list(self.embed_messages.items()):
            if mid_msg == message_id:
                del self.embed_messages[mid]

    async def update_queue_embeds(self, machine_id: int | None = None) -> None:
        """Edit the pinned embed(s) to reflect current queue state.

        Parameters
        ----------
        machine_id:
            If given, only update that machine's embed. Otherwise update all.
        """
        channel = self.get_channel(settings.queue_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        machine_ids = (
            [machine_id] if machine_id is not None else list(self.embed_messages)
        )

        for mid in machine_ids:
            msg_id = self.embed_messages.get(mid)
            if msg_id is None:
                continue

            machine = await models.get_machine(mid)
            if machine is None:
                continue

            queue = await models.get_queue_for_machine(mid)
            units_view = await self._build_units_view(mid)
            embed = build_machine_embed(machine, queue, units=units_view)

            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
            except discord.NotFound:
                log.warning(
                    "Embed message %d for machine %d not found -- re-posting",
                    msg_id,
                    mid,
                )
                view = QueueButtonView(mid)
                new_msg = await channel.send(embed=embed, view=view)
                self.embed_messages[mid] = new_msg.id
                await models.update_machine_embed_message_id(mid, new_msg.id)
            except Exception:
                log.exception("Failed to update embed for machine %d", mid)

            # Edit each waiting user's join DM in place with their live rank.
            await self._refresh_position_dms(mid, queue)

    async def _refresh_position_dms(
        self,
        machine_id: int,
        queue: list[dict],
    ) -> None:
        """Edit each waiting user's join-confirmation DM with their live rank.

        Best-effort: skips entries with no stored DM message id, missing user,
        or DM-disabled. The DM was sent from the user's DM channel, so we
        fetch by discord_id and edit there.
        """
        machine = await models.get_machine(machine_id)
        if machine is None:
            return
        machine_name = machine["name"]
        waiting = [e for e in queue if e["status"] == "waiting"]
        for idx, entry in enumerate(waiting, start=1):
            msg_id = entry.get("join_dm_message_id")
            discord_id = entry.get("discord_id")
            if not msg_id or not discord_id:
                continue
            try:
                user = self.get_user(int(discord_id)) or await self.fetch_user(
                    int(discord_id)
                )
                dm = user.dm_channel or await user.create_dm()
                msg = await dm.fetch_message(int(msg_id))
                await msg.edit(
                    content=(
                        f"You're **#{idx}** in the queue for **{machine_name}**. "
                        f"I'll edit this message as the queue moves and DM you "
                        f"again when it's your turn."
                    )
                )
            except (discord.NotFound, discord.Forbidden, ValueError):
                # User deleted the DM, blocked the bot, or bad id — drop the
                # tracking so we don't keep retrying.
                try:
                    await models.set_join_dm_message_id(entry["id"], 0)
                except Exception:
                    pass
            except Exception:
                log.exception(
                    "Failed to refresh DM rank for entry %s", entry.get("id")
                )
