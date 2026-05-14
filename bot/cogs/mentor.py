"""Mentor shifts — Start/End buttons in #reserve-shifts + training routing.

Clicking **Start Shift** in the panel opens a row in ``mentor_shifts``.
Any unassigned trainees are then fanned out to whichever on-shift mentor
currently has the fewest active assignments. Clicking **End Shift** closes
the row.

When a trainee joins the queue with ``purpose='training'``, the join helper
calls :func:`assign_mentor_for_training_entry`, which picks a free mentor
and DMs both parties so the mentor can help with the training session. If
no mentor is on shift, the trainee is told they'll be notified when one
comes on.

Channel access is gated by the ``The Shop Team`` Discord role — no
dedicated ``mentor`` Discord role is used; "mentor" is purely an internal
concept tracked via ``mentor_shifts.discord_id``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from config import settings
from db import models
from api.settings_store import get_setting, set_setting

if TYPE_CHECKING:
    from bot.bot import ReservBot

log = logging.getLogger(__name__)

LEGACY_MENTOR_ROLE_NAME = "mentor"  # cleaned up on startup if found
SHOP_ROLE_NAME = "the shop team"    # matched case-insensitive
PANEL_MESSAGE_SETTING = "mentor_panel_message_id"
PANEL_CHANNEL_SETTING = "mentor_panel_channel_id"
SHIFTS_CHANNEL_NAME = "reserve-shifts"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _display_name(member: discord.abc.User, full_name: str | None) -> str:
    """Prefer a user's registered SCD full name, fall back to Discord name."""
    return (full_name or "").strip() or member.display_name


async def _resolve_member(
    bot: ReservBot, guild: discord.Guild, discord_id: str
) -> discord.Member | None:
    """Best-effort member lookup that survives an empty member cache."""
    try:
        uid = int(discord_id)
    except (TypeError, ValueError):
        return None
    member = guild.get_member(uid)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(uid)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def _send_dm(bot: ReservBot, discord_id: str, content: str) -> bool:
    """Send a DM, swallowing the usual unavailability errors."""
    try:
        user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
        await user.send(content)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False
    except Exception:
        log.exception("Unexpected error DMing %s", discord_id)
        return False


# --------------------------------------------------------------------------- #
# Discord role + channel setup
# --------------------------------------------------------------------------- #

async def delete_legacy_mentor_role(guild: discord.Guild) -> None:
    """One-shot: remove the legacy ``mentor`` role if it still exists.

    Earlier versions of this cog auto-created a Discord role to track shift
    state visually. The role isn't used anymore — access is gated by
    ``The Shop Team`` and shift state lives in ``mentor_shifts``. Idempotent:
    silently no-ops when the role is already gone.
    """
    role = discord.utils.get(guild.roles, name=LEGACY_MENTOR_ROLE_NAME)
    if role is None:
        return
    try:
        await role.delete(
            reason="Reserv: legacy mentor role no longer used; Shop Team gates access"
        )
        log.info(
            "Deleted legacy '%s' role from guild %d",
            LEGACY_MENTOR_ROLE_NAME, guild.id,
        )
    except discord.Forbidden:
        log.warning(
            "Cannot delete legacy '%s' role -- bot lacks Manage Roles or "
            "the role sits above the bot in the hierarchy",
            LEGACY_MENTOR_ROLE_NAME,
        )
    except Exception:
        log.exception(
            "Failed to delete legacy '%s' role", LEGACY_MENTOR_ROLE_NAME
        )


async def ensure_shifts_channel(
    guild: discord.Guild,
) -> discord.TextChannel | None:
    """Idempotently ensure a ``reserve-shifts`` text channel exists.

    Left open (no explicit permission overwrites) for now -- visible to
    @everyone using the guild's default. Returns the channel, or ``None`` if
    the bot lacks Manage Channels.
    """
    existing = discord.utils.get(guild.text_channels, name=SHIFTS_CHANNEL_NAME)
    if existing is not None:
        return existing
    try:
        channel = await guild.create_text_channel(
            name=SHIFTS_CHANNEL_NAME,
            reason="Reserv: auto-created shifts channel",
        )
        log.info("Created '#%s' channel in guild %d", SHIFTS_CHANNEL_NAME, guild.id)
        return channel
    except discord.Forbidden:
        log.warning(
            "Cannot create '#%s' -- bot lacks Manage Channels in guild %d",
            SHIFTS_CHANNEL_NAME, guild.id,
        )
    except Exception:
        log.exception("Failed to create '#%s'", SHIFTS_CHANNEL_NAME)
    return None


async def lock_shifts_channel(
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> None:
    """Restrict ``#reserve-shifts`` to the Shop Team role.

    Denies View Channel for ``@everyone``, grants it to whatever role matches
    ``SHOP_ROLE_NAME`` case-insensitively. The bot member is explicitly granted
    view+send so it can keep refreshing the panel. Idempotent; logs and
    continues on permission failures.
    """
    shop_role = next(
        (r for r in guild.roles if r.name.lower() == SHOP_ROLE_NAME),
        None,
    )

    targets: list[tuple[discord.abc.Snowflake, dict[str, bool | None]]] = [
        (guild.default_role, {"view_channel": False}),
    ]
    if shop_role is not None:
        targets.append((shop_role, {"view_channel": True}))
    else:
        log.warning(
            "No role matching '%s' (case-insensitive) found in guild %d -- "
            "no one will see #%s except admins",
            SHOP_ROLE_NAME, guild.id, channel.name,
        )

    if guild.me is not None:
        targets.append(
            (guild.me, {"view_channel": True, "send_messages": True})
        )

    for target, overwrite_kwargs in targets:
        try:
            await channel.set_permissions(
                target,
                **overwrite_kwargs,
                reason="Reserv: lock #reserve-shifts to Shop Team",
            )
        except discord.Forbidden:
            log.warning(
                "Cannot set perms on #%s for %r -- bot lacks Manage Channels",
                channel.name, getattr(target, "name", target),
            )
            return
        except Exception:
            log.exception(
                "Failed to set perms on #%s for %r",
                channel.name, getattr(target, "name", target),
            )

    log.info(
        "Locked #%s to %s",
        channel.name,
        shop_role.name if shop_role else "admins only (no Shop Team role found)",
    )


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

class MentorPanelView(discord.ui.View):
    """Persistent view with Start/End shift buttons."""

    def __init__(self, bot: ReservBot) -> None:
        super().__init__(timeout=None)
        self._bot = bot

    @discord.ui.button(
        label="Start Shift",
        style=discord.ButtonStyle.green,
        custom_id="mentor:start_shift",
    )
    async def start_shift(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _handle_start_shift(self._bot, interaction)

    @discord.ui.button(
        label="End Shift",
        style=discord.ButtonStyle.red,
        custom_id="mentor:end_shift",
    )
    async def end_shift(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _handle_end_shift(self._bot, interaction)


async def _build_panel_embed() -> discord.Embed:
    open_shifts = await models.list_open_mentor_shifts()
    embed = discord.Embed(
        title="Shop Team Shifts",
        description=(
            "Click **Start Shift** when you arrive — trainees who join the "
            "queue while you're on shift will be routed to you for training "
            "help. Click **End Shift** when you leave."
        ),
        colour=discord.Colour.blurple(),
    )
    if open_shifts:
        lines = [
            f"• <@{s['discord_id']}> (since {s['started_at']} UTC)"
            for s in open_shifts
        ]
        embed.add_field(
            name=f"On shift ({len(open_shifts)})",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="On shift",
            value="_No one is on shift right now._",
            inline=False,
        )
    return embed


async def post_or_refresh_panel(bot: ReservBot) -> None:
    """Post the persistent mentor panel in the ``#reserve-shifts`` channel.

    Tracks both the channel and message ID in settings so the panel can move
    cleanly between channels. On first run after the panel moves from another
    channel, deletes the stale message there.
    """
    guild = bot.get_guild(settings.discord_guild_id)
    if guild is None:
        log.warning(
            "Guild %d not in cache -- cannot post mentor panel",
            settings.discord_guild_id,
        )
        return

    target = discord.utils.get(guild.text_channels, name=SHIFTS_CHANNEL_NAME)
    if target is None:
        target = await ensure_shifts_channel(guild)
    if target is None:
        log.warning("No #%s channel available -- skipping mentor panel", SHIFTS_CHANNEL_NAME)
        return

    embed = await _build_panel_embed()
    view = MentorPanelView(bot)

    stored_channel_id = await get_setting(PANEL_CHANNEL_SETTING)
    stored_message_id = await get_setting(PANEL_MESSAGE_SETTING)

    # If the panel previously lived elsewhere, delete the old message so we
    # don't leave a duplicate (still-functional) panel behind.
    if (
        stored_channel_id
        and stored_message_id
        and stored_channel_id != str(target.id)
    ):
        try:
            old_ch = bot.get_channel(int(stored_channel_id))
            if isinstance(old_ch, discord.TextChannel):
                old_msg = await old_ch.fetch_message(int(stored_message_id))
                await old_msg.delete()
                log.info(
                    "Removed stale mentor panel from channel %d",
                    old_ch.id,
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            pass
        stored_message_id = None  # force repost in the new channel
    # Backwards-compat: pre-migration we only stored the message_id and it was
    # always in the admin channel. If channel_id wasn't recorded but a message
    # exists in admin_channel_id, sweep it.
    elif (
        stored_message_id
        and not stored_channel_id
        and settings.admin_channel_id
        and settings.admin_channel_id != target.id
    ):
        try:
            admin_ch = bot.get_channel(settings.admin_channel_id)
            if isinstance(admin_ch, discord.TextChannel):
                old_msg = await admin_ch.fetch_message(int(stored_message_id))
                await old_msg.delete()
                log.info("Removed legacy admin-channel mentor panel")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            pass
        stored_message_id = None

    message: discord.Message | None = None
    if stored_message_id:
        try:
            message = await target.fetch_message(int(stored_message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            message = None

    if message is None:
        try:
            message = await target.send(embed=embed, view=view)
            await set_setting(PANEL_CHANNEL_SETTING, str(target.id))
            await set_setting(PANEL_MESSAGE_SETTING, str(message.id))
            log.info(
                "Posted mentor panel in #%s (message %d)",
                target.name, message.id,
            )
        except discord.Forbidden:
            log.warning("Cannot post mentor panel -- bot lacks Send perms")
    else:
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            log.exception("Failed to refresh mentor panel")


# --------------------------------------------------------------------------- #
# Button handlers
# --------------------------------------------------------------------------- #

async def _handle_start_shift(
    bot: ReservBot, interaction: discord.Interaction
) -> None:
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "Start Shift can only be used inside the server.", ephemeral=True
        )
        return

    discord_id = str(interaction.user.id)
    try:
        await models.start_mentor_shift(discord_id)
    except models.MentorShiftAlreadyOpenError:
        await interaction.response.send_message(
            "You're already on shift. Click **End Shift** first if you want "
            "to restart.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "You're on shift — trainees joining the queue will be routed to you "
        "for training help.",
        ephemeral=True,
    )

    # Sweep unassigned trainees onto whoever is now free.
    assigned = await _sweep_unassigned_trainees(bot)
    if assigned:
        await interaction.followup.send(
            f"Picked up {assigned} unassigned trainee(s) waiting for a mentor.",
            ephemeral=True,
        )

    await post_or_refresh_panel(bot)


async def _handle_end_shift(
    bot: ReservBot, interaction: discord.Interaction
) -> None:
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "End Shift can only be used inside the server.", ephemeral=True
        )
        return

    discord_id = str(interaction.user.id)
    row = await models.end_mentor_shift(discord_id)
    if row is None:
        await interaction.response.send_message(
            "You don't have an open shift to end.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "Shift ended. Thanks for helping out!", ephemeral=True
    )
    await post_or_refresh_panel(bot)


async def _sweep_unassigned_trainees(bot: ReservBot) -> int:
    """Assign any unassigned training trainees to on-shift mentors. Returns count."""
    unassigned = await models.get_unassigned_training_entries()
    assigned = 0
    for entry in unassigned:
        mentor_id = await models.pick_free_mentor()
        if mentor_id is None:
            break
        await models.assign_mentor_to_entry(entry["id"], mentor_id)
        await _notify_pair(bot, entry, mentor_id)
        assigned += 1
    return assigned


# --------------------------------------------------------------------------- #
# Public entrypoints used by the queue join flow
# --------------------------------------------------------------------------- #

async def assign_mentor_for_training_entry(
    bot: ReservBot, entry: dict
) -> str | None:
    """Pick a free mentor, stamp the entry, and DM both parties.

    Returns the mentor's display name, or ``None`` if no mentor was on shift.
    The trainee gets a fallback DM either way (the queue join helper sends
    the main join-confirmation; this function only adds the mentor-context
    line). Caller is responsible for sending the join confirmation itself.
    """
    mentor_id = await models.pick_free_mentor()
    if mentor_id is None:
        await _send_dm(
            bot,
            entry["discord_id"],
            "A Shop Team member will be assigned to help with your training "
            "as soon as one comes on shift.",
        )
        return None

    await models.assign_mentor_to_entry(entry["id"], mentor_id)
    return await _notify_pair(bot, entry, mentor_id)


async def _notify_pair(
    bot: ReservBot, entry: dict, mentor_discord_id: str
) -> str | None:
    """DM trainee + mentor about the pairing. Returns mentor display name."""
    machine_name = entry.get("machine_name") or "the machine"
    trainee_label = (entry.get("full_name") or entry.get("discord_name") or "a trainee").strip()

    mentor_label = mentor_discord_id  # fallback
    mentor_user = await models.get_user_by_discord_id(mentor_discord_id)
    if mentor_user:
        mentor_label = (
            mentor_user.get("full_name")
            or mentor_user.get("discord_name")
            or mentor_discord_id
        )

    await _send_dm(
        bot,
        entry["discord_id"],
        f"**{mentor_label}** is on shift and will help with your "
        f"**{machine_name}** training. They've been notified.",
    )
    await _send_dm(
        bot,
        mentor_discord_id,
        f"You've been assigned to help **{trainee_label}** with their "
        f"**{machine_name}** training.",
    )
    return mentor_label


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #

class MentorCog(commands.Cog):
    """Holds the persistent view registration and exposes the public hooks."""

    def __init__(self, bot: ReservBot) -> None:
        self.bot = bot
        # Register the persistent view so buttons survive restarts.
        bot.add_view(MentorPanelView(bot))


async def setup(bot: ReservBot) -> None:
    await bot.add_cog(MentorCog(bot))
