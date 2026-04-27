"""Leave-while-serving two-option flow."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.queue import LeaveServingView, QueueCog
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_bot():
    bot = MagicMock()
    bot.update_queue_embeds = AsyncMock()
    return bot


@pytest.fixture
def fake_interaction():
    inter = MagicMock()
    inter.user.id = 12345
    inter.user.display_name = "tester"
    inter.user.send = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    return inter


async def _make_serving_entry(discord_id: str = "leave-serve"):
    user = await models.get_or_create_user(discord_id=discord_id, discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    await models.update_entry_status(entry["id"], "serving")
    return entry, user, machines[0]


async def _make_waiting_entry(discord_id: str = "leave-wait"):
    user = await models.get_or_create_user(discord_id=discord_id, discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    return entry, user, machines[0]


async def test_waiting_user_leave_unchanged(db, fake_bot, fake_interaction):
    """Waiting users still get the immediate-leave path with no view."""
    entry, user, machine = await _make_waiting_entry("leave-w-1")
    fake_interaction.user.id = int(user["discord_id"]) if user["discord_id"].isdigit() else 12345
    # Patch get_user_by_discord_id since fake_interaction.user.id is a MagicMock-ish int
    with patch("bot.cogs.queue.models.get_user_by_discord_id",
               return_value=user):
        cog = QueueCog(fake_bot)
        await cog._handle_leave(fake_interaction, machine["id"])

    fake_interaction.response.send_message.assert_awaited_once()
    args, kwargs = fake_interaction.response.send_message.call_args
    # No view kwarg -> immediate leave path
    assert "view" not in kwargs or kwargs.get("view") is None
    refreshed = await models.get_user_active_entry(user["id"], machine["id"])
    assert refreshed is None  # cancelled


async def test_serving_user_leave_shows_view(db, fake_bot, fake_interaction):
    entry, user, machine = await _make_serving_entry("leave-s-1")
    with patch("bot.cogs.queue.models.get_user_by_discord_id",
               return_value=user):
        cog = QueueCog(fake_bot)
        await cog._handle_leave(fake_interaction, machine["id"])

    fake_interaction.response.send_message.assert_awaited_once()
    args, kwargs = fake_interaction.response.send_message.call_args
    assert isinstance(kwargs["view"], LeaveServingView)
    # Entry untouched until they pick
    refreshed = await models.get_user_active_entry(user["id"], machine["id"])
    assert refreshed is not None
    assert refreshed["status"] == "serving"


async def test_finish_early_completes_and_sends_rating_dm(
    db, fake_bot, fake_interaction
):
    entry, user, machine = await _make_serving_entry("leave-finish")
    view = LeaveServingView(
        bot=fake_bot, entry_id=entry["id"],
        machine_id=machine["id"], machine_name=machine["name"],
    )
    finish_btn = next(b for b in view.children
                      if getattr(b, "_mode", None) == "finish")

    with patch("bot.cogs.dm.send_rating_dm", new=AsyncMock()) as mock_send:
        await finish_btn.callback(fake_interaction)
        mock_send.assert_awaited_once()
        kw = mock_send.call_args.kwargs
        assert kw["queue_entry_id"] == entry["id"]
        assert kw["machine_name"] == machine["name"]

    refreshed = await models.get_user_active_entry(user["id"], machine["id"])
    assert refreshed is None  # no longer active (completed)
    fake_interaction.response.edit_message.assert_awaited_once()
    fake_bot.update_queue_embeds.assert_awaited_once_with(machine["id"])


async def test_cancel_session_marks_cancelled_no_rating(
    db, fake_bot, fake_interaction
):
    entry, user, machine = await _make_serving_entry("leave-cancel")
    view = LeaveServingView(
        bot=fake_bot, entry_id=entry["id"],
        machine_id=machine["id"], machine_name=machine["name"],
    )
    cancel_btn = next(b for b in view.children
                      if getattr(b, "_mode", None) == "cancel")

    with patch("bot.cogs.dm.send_rating_dm", new=AsyncMock()) as mock_send:
        await cancel_btn.callback(fake_interaction)
        mock_send.assert_not_awaited()

    refreshed = await models.get_user_active_entry(user["id"], machine["id"])
    assert refreshed is None
    # No feedback row for this entry
    assert await models.get_feedback_by_entry(entry["id"]) is None
    fake_interaction.response.edit_message.assert_awaited_once()
    fake_bot.update_queue_embeds.assert_awaited_once_with(machine["id"])
