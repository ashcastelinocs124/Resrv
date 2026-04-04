"""Queue cog -- handles button interactions from the machine embeds."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from config import settings
from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)


def _requires_verification(user: dict) -> bool:
    """Check if the user needs to verify before joining a queue."""
    if settings.public_mode:
        return False
    return not user.get("verified", False)


class QueueCog(commands.Cog):
    """Listener-based cog that routes button presses to queue actions."""

    def __init__(self, bot: ReservBot) -> None:
        self.bot = bot

    # --------------------------------------------------------------------- #
    # Interaction router
    # --------------------------------------------------------------------- #

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Dispatch button presses by custom_id prefix."""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id: str = interaction.data.get("custom_id", "")  # type: ignore[union-attr]
        if ":" not in custom_id:
            return

        action, _, raw_machine_id = custom_id.partition(":")
        try:
            machine_id = int(raw_machine_id)
        except ValueError:
            return

        handler = {
            "join_queue": self._handle_join,
            "check_position": self._handle_check,
            "leave_queue": self._handle_leave,
        }.get(action)

        if handler is not None:
            await handler(interaction, machine_id)

    # --------------------------------------------------------------------- #
    # Join Queue
    # --------------------------------------------------------------------- #

    async def _handle_join(
        self, interaction: discord.Interaction, machine_id: int
    ) -> None:
        """Add the user to the specified machine's queue."""
        machine = await models.get_machine(machine_id)
        if machine is None:
            await interaction.response.send_message(
                "Machine not found.", ephemeral=True
            )
            return

        if machine["status"] != "active":
            await interaction.response.send_message(
                f"**{machine['name']}** is not currently accepting new entries "
                f"(status: {machine['status']}).",
                ephemeral=True,
            )
            return

        # Get or create the user record
        user = await models.get_or_create_user(
            discord_id=str(interaction.user.id),
            discord_name=interaction.user.display_name,
        )

        # Verification gate
        if _requires_verification(user):
            await interaction.response.send_message(
                "You need to verify your **@illinois.edu** email before joining a queue.\n"
                "DM me your email address to get started!",
                ephemeral=True,
            )
            try:
                await interaction.user.send(
                    "To join a queue, I need to verify your Illinois email first.\n"
                    "Just send me your **@illinois.edu** email address right here!"
                )
            except discord.Forbidden:
                pass
            return

        # Check for duplicate active entry
        existing = await models.get_user_active_entry(user["id"], machine_id)
        if existing is not None:
            await interaction.response.send_message(
                f"You are already in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        # Join the queue
        entry = await models.join_queue(user["id"], machine_id)
        position = entry["position"]
        waiting_count = await models.get_waiting_count(machine_id)

        await interaction.response.send_message(
            f"You joined the queue for **{machine['name']}**!\n"
            f"Your position: **#{position}** ({waiting_count} waiting)",
            ephemeral=True,
        )

        # Update the pinned embed
        await self.bot.update_queue_embeds(machine_id)

        # DM confirmation
        try:
            await interaction.user.send(
                f"You're **#{position}** in the queue for **{machine['name']}**. "
                f"I'll DM you when it's your turn!"
            )
        except discord.Forbidden:
            log.warning(
                "Cannot DM user %s (%s) -- DMs disabled",
                interaction.user.display_name,
                interaction.user.id,
            )

    # --------------------------------------------------------------------- #
    # Check Position
    # --------------------------------------------------------------------- #

    async def _handle_check(
        self, interaction: discord.Interaction, machine_id: int
    ) -> None:
        """Tell the user their current position (or that they're not in queue)."""
        machine = await models.get_machine(machine_id)
        if machine is None:
            await interaction.response.send_message(
                "Machine not found.", ephemeral=True
            )
            return

        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await interaction.response.send_message(
                f"You are not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        entry = await models.get_user_active_entry(user["id"], machine_id)
        if entry is None:
            await interaction.response.send_message(
                f"You are not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        if entry["status"] == "serving":
            await interaction.response.send_message(
                f"You are currently being **served** at **{machine['name']}**!",
                ephemeral=True,
            )
        else:
            # Count how many people are ahead
            queue = await models.get_queue_for_machine(machine_id)
            waiting = [e for e in queue if e["status"] == "waiting"]
            pos = next(
                (
                    idx
                    for idx, e in enumerate(waiting, start=1)
                    if e["user_id"] == user["id"]
                ),
                None,
            )
            if pos is not None:
                await interaction.response.send_message(
                    f"You are **#{pos}** in the queue for **{machine['name']}** "
                    f"({len(waiting)} waiting).",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"You are not in the queue for **{machine['name']}**.",
                    ephemeral=True,
                )

    # --------------------------------------------------------------------- #
    # Leave Queue
    # --------------------------------------------------------------------- #

    async def _handle_leave(
        self, interaction: discord.Interaction, machine_id: int
    ) -> None:
        """Remove the user from the queue."""
        machine = await models.get_machine(machine_id)
        if machine is None:
            await interaction.response.send_message(
                "Machine not found.", ephemeral=True
            )
            return

        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await interaction.response.send_message(
                f"You are not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        entry = await models.get_user_active_entry(user["id"], machine_id)
        if entry is None:
            await interaction.response.send_message(
                f"You are not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        await models.leave_queue(entry["id"])

        await interaction.response.send_message(
            f"You have left the queue for **{machine['name']}**.",
            ephemeral=True,
        )

        # Update the pinned embed
        await self.bot.update_queue_embeds(machine_id)

        # DM confirmation
        try:
            await interaction.user.send(
                f"You've been removed from the **{machine['name']}** queue."
            )
        except discord.Forbidden:
            log.warning(
                "Cannot DM user %s (%s) -- DMs disabled",
                interaction.user.display_name,
                interaction.user.id,
            )


async def setup(bot: ReservBot) -> None:
    await bot.add_cog(QueueCog(bot))
