"""Queue embed builders and interactive button views."""

from __future__ import annotations

from typing import Any

import discord


# -- Colour map ---------------------------------------------------------------

_STATUS_COLOURS: dict[str, discord.Colour] = {
    "active": discord.Colour.green(),
    "maintenance": discord.Colour.orange(),
    "offline": discord.Colour.red(),
}


# -- Button View --------------------------------------------------------------

class QueueButtonView(discord.ui.View):
    """Persistent buttons attached to every machine embed.

    Uses ``custom_id`` so the view survives bot restarts (no in-memory state).
    ``timeout=None`` keeps the view alive indefinitely.
    """

    def __init__(self, machine_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Join Queue",
                style=discord.ButtonStyle.green,
                custom_id=f"join_queue:{machine_id}",
                emoji="\U0001F4CB",  # clipboard
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Check Position",
                style=discord.ButtonStyle.blurple,
                custom_id=f"check_position:{machine_id}",
                emoji="\U0001F50D",  # magnifying glass
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Leave Queue",
                style=discord.ButtonStyle.red,
                custom_id=f"leave_queue:{machine_id}",
                emoji="\U0001F6AA",  # door
            )
        )


# -- Embed builder ------------------------------------------------------------

def build_machine_embed(
    machine: dict[str, Any],
    queue_entries: list[dict[str, Any]],
    units: list[dict[str, Any]] | None = None,
) -> discord.Embed:
    """Build a rich embed showing machine status and its current queue.

    Parameters
    ----------
    machine:
        Row from the ``machines`` table (dict with id, name, slug, status).
    queue_entries:
        Rows from ``get_queue_for_machine`` -- must include ``discord_name``,
        ``status``, and ``position`` fields, ordered by position ASC.

    Returns
    -------
    discord.Embed
    """
    status: str = machine["status"]
    colour = _STATUS_COLOURS.get(status, discord.Colour.greyple())

    embed = discord.Embed(
        title=machine["name"],
        colour=colour,
    )

    # Status badge
    status_display = {
        "active": "Open",
        "maintenance": "Paused",
        "offline": "Offline",
    }.get(status, status.capitalize())
    embed.add_field(name="Status", value=status_display, inline=True)

    # Split queue entries
    serving = [e for e in queue_entries if e["status"] == "serving"]
    waiting = [e for e in queue_entries if e["status"] == "waiting"]

    embed.add_field(name="Waiting", value=str(len(waiting)), inline=True)

    # Units block — hidden when a machine has a single "Main" unit (back-compat)
    if units is not None and not (
        len(units) == 1 and units[0]["label"] == "Main"
    ):
        active_units = [u for u in units if u.get("archived_at") is None]
        if not active_units or all(
            u["status"] == "maintenance" for u in active_units
        ):
            embed.add_field(
                name="Units", value="_All units unavailable_", inline=False
            )
        else:
            lines = []
            for u in active_units:
                if u["status"] == "maintenance":
                    lines.append(f"\u2022 {u['label']} — \U0001F527 maintenance")
                elif u.get("serving_name"):
                    lines.append(
                        f"\u2022 {u['label']} — \U0001F535 {u['serving_name']}"
                    )
                else:
                    lines.append(f"\u2022 {u['label']} — \U0001F7E2 available")
            embed.add_field(name="Units", value="\n".join(lines), inline=False)

    # Currently serving
    if serving:
        serving_entry = serving[0]
        embed.add_field(
            name="Now Serving",
            value=serving_entry["discord_name"],
            inline=False,
        )
    else:
        embed.add_field(name="Now Serving", value="--", inline=False)

    # Waiting list
    if waiting:
        lines: list[str] = []
        for idx, entry in enumerate(waiting, start=1):
            lines.append(f"**{idx}.** {entry['discord_name']}")
            if idx >= 10:
                remaining = len(waiting) - 10
                if remaining > 0:
                    lines.append(f"*...and {remaining} more*")
                break
        embed.add_field(name="Queue", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Queue", value="*No one waiting*", inline=False)

    embed.set_footer(text=f"Machine: {machine['slug']}")
    return embed
