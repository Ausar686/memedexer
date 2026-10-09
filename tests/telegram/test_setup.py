import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat, SetMyCommands
from aiogram.types import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from memedexer.config import Settings
from memedexer.telegram.setup import GROUP_COMMANDS, PRIVATE_COMMANDS, prepare_bot
from tests.telegram.fake_api import FakeTelegram


async def test_sets_command_menus_and_checks_owner(bot: Bot, telegram: FakeTelegram, settings: Settings) -> None:
    await prepare_bot(bot, settings)

    group, private = telegram.calls(SetMyCommands)
    assert (group.commands, type(group.scope)) == (GROUP_COMMANDS, BotCommandScopeAllGroupChats)
    assert (private.commands, type(private.scope)) == (PRIVATE_COMMANDS, BotCommandScopeAllPrivateChats)
    assert [call.chat_id for call in telegram.calls(GetChat)] == [settings.owner_user_id]


async def test_failures_are_logged_not_raised(
    bot: Bot, telegram: FakeTelegram, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    telegram.errors[SetMyCommands] = TelegramBadRequest(SetMyCommands(commands=[]), "bad")
    telegram.errors[GetChat] = TelegramBadRequest(GetChat(chat_id=1), "chat not found")

    await prepare_bot(bot, settings)

    assert "could not set the command menu" in caplog.text
    assert "send /start to the bot" in caplog.text
