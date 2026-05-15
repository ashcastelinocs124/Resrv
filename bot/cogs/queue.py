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


async def _defer_and_dm(
    interaction: discord.Interaction,
    content: str,
    *,
    view: discord.ui.View | None = None,
) -> None:
    """Acknowledge the channel interaction silently and reply via DM."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        kwargs: dict = {}
        if view is not None:
            kwargs["view"] = view
        await interaction.user.send(content, **kwargs)
    except discord.Forbidden:
        log.warning(
            "Cannot DM user %s (%s) -- falling back to ephemeral",
            interaction.user.display_name,
            interaction.user.id,
        )
        kwargs_fb: dict = {"ephemeral": True}
        if view is not None:
            kwargs_fb["view"] = view
        await interaction.followup.send(content, **kwargs_fb)


async def _join_and_dm(
    *,
    interaction: discord.Interaction,
    bot: "ReservBot",
    user_id: int,
    machine_id: int,
    machine_name: str,
    purpose: str = "production",
    confirmation_prefix: str = "You joined the queue for",
) -> None:
    """Join the queue and send a DM with live rank."""
    entry = await models.join_queue(user_id, machine_id, purpose=purpose)

    if purpose == "training":
        await models.bump_entry_to_top(entry["id"], machine_id)

    queue = await models.get_queue_for_machine(machine_id)
    waiting = [e for e in queue if e["status"] == "waiting"]
    position = next(
        (idx for idx, e in enumerate(waiting, start=1) if e["id"] == entry["id"]),
        len(waiting),
    )
    waiting_count = len(waiting)

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    await bot.update_queue_embeds(machine_id)

    purpose_label = " (Training)" if purpose == "training" else ""
    try:
        msg = await interaction.user.send(
            f"{confirmation_prefix} **{machine_name}**{purpose_label}!\n"
            f"Your position: **#{position}** ({waiting_count} waiting).\n"
            f"I'll update this message as the queue moves and DM you again when "
            f"it's your turn."
        )
        await models.set_join_dm_message_id(entry["id"], msg.id)
    except discord.Forbidden:
        log.warning(
            "Cannot DM user %s (%s) -- falling back to ephemeral",
            interaction.user.display_name,
            interaction.user.id,
        )
        await interaction.followup.send(
            f"{confirmation_prefix} **{machine_name}**{purpose_label}!\n"
            f"Your position: **#{position}** ({waiting_count} waiting)",
            ephemeral=True,
        )

    # Training entries get routed to whichever mentor is currently free.
    if purpose == "training":
        try:
            from bot.cogs.mentor import assign_mentor_for_training_entry

            db_user = await models.get_user_by_discord_id(
                str(interaction.user.id)
            )
            enriched = {
                "id": entry["id"],
                "discord_id": str(interaction.user.id),
                "full_name": (db_user or {}).get("full_name"),
                "discord_name": (
                    (db_user or {}).get("discord_name")
                    or interaction.user.display_name
                ),
                "machine_name": machine_name,
            }
            await assign_mentor_for_training_entry(bot, enriched)
        except Exception:
            log.exception(
                "Failed to assign mentor for training entry %d", entry["id"]
            )


class PurposeSelectView(discord.ui.View):
    """DM view with Training / Production buttons shown before joining the queue."""

    def __init__(
        self,
        *,
        bot: "ReservBot",
        user_id: int,
        machine_id: int,
        machine_name: str,
        confirmation_prefix: str = "You joined the queue for",
    ) -> None:
        super().__init__(timeout=120)
        self._bot = bot
        self._user_id = user_id
        self._machine_id = machine_id
        self._machine_name = machine_name
        self._confirmation_prefix = confirmation_prefix

    async def _join(self, interaction: discord.Interaction, purpose: str) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(
            content=f"Selected **{purpose.capitalize()}**. Joining queue...",
            view=self,
        )
        await _join_and_dm(
            interaction=interaction,
            bot=self._bot,
            user_id=self._user_id,
            machine_id=self._machine_id,
            machine_name=self._machine_name,
            purpose=purpose,
            confirmation_prefix=self._confirmation_prefix,
        )

    @discord.ui.button(label="Production", style=discord.ButtonStyle.green)
    async def production(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._join(interaction, "production")

    @discord.ui.button(label="Training", style=discord.ButtonStyle.blurple)
    async def training(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._join(interaction, "training")


class VerificationModal(discord.ui.Modal, title="SCD Queue — Email Verification"):
    """Collects the 6-digit code we mailed the user."""

    code = discord.ui.TextInput(
        label="6-digit code",
        placeholder="123456",
        min_length=6,
        max_length=6,
    )

    def __init__(
        self,
        *,
        bot: "ReservBot",
        user_id: int,
        discord_id: str,
        machine_id: int,
        college_id: int,
        full_name: str,
        email: str,
        major: str,
        graduation_year: str,
    ) -> None:
        super().__init__()
        self._bot = bot
        self._user_id = user_id
        self._discord_id = discord_id
        self._machine_id = machine_id
        self._college_id = college_id
        self._full_name = full_name
        self._email = email
        self._major = major
        self._graduation_year = graduation_year

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from bot import email_verification as ev

        ok, email = await ev.verify_code(
            self._discord_id, self.code.value.strip()
        )
        if not ok:
            await _defer_and_dm(
                interaction,
                "Wrong or expired code. Click **Join Queue** again to request "
                "a new one.",
            )
            return

        verified_email = email or self._email
        await ev.mark_user_verified(self._user_id, verified_email)
        await models.register_user(
            user_id=self._user_id,
            full_name=self._full_name,
            email=verified_email,
            major=self._major,
            college_id=self._college_id,
            graduation_year=self._graduation_year,
        )
        machine = await models.get_machine(self._machine_id)
        if machine is None:
            await _defer_and_dm(interaction, "Machine not found.")
            return

        existing = await models.get_user_active_entry(
            self._user_id, self._machine_id
        )
        if existing is not None:
            await _defer_and_dm(
                interaction,
                f"You're verified! You're already in the queue for "
                f"**{machine['name']}**.",
            )
            return

        purpose_view = PurposeSelectView(
            bot=self._bot,
            user_id=self._user_id,
            machine_id=self._machine_id,
            machine_name=machine["name"],
            confirmation_prefix=(
                "Verified! You're registered and joined the queue for"
            ),
        )
        await _defer_and_dm(
            interaction,
            f"Are you using **{machine['name']}** for training or production?",
            view=purpose_view,
        )


class VerificationLaunchView(discord.ui.View):
    """Single-button view (sent via DM) that opens VerificationModal on click.

    Discord forbids opening a modal as the response to a modal submission,
    so we hand SignupModal a DM message + button. The button's click is a
    MessageComponent interaction, which IS allowed to send a modal in response.
    """

    def __init__(
        self,
        *,
        bot: "ReservBot",
        user_id: int,
        discord_id: str,
        machine_id: int,
        college_id: int,
        full_name: str,
        email: str,
        major: str,
        graduation_year: str,
    ) -> None:
        super().__init__(timeout=600)
        self._bot = bot
        self._user_id = user_id
        self._discord_id = discord_id
        self._machine_id = machine_id
        self._college_id = college_id
        self._full_name = full_name
        self._email = email
        self._major = major
        self._graduation_year = graduation_year

    @discord.ui.button(label="Enter verification code",
                        style=discord.ButtonStyle.primary)
    async def open_modal(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            VerificationModal(
                bot=self._bot,
                user_id=self._user_id,
                discord_id=self._discord_id,
                machine_id=self._machine_id,
                college_id=self._college_id,
                full_name=self._full_name,
                email=self._email,
                major=self._major,
                graduation_year=self._graduation_year,
            )
        )


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
    """DM two-button choice when a serving user clicks Leave Queue."""

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
    """View sent via DM before the signup modal -- picks a UIUC college."""

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
            prior_full = (prefill.get("full_name") or "").strip()
            parts = prior_full.split(None, 1)
            self.first_name.default = parts[0] if parts else ""
            self.last_name.default = parts[1] if len(parts) > 1 else ""
            self.email.default = prefill.get("email") or ""
            self.major.default = prefill.get("major") or ""
            self.graduation_year.default = prefill.get("graduation_year") or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from api.settings_store import get_setting
        from bot import email_verification as ev

        email_val = self.email.value.strip()
        if not _ILLINOIS_EMAIL_RE.match(email_val):
            await _defer_and_dm(
                interaction,
                "Please enter a valid **@illinois.edu** email.",
            )
            return

        year_val = self.graduation_year.value.strip()
        if not year_val.isdigit() or not (2024 <= int(year_val) <= 2035):
            await _defer_and_dm(
                interaction,
                "Graduation year must be between 2024 and 2035.",
            )
            return

        full_name_val = (
            f"{self.first_name.value.strip()} {self.last_name.value.strip()}"
        ).strip()

        machine = await models.get_machine(self._machine_id)
        if machine is None:
            await _defer_and_dm(interaction, "Machine not found.")
            return

        public_mode = (await get_setting("public_mode")) == "true"
        existing_user = await models.get_user_by_discord_id(
            str(interaction.user.id)
        )
        already_verified = (
            existing_user is not None
            and existing_user.get("verified") == 1
            and existing_user.get("email") == email_val
        )

        if public_mode or already_verified:
            await models.register_user(
                user_id=self._user_id,
                full_name=full_name_val,
                email=email_val,
                major=self.major.value.strip(),
                college_id=self._college_id,
                graduation_year=year_val,
            )
            existing = await models.get_user_active_entry(
                self._user_id, self._machine_id
            )
            if existing is not None:
                await _defer_and_dm(
                    interaction,
                    f"You're registered! You're already in the queue for "
                    f"**{machine['name']}**.",
                )
                return
            purpose_view = PurposeSelectView(
                bot=self._bot,
                user_id=self._user_id,
                machine_id=self._machine_id,
                machine_name=machine["name"],
                confirmation_prefix=(
                    "Welcome! You're registered and joined the queue for"
                ),
            )
            await _defer_and_dm(
                interaction,
                f"Are you using **{machine['name']}** for training or production?",
                view=purpose_view,
            )
            return

        try:
            code = await ev.issue_code(str(interaction.user.id), email_val)
            await ev.send_verification_email(email_val, code)
        except ev.VerificationRateLimitError:
            await _defer_and_dm(
                interaction,
                "Too many verification requests. Try again in an hour, or "
                "ask staff for help.",
            )
            return
        except ev.EmailSendError:
            await _defer_and_dm(
                interaction,
                "Verification is temporarily unavailable. Please ask staff.",
            )
            return

        view = VerificationLaunchView(
            bot=self._bot,
            user_id=self._user_id,
            discord_id=str(interaction.user.id),
            machine_id=self._machine_id,
            college_id=self._college_id,
            full_name=full_name_val,
            email=email_val,
            major=self.major.value.strip(),
            graduation_year=year_val,
        )
        await _defer_and_dm(
            interaction,
            f"We sent a 6-digit code to **{email_val}**. "
            f"Check your **spam/junk** folder if you don't see it!\n"
            f"Click the button below to enter it.",
            view=view,
        )


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
            await _defer_and_dm(interaction, "Machine not found.")
            return

        if machine["status"] != "active":
            await _defer_and_dm(
                interaction,
                f"**{machine['name']}** is not currently accepting new entries "
                f"(status: {machine['status']}).",
            )
            return

        user = await models.get_or_create_user(
            discord_id=str(interaction.user.id),
            discord_name=interaction.user.display_name,
        )

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
                await _defer_and_dm(
                    interaction,
                    "Sign-ups are temporarily unavailable — please contact staff.",
                )
                return
            await _defer_and_dm(
                interaction,
                "Pick your UIUC college:",
                view=view,
            )
            return

        existing = await models.get_user_active_entry(user["id"], machine_id)
        if existing is not None:
            await _defer_and_dm(
                interaction,
                f"You are already in the queue for **{machine['name']}**.",
            )
            return

        view = PurposeSelectView(
            bot=self.bot,
            user_id=user["id"],
            machine_id=machine_id,
            machine_name=machine["name"],
        )
        await _defer_and_dm(
            interaction,
            f"Are you using **{machine['name']}** for training or production?",
            view=view,
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
            await _defer_and_dm(interaction, "Machine not found.")
            return

        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await _defer_and_dm(
                interaction,
                f"You are not in the queue for **{machine['name']}**.",
            )
            return

        entry = await models.get_user_active_entry(user["id"], machine_id)
        if entry is None:
            await _defer_and_dm(
                interaction,
                f"You are not in the queue for **{machine['name']}**.",
            )
            return

        if entry["status"] == "serving":
            await _defer_and_dm(
                interaction,
                f"You are currently being **served** at **{machine['name']}**!",
            )
        else:
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
                await _defer_and_dm(
                    interaction,
                    f"You are **#{pos}** in the queue for **{machine['name']}** "
                    f"({len(waiting)} waiting).",
                )
            else:
                await _defer_and_dm(
                    interaction,
                    f"You are not in the queue for **{machine['name']}**.",
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
            await _defer_and_dm(interaction, "Machine not found.")
            return

        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await _defer_and_dm(
                interaction,
                f"You are not in the queue for **{machine['name']}**.",
            )
            return

        entry = await models.get_user_active_entry(user["id"], machine_id)
        if entry is None:
            await _defer_and_dm(
                interaction,
                f"You are not in the queue for **{machine['name']}**.",
            )
            return

        if entry["status"] == "serving":
            view = LeaveServingView(
                bot=self.bot,
                entry_id=entry["id"],
                machine_id=machine_id,
                machine_name=machine["name"],
            )
            await _defer_and_dm(
                interaction,
                f"Are you finishing your session on **{machine['name']}** "
                f"or cancelling?",
                view=view,
            )
            return

        await models.leave_queue(entry["id"])

        await _defer_and_dm(
            interaction,
            f"You have left the queue for **{machine['name']}**.",
        )

        await self.bot.update_queue_embeds(machine_id)


async def setup(bot: ReservBot) -> None:
    await bot.add_cog(QueueCog(bot))
