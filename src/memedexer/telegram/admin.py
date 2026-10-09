"""Group commands: show settings to anyone; change them (and force a recaption) as a chat admin."""

import html
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.captioning.catalog import MODELS, find_model
from memedexer.config import Settings
from memedexer.pipeline.budget import limits_microusd, period_starts
from memedexer.pipeline.resolve import resolve_caption_settings
from memedexer.storage import repo
from memedexer.storage.models import DESCRIPTION_LANGUAGE_MAX_LENGTH, Chat, ChatStatus, JobTrigger, Topic, utcnow
from memedexer.telegram.handlers import Notifier
from memedexer.telegram.media import extract_image, topic_id

_in_group = F.chat.type.in_({"group", "supergroup"})
_LANGUAGE = re.compile(rf"^[^\W\d_][\w\s()-]{{0,{DESCRIPTION_LANGUAGE_MAX_LENGTH - 1}}}$")

NOT_APPROVED = "This chat is waiting for the bot owner's approval."
ADMINS_ONLY = "Only chat admins can change this."
MODEL_SOURCES = {"topic": "set for this topic", "chat": "set for this chat", "default": "default"}


def build_admin_router() -> Router:
    router = Router(name="admin")
    router.message.register(show_settings, _in_group, Command("settings"))
    router.message.register(toggle_captions, _in_group, Command("captions"))
    router.message.register(set_model, _in_group, Command("model"))
    router.message.register(set_language, _in_group, Command("language"))
    router.message.register(recaption, _in_group, Command("recaption"))
    return router


async def is_chat_admin(bot: Bot, message: Message, settings: Settings) -> bool:
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return True  # an anonymous admin posting as the group
    if message.from_user is None:
        return False
    if message.from_user.id == settings.owner_user_id:
        return True
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


async def _approved_chat(session: AsyncSession, message: Message) -> Chat | None:
    chat = await repo.get_chat(session, message.chat.id)
    if chat is None or chat.status is not ChatStatus.APPROVED:
        await message.reply(NOT_APPROVED)
        return None
    return chat


def _usd(microusd: int) -> str:
    return f"${microusd / 1_000_000:.2f}"


def _available_models(settings: Settings) -> str:
    ids = [spec.id for spec in MODELS.values() if spec.provider in settings.available_providers]
    return ", ".join(f"<code>{model_id}</code>" for model_id in ids)


async def show_settings(
    message: Message, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with sessionmaker() as session:
        chat = await _approved_chat(session, message)
        if chat is None:
            return
        topic = await session.get(Topic, (chat.id, topic_id(message)))
        resolved = resolve_caption_settings(chat, topic, settings)
        limits = limits_microusd(settings)
        spent = {
            period: await repo.spent_microusd(session, chat.id, start)
            for period, start in period_starts(utcnow()).items()
        }
    enabled = topic is not None and topic.captioning_enabled
    language = html.escape(resolved.description_language) if resolved.description_language else "the meme's language"
    budget = " · ".join(f"{period} {_usd(spent[period])} / {_usd(limits[period])}" for period in limits)
    await message.reply(
        f"<b>Captioning here:</b> {'on' if enabled else 'off'}\n"
        f"<b>Model:</b> <code>{resolved.model.id}</code> ({MODEL_SOURCES[resolved.model_source]})\n"
        f"<b>Description language:</b> {language}\n"
        f"<b>Spent:</b> {budget}\n"
        f"<b>Available models:</b> {_available_models(settings)}"
    )


async def toggle_captions(
    message: Message,
    command: CommandObject,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    if command.args not in ("on", "off"):
        await message.reply("Usage: <code>/captions on</code> or <code>/captions off</code> (applies to this topic).")
        return
    if not await is_chat_admin(bot, message, settings):
        await message.reply(ADMINS_ONLY)
        return
    async with sessionmaker() as session:
        chat = await _approved_chat(session, message)
        if chat is None:
            return
        topic = await repo.get_or_create_topic(session, chat.id, topic_id(message))
        topic.captioning_enabled = command.args == "on"
        await session.commit()
    await message.reply(f"Captioning is now {command.args} here.")


async def set_model(
    message: Message,
    command: CommandObject,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """`/model [chat] <model_id>|reset`; without `chat` it applies to the current topic."""
    args = (command.args or "").split()
    scope = "chat" if args[:1] == ["chat"] else "topic"
    if scope == "chat":
        args = args[1:]
    if len(args) != 1:
        await message.reply(
            "Usage: <code>/model [chat] &lt;model&gt;</code> or <code>/model [chat] reset</code>.\n"
            f"Available models: {_available_models(settings)}"
        )
        return
    spec = None
    if args[0] != "reset":
        spec = find_model(args[0])
        if spec is None or spec.provider not in settings.available_providers:
            await message.reply(
                f"Unknown model <code>{html.escape(args[0])}</code>. Available: {_available_models(settings)}"
            )
            return
    if not await is_chat_admin(bot, message, settings):
        await message.reply(ADMINS_ONLY)
        return
    async with sessionmaker() as session:
        chat = await _approved_chat(session, message)
        if chat is None:
            return
        target = chat if scope == "chat" else await repo.get_or_create_topic(session, chat.id, topic_id(message))
        target.provider = spec.provider if spec else None
        target.model = spec.id if spec else None
        await session.commit()
    where = "this chat" if scope == "chat" else "this topic"
    if spec is None:
        await message.reply(f"Model override for {where} removed.")
    else:
        await message.reply(f"Model for {where} set to <code>{spec.id}</code>.")


async def set_language(
    message: Message,
    command: CommandObject,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """`/language <name>|reset` for the whole chat; reset means "the meme's own language"."""
    value = (command.args or "").strip()
    if value != "reset" and not _LANGUAGE.match(value):
        await message.reply(
            "Usage: <code>/language Russian</code> or <code>/language reset</code> (use the meme's language)."
        )
        return
    if not await is_chat_admin(bot, message, settings):
        await message.reply(ADMINS_ONLY)
        return
    async with sessionmaker() as session:
        chat = await _approved_chat(session, message)
        if chat is None:
            return
        chat.description_language = None if value == "reset" else value
        await session.commit()
    if value == "reset":
        await message.reply("Descriptions will use each meme's own language.")
    else:
        await message.reply(f"Descriptions will be written in {html.escape(value)}.")


async def recaption(
    message: Message,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    worker: Notifier,
) -> None:
    target = message.reply_to_message
    image = extract_image(target) if target is not None else None
    if image is None:
        await message.reply("Reply to an image with <code>/recaption</code>.")
        return
    if not await is_chat_admin(bot, message, settings):
        await message.reply(ADMINS_ONLY)
        return
    async with sessionmaker() as session:
        chat = await _approved_chat(session, message)
        if chat is None:
            return
        await repo.upsert_media(
            session,
            file_unique_id=image.file_unique_id,
            file_id=image.file_id,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            file_size=image.file_size,
            photo_file_id=image.photo_file_id,
        )
        await repo.enqueue_job(
            session,
            chat_id=chat.id,
            thread_id=topic_id(target),
            message_id=target.message_id,
            file_unique_id=image.file_unique_id,
            trigger=JobTrigger.RECAPTION,
        )
        await session.commit()
    worker.notify()
    await message.reply("Queued a fresh caption.")
