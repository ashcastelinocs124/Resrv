"""Discord signup flow — picker view -> modal."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.queue import CollegeSelectView, SignupModal, QueueCog
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
    inter.response.send_modal = AsyncMock()
    return inter


async def test_unregistered_user_sees_select_view_not_modal(
    db, fake_bot, fake_interaction
):
    """First-time Join Queue should send the CollegeSelectView, NOT the modal."""
    cog = QueueCog(fake_bot)
    machine = await models.create_machine(name="X", slug="x")
    await cog._handle_join(fake_interaction, machine["id"])

    fake_interaction.response.send_message.assert_awaited_once()
    args, kwargs = fake_interaction.response.send_message.call_args
    assert isinstance(kwargs["view"], CollegeSelectView)
    fake_interaction.response.send_modal.assert_not_called()


async def test_select_callback_opens_modal_with_college_id(
    db, fake_bot, fake_interaction
):
    college = await models.create_college("Test Sel")
    machine = await models.create_machine(name="X2", slug="x2")
    user = await models.get_or_create_user(discord_id="555", discord_name="u")

    view = await CollegeSelectView.build(
        bot=fake_bot, user_id=user["id"], machine_id=machine["id"], prefill=None
    )
    select = view.children[0]
    select.values = [str(college["id"])]
    await view.on_select(fake_interaction, select)

    fake_interaction.response.send_modal.assert_awaited_once()
    modal = fake_interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, SignupModal)
    assert modal._college_id == college["id"]


async def test_modal_submit_calls_register_user_with_college_id(
    db, fake_bot, fake_interaction
):
    # public_mode=true skips the email-verification gate so the modal
    # synchronously registers + joins (the path this test cares about).
    from api.settings_store import set_setting
    await set_setting("public_mode", "true")

    college = await models.create_college("Submit College")
    machine = await models.create_machine(name="Y", slug="y")
    user = await models.get_or_create_user(discord_id="556", discord_name="u")
    fake_interaction.user.id = 556

    modal = SignupModal(
        bot=fake_bot, user_id=user["id"], machine_id=machine["id"],
        college_id=college["id"], prefill=None,
    )
    # Bypass discord.ui.TextInput by mocking on the instance
    modal.full_name = MagicMock(value="Sub User")
    modal.email = MagicMock(value="sub@illinois.edu")
    modal.major = MagicMock(value="CS")
    modal.graduation_year = MagicMock(value="2027")

    await modal.on_submit(fake_interaction)
    fetched = await models.get_user_by_discord_id("556")
    assert fetched["college_id"] == college["id"]
    assert fetched["registered"] == 1


async def test_resignup_prefills_existing_values(
    db, fake_bot, fake_interaction
):
    """User who is registered=0 but has prior values should see them as defaults."""
    machine = await models.create_machine(name="Z", slug="z")
    user = await models.get_or_create_user(discord_id="12345", discord_name="u")
    db_conn = await models.get_db()
    await db_conn.execute(
        "UPDATE users SET full_name=?, email=?, major=?, graduation_year=?, registered=0 "
        "WHERE id=?",
        ("Prior Name", "prior@illinois.edu", "ECE", "2026", user["id"]),
    )
    await db_conn.commit()

    cog = QueueCog(fake_bot)
    await cog._handle_join(fake_interaction, machine["id"])

    args, kwargs = fake_interaction.response.send_message.call_args
    view = kwargs["view"]
    assert view._prefill["full_name"] == "Prior Name"
    assert view._prefill["email"] == "prior@illinois.edu"


async def test_empty_colleges_list_shows_unavailable_message(
    db, fake_bot, fake_interaction
):
    machine = await models.create_machine(name="Q", slug="q")
    db_conn = await models.get_db()
    await db_conn.execute("UPDATE colleges SET archived_at = datetime('now')")
    await db_conn.commit()

    cog = QueueCog(fake_bot)
    await cog._handle_join(fake_interaction, machine["id"])

    args, kwargs = fake_interaction.response.send_message.call_args
    msg = args[0] if args else kwargs.get("content", "")
    assert "temporarily unavailable" in msg.lower()
