"""Admin cog -- staff slash commands restricted to the admin channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import re

from config import settings
from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)

_ILLINOIS_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@illinois\.edu$", re.IGNORECASE)


async def _machine_slug_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for machine_slug parameters."""
    machines = await models.get_machines()
    choices = [
        app_commands.Choice(name=f"{m['name']} ({m['slug']})", value=m["slug"])
        for m in machines
        if current.lower() in m["slug"].lower()
        or current.lower() in m["name"].lower()
    ]
    return choices[:25]


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


class ProfileModal(discord.ui.Modal, title="SCD Queue — Edit Profile"):
    """Edit profile modal, pre-filled with current data."""

    first_name = discord.ui.TextInput(
        label="First Name",
        placeholder="e.g. Alex",
        min_length=1,
        max_length=50,
    )
    last_name = discord.ui.TextInput(
        label="Last Name",
        placeholder="e.g. Chen",
        min_length=1,
        max_length=50,
    )
    email = discord.ui.TextInput(
        label="Email",
        placeholder="e.g. achen2@illinois.edu",
        min_length=5,
        max_length=100,
    )
    major = discord.ui.TextInput(
        label="Major",
        placeholder="e.g. Computer Science",
        min_length=2,
        max_length=100,
    )
    graduation_year = discord.ui.TextInput(
        label="Expected Graduation Year",
        placeholder="e.g. 2027",
        min_length=4,
        max_length=4,
    )

    def __init__(self, user_id: int, college_id: int | None) -> None:
        super().__init__()
        self._user_id = user_id
        self._college_id = college_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        email_val = self.email.value.strip()
        if not _ILLINOIS_EMAIL_RE.match(email_val):
            await interaction.response.send_message(
                "Please enter a valid **@illinois.edu** email.", ephemeral=True
            )
            return

        year_val = self.graduation_year.value.strip()
        if not year_val.isdigit() or not (2024 <= int(year_val) <= 2035):
            await interaction.response.send_message(
                "Graduation year must be between 2024 and 2035.", ephemeral=True
            )
            return

        full_name_val = (
            f"{self.first_name.value.strip()} {self.last_name.value.strip()}"
        ).strip()
        await models.update_user_profile(
            user_id=self._user_id,
            full_name=full_name_val,
            email=email_val,
            major=self.major.value.strip(),
            college_id=self._college_id,
            graduation_year=year_val,
        )
        await interaction.response.send_message(
            "Profile updated!", ephemeral=True
        )


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
    @app_commands.autocomplete(machine_slug=_machine_slug_autocomplete)
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
    @app_commands.autocomplete(machine_slug=_machine_slug_autocomplete)
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
    @app_commands.autocomplete(machine_slug=_machine_slug_autocomplete)
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
    @app_commands.autocomplete(machine_slug=_machine_slug_autocomplete)
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

            serving_name = (
                (serving.get("full_name") or serving["discord_name"])
                if serving
                else "--"
            )
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

    # --------------------------------------------------------------------- #
    # /profile (available to everyone)
    # --------------------------------------------------------------------- #

    @app_commands.command(
        name="profile", description="View or edit your SCD profile"
    )
    async def profile(self, interaction: discord.Interaction) -> None:
        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            user = await models.get_or_create_user(
                str(interaction.user.id), interaction.user.display_name
            )

        modal = ProfileModal(user["id"], user.get("college_id"))
        if user.get("full_name"):
            prior_full = user["full_name"].strip()
            parts = prior_full.split(None, 1)
            modal.first_name.default = parts[0] if parts else ""
            modal.last_name.default = parts[1] if len(parts) > 1 else ""
        if user.get("email"):
            modal.email.default = user["email"]
        if user.get("major"):
            modal.major.default = user["major"]
        if user.get("graduation_year"):
            modal.graduation_year.default = user["graduation_year"]

        await interaction.response.send_modal(modal)


async def setup(bot: ReservBot) -> None:
    await bot.add_cog(AdminCog(bot))
