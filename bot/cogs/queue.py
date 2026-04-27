"""Queue cog -- handles button interactions from the machine embeds."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)

_ILLINOIS_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@illinois\.edu$", re.IGNORECASE)


class _LeaveServingButton(discord.ui.Button):
    """Button on LeaveServingView. ``mode`` is 'finish' or 'cancel'."""

    def __init__(self, *, mode: str, label: str, style: discord.ButtonStyle,
                  custom_id: str) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "LeaveServingView" = self.view  # type: ignore[assignment]
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        if self._mode == "finish":
            await models.update_entry_status(
                view._entry_id, "completed", job_successful=1,
            )
            await interaction.response.edit_message(
                content=f"Marked **{view._machine_name}** as finished. "
                        "Check your DMs to rate the visit.",
                view=view,
            )
            from bot.cogs.dm import send_rating_dm
            await send_rating_dm(
                interaction.user,
                queue_entry_id=view._entry_id,
                machine_name=view._machine_name,
            )
        else:  # cancel
            await models.leave_queue(view._entry_id)
            await interaction.response.edit_message(
                content=f"Session on **{view._machine_name}** cancelled.",
                view=view,
            )

        await view._bot.update_queue_embeds(view._machine_id)


class LeaveServingView(discord.ui.View):
    """Ephemeral two-button choice when a serving user clicks Leave Queue."""

    def __init__(self, *, bot: "ReservBot", entry_id: int, machine_id: int,
                  machine_name: str) -> None:
        super().__init__(timeout=120)
        self._bot = bot
        self._entry_id = entry_id
        self._machine_id = machine_id
        self._machine_name = machine_name
        self.add_item(_LeaveServingButton(
            mode="finish", label="Finish early",
            style=discord.ButtonStyle.success,
            custom_id=f"leave_finish:{entry_id}",
        ))
        self.add_item(_LeaveServingButton(
            mode="cancel", label="Cancel session",
            style=discord.ButtonStyle.danger,
            custom_id=f"leave_cancel:{entry_id}",
        ))


class _CollegeSelect(discord.ui.Select):
    """Select subclass exposing a writable ``values`` attribute for tests."""

    def __init__(self, *, view_ref: "CollegeSelectView", **kwargs) -> None:
        super().__init__(**kwargs)
        self._view_ref = view_ref
        self._test_values: list[str] | None = None

    @property
    def values(self) -> list[str]:  # type: ignore[override]
        if self._test_values is not None:
            return self._test_values
        return super().values

    @values.setter
    def values(self, val: list[str]) -> None:
        self._test_values = list(val)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._view_ref.on_select(interaction, self)


class CollegeSelectView(discord.ui.View):
    """Ephemeral view shown before the signup modal — picks a UIUC college."""

    def __init__(
        self,
        *,
        bot: ReservBot,
        user_id: int,
        machine_id: int,
        prefill: dict | None,
    ) -> None:
        super().__init__(timeout=120)
        self._bot = bot
        self._user_id = user_id
        self._machine_id = machine_id
        self._prefill = prefill

    @classmethod
    async def build(
        cls,
        *,
        bot: ReservBot,
        user_id: int,
        machine_id: int,
        prefill: dict | None,
    ) -> "CollegeSelectView":
        colleges = await models.list_active_colleges()
        view = cls(
            bot=bot, user_id=user_id, machine_id=machine_id, prefill=prefill
        )
        options = [
            discord.SelectOption(label=c["name"][:100], value=str(c["id"]))
            for c in colleges[:25]
        ]
        select = _CollegeSelect(
            view_ref=view,
            custom_id=f"signup_college:{user_id}:{machine_id}",
            placeholder="Select your college",
            min_values=1,
            max_values=1,
            options=options,
        )
        view.add_item(select)
        return view

    async def on_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        college_id = int(select.values[0])
        modal = SignupModal(
            bot=self._bot,
            user_id=self._user_id,
            machine_id=self._machine_id,
            college_id=college_id,
            prefill=self._prefill,
        )
        await interaction.response.send_modal(modal)


class SignupModal(discord.ui.Modal, title="SCD Queue — Sign Up"):
    """Collects user profile info before first queue join (college picked separately)."""

    full_name = discord.ui.TextInput(
        label="Full Name",
        placeholder="e.g. Alex Chen",
        min_length=2,
        max_length=100,
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

    def __init__(
        self,
        *,
        bot: ReservBot,
        user_id: int,
        machine_id: int,
        college_id: int,
        prefill: dict | None,
    ) -> None:
        super().__init__()
        self._bot = bot
        self._user_id = user_id
        self._machine_id = machine_id
        self._college_id = college_id
        if prefill:
            self.full_name.default = prefill.get("full_name") or ""
            self.email.default = prefill.get("email") or ""
            self.major.default = prefill.get("major") or ""
            self.graduation_year.default = prefill.get("graduation_year") or ""

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

        await models.register_user(
            user_id=self._user_id,
            full_name=self.full_name.value.strip(),
            email=email_val,
            major=self.major.value.strip(),
            college_id=self._college_id,
            graduation_year=year_val,
        )

        machine = await models.get_machine(self._machine_id)
        if machine is None:
            await interaction.response.send_message(
                "Machine not found.", ephemeral=True
            )
            return

        existing = await models.get_user_active_entry(self._user_id, self._machine_id)
        if existing is not None:
            await interaction.response.send_message(
                f"You're registered! You're already in the queue for **{machine['name']}**.",
                ephemeral=True,
            )
            return

        entry = await models.join_queue(self._user_id, self._machine_id)
        position = entry["position"]
        waiting_count = await models.get_waiting_count(self._machine_id)

        await interaction.response.send_message(
            f"Welcome! You're registered and joined the queue for **{machine['name']}**!\n"
            f"Your position: **#{position}** ({waiting_count} waiting)",
            ephemeral=True,
        )
        await self._bot.update_queue_embeds(self._machine_id)

        try:
            await interaction.user.send(
                f"You're **#{position}** in the queue for **{machine['name']}**. "
                f"I'll DM you when it's your turn!"
            )
        except discord.Forbidden:
            pass


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

        # Registration gate — show college select view (then modal) if not registered
        if not user.get("registered"):
            prefill_dict = None
            has_any = any([
                user.get("full_name"), user.get("email"),
                user.get("major"), user.get("graduation_year"),
            ])
            if has_any:
                prefill_dict = {
                    "full_name": user.get("full_name"),
                    "email": user.get("email"),
                    "major": user.get("major"),
                    "graduation_year": user.get("graduation_year"),
                }
            view = await CollegeSelectView.build(
                bot=self.bot,
                user_id=user["id"],
                machine_id=machine_id,
                prefill=prefill_dict,
            )
            if not view.children or not view.children[0].options:
                await interaction.response.send_message(
                    "Sign-ups are temporarily unavailable — please contact staff.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Pick your UIUC college:", view=view, ephemeral=True,
            )
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

        if entry["status"] == "serving":
            view = LeaveServingView(
                bot=self.bot,
                entry_id=entry["id"],
                machine_id=machine_id,
                machine_name=machine["name"],
            )
            await interaction.response.send_message(
                f"Are you finishing your session on **{machine['name']}** "
                f"or cancelling?",
                view=view,
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
