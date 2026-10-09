"""One-time bot preparation at startup: command menus and a check that the owner can be reached."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from memedexer.config import Settings

log = logging.getLogger(__name__)

GROUP_COMMANDS = [
    BotCommand(command="settings", description="Show captioning settings for this topic"),
    BotCommand(command="captions", description="on|off: caption images in this topic (admins)"),
    BotCommand(command="model", description="[chat] <model>|reset: choose the model (admins)"),
    BotCommand(command="language", description="<language>|reset: description language (admins)"),
    BotCommand(command="recaption", description="Reply to an image to caption it again (admins)"),
]
PRIVATE_COMMANDS = [
    BotCommand(command="start", description="About this bot"),
    BotCommand(command="chats", description="List chats and approve or revoke them (owner)"),
]


async def prepare_bot(bot: Bot, settings: Settings) -> None:
    """Best effort: a failure here shouldn't keep the bot from captioning."""
    try:
        await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError as exc:
        log.warning("could not set the command menu: %s", exc)
    try:
        await bot.get_chat(settings.owner_user_id)
    except TelegramAPIError as exc:
        log.warning("the owner %d can't be reached; send /start to the bot from that account: %s",
                    settings.owner_user_id, exc)
    return None
