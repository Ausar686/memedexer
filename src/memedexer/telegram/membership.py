"""Chat lifecycle: owner approval of new chats via DM, revocation, removal and group→supergroup migration."""

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER, ChatMemberUpdatedFilter, Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.config import Settings
from memedexer.storage import repo
from memedexer.storage.models import Chat, ChatStatus

log = logging.getLogger(__name__)

_in_group = F.chat.type.in_({"group", "supergroup"})
_in_private = F.chat.type == "private"

APPROVED_NOTICE = (
    "✅ This chat is approved for meme captioning.\n"
    "Admins: send <code>/captions on</code> in a topic to caption images posted there; "
    "<code>/settings</code> shows the current setup.\n"
    "Images from enabled topics are sent to a third-party AI provider (Anthropic or OpenAI) to generate captions."
)


class ChatAction(CallbackData, prefix="chat"):
    action: str
    chat_id: int


def build_membership_router() -> Router:
    router = Router(name="membership")
    router.my_chat_member.register(bot_joined, _in_group, ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
    router.my_chat_member.register(bot_left, _in_group, ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
    router.message.register(chat_migrated, F.migrate_to_chat_id)
    router.message.register(chat_migrated_in, F.migrate_from_chat_id)
    router.message.register(start, _in_private, Command("start"))
    router.message.register(list_chats, _in_private, Command("chats"))
    router.callback_query.register(on_chat_action, ChatAction.filter())
    return router


def describe_chat(chat: Chat) -> str:
    title = html.escape(chat.title or "untitled")
    return f"<b>{title}</b> (<code>{chat.id}</code>, {'forum' if chat.is_forum else 'group'})"


def _approval_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Approve", callback_data=ChatAction(action="approve", chat_id=chat_id))
    keyboard.button(text="Reject", callback_data=ChatAction(action="reject", chat_id=chat_id))
    return keyboard.as_markup()


async def notify_owner(bot: Bot, settings: Settings, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    """DM the owner; False when they haven't started the bot (Telegram forbids bots from messaging first)."""
    try:
        await bot.send_message(settings.owner_user_id, text, reply_markup=markup)
    except TelegramAPIError as exc:
        log.warning("cannot DM the owner (have they sent /start to the bot?): %s", exc)
        return False
    return True


async def bot_joined(
    event: ChatMemberUpdated, bot: Bot, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with sessionmaker() as session:
        chat = await repo.upsert_chat(
            session,
            event.chat.id,
            title=event.chat.title,
            is_forum=bool(event.chat.is_forum),
            added_by_user_id=event.from_user.id,
        )
        if chat.status is ChatStatus.APPROVED:
            await session.commit()
            return
        chat.added_by_user_id = event.from_user.id
        chat.status = ChatStatus.PENDING
        await session.commit()
    adder = html.escape(event.from_user.full_name)
    await notify_owner(
        bot,
        settings,
        f"The bot was added to {describe_chat(chat)} by {adder} (<code>{event.from_user.id}</code>).",
        _approval_keyboard(chat.id),
    )


async def bot_left(event: ChatMemberUpdated, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        chat = await repo.get_chat(session, event.chat.id)
        # Keep REJECTED as is: leaving is how a rejection takes effect.
        if chat is not None and chat.status in (ChatStatus.APPROVED, ChatStatus.PENDING):
            chat.status = ChatStatus.REVOKED
            await session.commit()


async def chat_migrated(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    await _migrate(sessionmaker, message.chat.id, message.migrate_to_chat_id)


async def chat_migrated_in(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    await _migrate(sessionmaker, message.migrate_from_chat_id, message.chat.id)


async def _migrate(sessionmaker: async_sessionmaker[AsyncSession], old_id: int, new_id: int) -> None:
    async with sessionmaker() as session:
        if await repo.migrate_chat(session, old_id, new_id):
            await session.commit()
            log.info("chat %d migrated to %d", old_id, new_id)


async def start(message: Message, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        await message.answer("This bot is private.")
        return
    await message.answer(
        "Hi! I'll DM you whenever I'm added to a chat so you can approve it.\n"
        "<code>/chats</code> lists chats and lets you approve or revoke them."
    )


async def list_chats(message: Message, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    if message.from_user is None or message.from_user.id != settings.owner_user_id:
        return
    async with sessionmaker() as session:
        chats = [c for c in await repo.list_chats(session) if c.status in (ChatStatus.APPROVED, ChatStatus.PENDING)]
    if not chats:
        await message.answer("No approved or pending chats.")
        return
    keyboard = InlineKeyboardBuilder()
    lines = []
    for chat in chats:
        lines.append(f"{'✅' if chat.status is ChatStatus.APPROVED else '⏳'} {describe_chat(chat)}")
        title = (chat.title or str(chat.id))[:24]
        if chat.status is ChatStatus.APPROVED:
            keyboard.button(text=f"Revoke {title}", callback_data=ChatAction(action="revoke", chat_id=chat.id))
        else:
            keyboard.button(text=f"Approve {title}", callback_data=ChatAction(action="approve", chat_id=chat.id))
            keyboard.button(text=f"Reject {title}", callback_data=ChatAction(action="reject", chat_id=chat.id))
    keyboard.adjust(1)
    await message.answer("\n".join(lines), reply_markup=keyboard.as_markup())


async def on_chat_action(
    query: CallbackQuery,
    callback_data: ChatAction,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    if query.from_user.id != settings.owner_user_id:
        await query.answer("Only the bot owner can do this.", show_alert=True)
        return
    async with sessionmaker() as session:
        chat = await repo.get_chat(session, callback_data.chat_id)
        if chat is None:
            await query.answer("Unknown chat.", show_alert=True)
            return
        match callback_data.action:
            case "approve":
                chat.status = ChatStatus.APPROVED
            case "reject":
                chat.status = ChatStatus.REJECTED
            case "revoke":
                chat.status = ChatStatus.REVOKED
            case _:
                await query.answer("Unknown action.", show_alert=True)
                return
        await session.commit()

    if chat.status is ChatStatus.APPROVED:
        await _send_quietly(bot.send_message(chat.id, APPROVED_NOTICE))
    else:
        await _send_quietly(bot.leave_chat(chat.id))
    outcome = {ChatStatus.APPROVED: "approved", ChatStatus.REJECTED: "rejected", ChatStatus.REVOKED: "revoked"}
    await query.answer(f"Chat {outcome[chat.status]}.")
    if isinstance(query.message, Message):
        await _send_quietly(query.message.edit_text(f"{describe_chat(chat)}: {outcome[chat.status]}."))


async def _send_quietly(call: object) -> None:
    """The chat or DM may be gone already; the status change in the database is what matters."""
    try:
        await call
    except TelegramAPIError as exc:
        log.warning("telegram call failed: %s", exc)
