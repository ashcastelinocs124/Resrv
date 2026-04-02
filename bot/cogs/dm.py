"""DM cog -- handles direct messages with OpenAI-powered intent classification."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from config import settings
from db import models

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)

# Available machine slugs for the classifier prompt
_MACHINE_SLUGS = ["large-format-printer", "laser-cutter", "cnc-router", "water-jet"]

# Valid intents the classifier can return
_VALID_INTENTS = {"done", "more_time", "check_position", "leave", "none"}

# Cooldown between DM responses (seconds)
_DM_COOLDOWN = 5.0

# System prompt for the conversational agent
_SYSTEM_PROMPT = """\
You are SCD Bot, the friendly queue assistant for the SCD (Student Creative Design) \
facility at the University of Illinois. You help students manage their machine queue \
reservations via DM.

You have a warm, casual personality. Keep responses short (1-3 sentences). Use emoji \
sparingly. Be helpful and encouraging.

Available machines: Large Format Printer, Laser Cutter, CNC Router, Water Jet.

When the user's message relates to a queue action, include an "action" in your JSON \
response. When they're just chatting, set action to "none".

Actions:
- "done" — user is finished with their machine session
- "more_time" — user needs more time on a machine
- "check_position" — user wants to know their queue position or status
- "leave" — user wants to leave or cancel from a queue
- "none" — no queue action needed (casual chat, greeting, question, etc.)

Respond with ONLY valid JSON (no markdown):
{"reply": "<your conversational response>", "action": "<action>", "machine": "<slug-or-null>"}

Machine slugs: large-format-printer, laser-cutter, cnc-router, water-jet
Set machine to null if not mentioned or not relevant.

Examples:
User: "hey whats up" -> {"reply": "Hey! Not much, just here to help with the queue. What's going on?", "action": "none", "machine": null}
User: "I just finished on the laser" -> {"reply": "Nice, marking you as done on the Laser Cutter!", "action": "done", "machine": "laser-cutter"}
User: "can I get a few more minutes" -> {"reply": "No worries, I'll reset your timer. Take your time!", "action": "more_time", "machine": null}
User: "where am I in line?" -> {"reply": "Let me check that for you!", "action": "check_position", "machine": null}
User: "thanks for the help!" -> {"reply": "Anytime! Good luck with your project 🙌", "action": "none", "machine": null}
"""


# --------------------------------------------------------------------------- #
# OpenAI client (lazy import to avoid hard dependency at module level)
# --------------------------------------------------------------------------- #

def _make_openai_client():  # type: ignore[no-untyped-def]
    """Create an AsyncOpenAI client if the API key is configured."""
    if not settings.openai_api_key:
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=settings.openai_api_key)
    except ImportError:
        log.warning("openai package not installed -- DM intent classification disabled")
        return None


# --------------------------------------------------------------------------- #
# Button Views
# --------------------------------------------------------------------------- #

class MachinePicker(discord.ui.View):
    """Buttons for each machine the user has an active entry in.

    Used when the user has multiple active entries and we need them to
    pick which machine their action applies to.
    """

    def __init__(self, action: str, entries: list[dict[str, Any]]) -> None:
        super().__init__(timeout=60)
        for entry in entries:
            self.add_item(
                discord.ui.Button(
                    label=entry["machine_name"],
                    style=discord.ButtonStyle.blurple,
                    custom_id=f"dm_pick:{action}:{entry['id']}",
                )
            )


class FallbackActions(discord.ui.View):
    """Fallback buttons when the classifier cannot determine intent.

    Presents the four primary actions as buttons so the user can pick one.
    """

    def __init__(self) -> None:
        super().__init__(timeout=60)
        actions = [
            ("I'm Done", "done", discord.ButtonStyle.green),
            ("More Time", "more_time", discord.ButtonStyle.blurple),
            ("Check Position", "check_position", discord.ButtonStyle.gray),
            ("Leave Queue", "leave", discord.ButtonStyle.red),
        ]
        for label, action, style in actions:
            self.add_item(
                discord.ui.Button(
                    label=label,
                    style=style,
                    custom_id=f"dm_fallback:{action}",
                )
            )


# --------------------------------------------------------------------------- #
# DM Cog
# --------------------------------------------------------------------------- #

class DMCog(commands.Cog):
    """Handles DMs from users with natural language intent classification."""

    def __init__(self, bot: ReservBot) -> None:
        self.bot = bot
        self._cooldowns: dict[int, float] = {}
        self._openai = _make_openai_client()

    # ------------------------------------------------------------------ #
    # on_message listener -- entry point for DMs
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Process incoming DMs from non-bot users."""
        # Ignore guild messages and bot messages
        if message.guild is not None:
            return
        if message.author.bot:
            return

        # Per-user cooldown
        now = time.monotonic()
        last = self._cooldowns.get(message.author.id, 0.0)
        if now - last < _DM_COOLDOWN:
            return
        self._cooldowns[message.author.id] = now

        # Show typing indicator while processing
        async with message.channel.typing():
            reply, action, machine_slug = await self._converse(message.content)

            if action == "none":
                # Pure chat — just send the conversational reply
                await message.reply(reply)
                return

            # Queue action detected — execute it
            await self._execute_intent(message, action, machine_slug, reply)

    # ------------------------------------------------------------------ #
    # OpenAI classifier
    # ------------------------------------------------------------------ #

    async def _converse(self, text: str) -> tuple[str, str, str | None]:
        """Send user message to OpenAI and get a conversational reply + action.

        Returns
        -------
        tuple[str, str, str | None]
            (reply, action, machine_slug)
        """
        if self._openai is None:
            return ("I'm having trouble right now. Try the buttons in the queue channel!", "none", None)

        try:
            response = await self._openai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            )

            raw = response.choices[0].message.content
            if raw is None:
                return ("Hmm, I didn't catch that. What can I help you with?", "none", None)

            data = json.loads(raw.strip())
            reply = data.get("reply", "What can I help you with?")
            action = data.get("action", "none")
            machine = data.get("machine")

            # Validate action
            if action not in _VALID_INTENTS:
                action = "none"

            # Validate machine slug
            if machine is not None and machine not in _MACHINE_SLUGS:
                machine = None

            return (reply, action, machine)

        except Exception:
            log.exception("OpenAI conversation failed")
            return ("I'm having a little trouble right now. Try the buttons in the queue channel!", "none", None)

    # ------------------------------------------------------------------ #
    # Intent execution
    # ------------------------------------------------------------------ #

    async def _execute_intent(
        self,
        message: discord.Message,
        action: str,
        machine_slug: str | None,
        ai_reply: str,
    ) -> None:
        """Resolve the user's active entries and execute the action."""
        # Look up user
        user = await models.get_user_by_discord_id(str(message.author.id))
        if user is None:
            await message.reply(
                "You're not in any queue right now. Head to the queue channel to join!"
            )
            return

        # Get all active entries
        entries = await models.get_user_active_entries(user["id"])
        if not entries:
            await message.reply(
                "You're not in any queue right now. Head to the queue channel to join!"
            )
            return

        # Filter by machine if specified
        if machine_slug is not None:
            entries = [e for e in entries if e["machine_slug"] == machine_slug]
            if not entries:
                await message.reply(
                    f"You don't have an active entry for **{machine_slug}**."
                )
                return

        # Multiple entries -- ask user to pick
        if len(entries) > 1:
            await message.reply(
                "You're in multiple queues — which machine?",
                view=MachinePicker(action, entries),
            )
            return

        # Single entry -- execute and reply with the AI's conversational message
        entry = entries[0]
        result = await self._do_action(action, entry)
        # Use AI reply as the primary message, append status info if different
        await message.reply(ai_reply)
        await self.bot.update_queue_embeds(entry["machine_id"])

    # ------------------------------------------------------------------ #
    # Action handler
    # ------------------------------------------------------------------ #

    async def _do_action(self, intent: str, entry: dict[str, Any]) -> str:
        """Execute an intent on a specific queue entry and return a response string."""
        machine_name = entry["machine_name"]
        status = entry["status"]
        entry_id = entry["id"]

        if intent == "done":
            if status == "serving":
                await models.update_entry_status(entry_id, "completed", job_successful=1)
                return f"Marked as done on **{machine_name}**. Thanks!"
            else:
                # waiting -- remove from queue
                await models.leave_queue(entry_id)
                return (
                    f"You weren't being served yet, so I've removed you "
                    f"from the **{machine_name}** queue."
                )

        elif intent == "more_time":
            if status == "serving":
                await models.reset_reminder(entry_id)
                return (
                    f"Got it! Timer reset on **{machine_name}**. "
                    f"I'll remind you again in {settings.reminder_minutes} minutes."
                )
            else:
                # waiting -- just tell them their position
                queue = await models.get_queue_for_machine(entry["machine_id"])
                waiting = [e for e in queue if e["status"] == "waiting"]
                pos = next(
                    (
                        idx
                        for idx, e in enumerate(waiting, start=1)
                        if e["user_id"] == entry["user_id"]
                    ),
                    None,
                )
                if pos is not None:
                    return (
                        f"You're still waiting -- **#{pos}** in line for "
                        f"**{machine_name}**. No timer to reset yet!"
                    )
                return f"You're still waiting for **{machine_name}**. No timer to reset yet!"

        elif intent == "check_position":
            if status == "serving":
                return f"You're currently being **served** at **{machine_name}**!"
            else:
                queue = await models.get_queue_for_machine(entry["machine_id"])
                waiting = [e for e in queue if e["status"] == "waiting"]
                pos = next(
                    (
                        idx
                        for idx, e in enumerate(waiting, start=1)
                        if e["user_id"] == entry["user_id"]
                    ),
                    None,
                )
                if pos is not None:
                    return f"You're **#{pos}** in the queue for **{machine_name}**."
                return f"You're in the queue for **{machine_name}**, but position could not be determined."

        elif intent == "leave":
            await models.leave_queue(entry_id)
            return f"You've been removed from the **{machine_name}** queue."

        return "I'm not sure how to handle that. Please try again."

    # ------------------------------------------------------------------ #
    # Button interaction handler
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle button presses from MachinePicker and FallbackActions views."""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id: str = interaction.data.get("custom_id", "")  # type: ignore[union-attr]

        if custom_id.startswith("dm_pick:"):
            await self._handle_machine_pick(interaction, custom_id)
        elif custom_id.startswith("dm_fallback:"):
            await self._handle_fallback(interaction, custom_id)

    async def _handle_machine_pick(
        self, interaction: discord.Interaction, custom_id: str
    ) -> None:
        """Handle dm_pick:<action>:<entry_id> button press."""
        parts = custom_id.split(":")
        if len(parts) != 3:
            return

        _, action, raw_entry_id = parts
        try:
            entry_id = int(raw_entry_id)
        except ValueError:
            return

        if action not in _VALID_INTENTS or action == "unknown":
            await interaction.response.send_message(
                "Invalid action.", ephemeral=True
            )
            return

        # Look up user to verify ownership
        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await interaction.response.send_message(
                "You're not in any queue.", ephemeral=True
            )
            return

        entries = await models.get_user_active_entries(user["id"])
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if entry is None:
            await interaction.response.send_message(
                "That queue entry is no longer active.", ephemeral=True
            )
            return

        result = await self._do_action(action, entry)
        await interaction.response.send_message(result, ephemeral=True)
        await self.bot.update_queue_embeds(entry["machine_id"])

    async def _handle_fallback(
        self, interaction: discord.Interaction, custom_id: str
    ) -> None:
        """Handle dm_fallback:<action> button press."""
        parts = custom_id.split(":")
        if len(parts) != 2:
            return

        _, action = parts
        if action not in _VALID_INTENTS or action == "unknown":
            await interaction.response.send_message(
                "Invalid action.", ephemeral=True
            )
            return

        # Resolve user entries
        user = await models.get_user_by_discord_id(str(interaction.user.id))
        if user is None:
            await interaction.response.send_message(
                "You're not in any queue right now.", ephemeral=True
            )
            return

        entries = await models.get_user_active_entries(user["id"])
        if not entries:
            await interaction.response.send_message(
                "You're not in any queue right now.", ephemeral=True
            )
            return

        if len(entries) > 1:
            await interaction.response.send_message(
                "You're in multiple queues. Which machine?",
                view=MachinePicker(action, entries),
                ephemeral=True,
            )
            return

        # Single entry -- execute directly
        entry = entries[0]
        result = await self._do_action(action, entry)
        await interaction.response.send_message(result, ephemeral=True)
        await self.bot.update_queue_embeds(entry["machine_id"])


# --------------------------------------------------------------------------- #
# Extension setup
# --------------------------------------------------------------------------- #

async def setup(bot: ReservBot) -> None:
    await bot.add_cog(DMCog(bot))
