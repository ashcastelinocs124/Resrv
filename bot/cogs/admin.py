"""Admin cog -- staff slash commands restricted to the admin channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import settings
from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)


def _admin_channel_only() -> app_commands.check:
    """Decorator: restrict a slash command to the configured admin channel."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.channel_id != settings.admin_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the admin channel.",
                ephemeral=True,
            )
            return False
        return True

    return app_commands.check(predicate)


class AdminCog(commands.Cog):
    """Staff-only slash commands for queue management."""

    def __init__(self, bot: ReservBot) -> None:
        self.bot = bot

    # --------------------------------------------------------------------- #
    # /bump @user machine_slug
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="bump", description="Move a user to the top of a machine's queue"
    )
    @app_commands.describe(
        user="The user to bump", machine_slug="Machine slug (e.g. laser-cutter)"
    )
    @_admin_channel_only()
    async def bump(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        machine_slug: str,
    ) -> None:
        machine = await models.get_machine_by_slug(machine_slug)
        if machine is None:
            await interaction.response.send_message(
                f"No machine found with slug `{machine_slug}`.", ephemeral=True
            )
            return

        db_user = await models.get_user_by_discord_id(str(user.id))
        if db_user is None:
            await interaction.response.send_message(
                f"{user.mention} is not registered.", ephemeral=True
            )
            return

        entry = await models.get_user_active_entry(db_user["id"], machine["id"])
        if entry is None or entry["status"] != "waiting":
            await interaction.response.send_message(
                f"{user.mention} is not waiting in the queue for "
                f"**{machine['name']}**.",
                ephemeral=True,
            )
            return

        await models.bump_entry_to_top(entry["id"], machine["id"])
        await interaction.response.send_message(
            f"Bumped {user.mention} to the top of **{machine['name']}** queue."
        )
        await self.bot.update_queue_embeds(machine["id"])

    # --------------------------------------------------------------------- #
    # /remove @user machine_slug
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="remove", description="Remove a user from a machine's queue"
    )
    @app_commands.describe(
        user="The user to remove", machine_slug="Machine slug (e.g. laser-cutter)"
    )
    @_admin_channel_only()
    async def remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        machine_slug: str,
    ) -> None:
        machine = await models.get_machine_by_slug(machine_slug)
        if machine is None:
            await interaction.response.send_message(
                f"No machine found with slug `{machine_slug}`.", ephemeral=True
            )
            return

        db_user = await models.get_user_by_discord_id(str(user.id))
        if db_user is None:
            await interaction.response.send_message(
                f"{user.mention} is not registered.", ephemeral=True
            )
            return

        entry = await models.get_user_active_entry(db_user["id"], machine["id"])
        if entry is None:
            await interaction.response.send_message(
                f"{user.mention} is not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        await models.leave_queue(entry["id"])
        await interaction.response.send_message(
            f"Removed {user.mention} from **{machine['name']}** queue."
        )
        await self.bot.update_queue_embeds(machine["id"])

    # --------------------------------------------------------------------- #
    # /skip @user machine_slug
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="skip",
        description="Mark a user as no-show and advance the queue",
    )
    @app_commands.describe(
        user="The user to skip", machine_slug="Machine slug (e.g. laser-cutter)"
    )
    @_admin_channel_only()
    async def skip(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        machine_slug: str,
    ) -> None:
        machine = await models.get_machine_by_slug(machine_slug)
        if machine is None:
            await interaction.response.send_message(
                f"No machine found with slug `{machine_slug}`.", ephemeral=True
            )
            return

        db_user = await models.get_user_by_discord_id(str(user.id))
        if db_user is None:
            await interaction.response.send_message(
                f"{user.mention} is not registered.", ephemeral=True
            )
            return

        entry = await models.get_user_active_entry(db_user["id"], machine["id"])
        if entry is None:
            await interaction.response.send_message(
                f"{user.mention} is not in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        await models.update_entry_status(entry["id"], "no_show")
        await interaction.response.send_message(
            f"Marked {user.mention} as **no-show** on **{machine['name']}**. "
            f"Queue will advance automatically."
        )
        await self.bot.update_queue_embeds(machine["id"])

    # --------------------------------------------------------------------- #
    # /pause machine_slug
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="pause",
        description="Toggle a machine between active and maintenance",
    )
    @app_commands.describe(machine_slug="Machine slug (e.g. laser-cutter)")
    @_admin_channel_only()
    async def pause(
        self, interaction: discord.Interaction, machine_slug: str
    ) -> None:
        machine = await models.get_machine_by_slug(machine_slug)
        if machine is None:
            await interaction.response.send_message(
                f"No machine found with slug `{machine_slug}`.", ephemeral=True
            )
            return

        new_status = (
            "maintenance" if machine["status"] == "active" else "active"
        )
        await models.update_machine_status(machine["id"], new_status)

        label = "Paused" if new_status == "maintenance" else "Resumed"
        await interaction.response.send_message(
            f"**{label}** {machine['name']} (now `{new_status}`)."
        )
        await self.bot.update_queue_embeds(machine["id"])

    # --------------------------------------------------------------------- #
    # /status
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="status", description="Quick overview of all machine queues"
    )
    @_admin_channel_only()
    async def status(self, interaction: discord.Interaction) -> None:
        machines = await models.get_machines()
        if not machines:
            await interaction.response.send_message(
                "No machines configured.", ephemeral=True
            )
            return

        lines: list[str] = []
        for m in machines:
            serving = await models.get_serving_entry(m["id"])
            waiting_count = await models.get_waiting_count(m["id"])

            status_icon = {
                "active": "\U0001F7E2",      # green circle
                "maintenance": "\U0001F7E0",  # orange circle
                "offline": "\U0001F534",      # red circle
            }.get(m["status"], "\U00002B1C")  # white square

            serving_name = serving["discord_name"] if serving else "--"
            lines.append(
                f"{status_icon} **{m['name']}** | "
                f"Serving: {serving_name} | "
                f"Waiting: {waiting_count}"
            )

        embed = discord.Embed(
            title="Queue Status Overview",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: ReservBot) -> None:
    await bot.add_cog(AdminCog(bot))
