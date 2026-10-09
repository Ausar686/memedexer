import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, LeaveChat, SendMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.storage import repo
from memedexer.storage.models import Chat, ChatStatus, Topic
from memedexer.telegram.membership import APPROVED_NOTICE, ChatAction
from tests.factories import CHAT_ID, seed_chat
from tests.telegram.fake_api import FakeTelegram
from tests.telegram.updates import administrator, callback_update, member_update, update

OWNER = 1


def private_update(user_id: int, text: str) -> object:
    return update(chat_id=user_id, chat_type="private", thread_id=None, user_id=user_id, text=text)


async def chat_status(sessionmaker: async_sessionmaker[AsyncSession], chat_id: int = CHAT_ID) -> ChatStatus | None:
    async with sessionmaker() as session:
        chat = await session.get(Chat, chat_id)
        return None if chat is None else chat.status


async def press(dispatcher: Dispatcher, bot: Bot, action: str, *, user_id: int = OWNER) -> None:
    data = ChatAction(action=action, chat_id=CHAT_ID).pack()
    await dispatcher.feed_update(bot, callback_update(data, user_id=user_id))


async def test_join_registers_pending_chat_and_asks_owner(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, sessionmaker: async_sessionmaker
) -> None:
    await dispatcher.feed_update(bot, member_update(old="left", new="member", title="<memes>", by=5))

    async with sessionmaker() as session:
        chat = await session.get(Chat, CHAT_ID)
    assert (chat.status, chat.title, chat.is_forum, chat.added_by_user_id) == (ChatStatus.PENDING, "<memes>", True, 5)
    [dm] = telegram.calls(SendMessage)
    assert dm.chat_id == OWNER
    assert "&lt;memes&gt;" in dm.text and "Bob &lt;Admin&gt;" in dm.text
    buttons = [b.callback_data for b in dm.reply_markup.inline_keyboard[0]]
    assert [ChatAction.unpack(b).action for b in buttons] == ["approve", "reject"]
    assert {ChatAction.unpack(b).chat_id for b in buttons} == {CHAT_ID}


async def test_rejoin_of_approved_chat_does_not_ask_again(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, member_update(old="left", new="member"))

    assert await chat_status(sessionmaker) is ChatStatus.APPROVED
    assert telegram.calls(SendMessage) == []


@pytest.mark.parametrize("previous", [ChatStatus.REJECTED, ChatStatus.REVOKED])
async def test_rejoin_after_reject_or_revoke_asks_again(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    previous: ChatStatus,
) -> None:
    await seed_chat(session, status=previous)

    await dispatcher.feed_update(bot, member_update(old="left", new="member", by=9))

    assert await chat_status(sessionmaker) is ChatStatus.PENDING
    async with sessionmaker() as s:
        assert (await s.get(Chat, CHAT_ID)).added_by_user_id == 9
    assert len(telegram.calls(SendMessage)) == 1


async def test_owner_unreachable_still_registers_chat(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, sessionmaker: async_sessionmaker
) -> None:
    telegram.errors[SendMessage] = TelegramForbiddenError(SendMessage(chat_id=OWNER, text=""), "bot can't initiate")

    await dispatcher.feed_update(bot, member_update(old="left", new="member"))

    assert await chat_status(sessionmaker) is ChatStatus.PENDING


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (ChatStatus.APPROVED, ChatStatus.REVOKED),
        (ChatStatus.PENDING, ChatStatus.REVOKED),
        (ChatStatus.REJECTED, ChatStatus.REJECTED),
    ],
)
async def test_removal(
    dispatcher: Dispatcher,
    bot: Bot,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    before: ChatStatus,
    after: ChatStatus,
) -> None:
    await seed_chat(session, status=before)

    await dispatcher.feed_update(bot, member_update(old="member", new="left"))

    assert await chat_status(sessionmaker) is after


async def test_promotion_is_not_a_join(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, sessionmaker: async_sessionmaker
) -> None:
    await dispatcher.feed_update(bot, member_update(old="member", new=administrator()))
    assert await chat_status(sessionmaker) is None
    assert telegram.calls(SendMessage) == []


async def test_approve(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, status=ChatStatus.PENDING)

    await press(dispatcher, bot, "approve")

    assert await chat_status(sessionmaker) is ChatStatus.APPROVED
    assert telegram.sent_texts(CHAT_ID) == [APPROVED_NOTICE]
    assert telegram.calls(LeaveChat) == []
    [answer] = telegram.calls(AnswerCallbackQuery)
    assert answer.text == "Chat approved."
    [edit] = telegram.calls(EditMessageText)
    assert edit.text.endswith(": approved.")


@pytest.mark.parametrize(("action", "status"), [("reject", ChatStatus.REJECTED), ("revoke", ChatStatus.REVOKED)])
async def test_reject_and_revoke_leave_the_chat(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    action: str,
    status: ChatStatus,
) -> None:
    await seed_chat(session, status=ChatStatus.PENDING)

    await press(dispatcher, bot, action)

    assert await chat_status(sessionmaker) is status
    [leave] = telegram.calls(LeaveChat)
    assert leave.chat_id == CHAT_ID
    assert telegram.sent_texts(CHAT_ID) == []


async def test_leave_failure_still_records_rejection(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, status=ChatStatus.PENDING)
    telegram.errors[LeaveChat] = TelegramForbiddenError(LeaveChat(chat_id=CHAT_ID), "bot is not a member")

    await press(dispatcher, bot, "reject")

    assert await chat_status(sessionmaker) is ChatStatus.REJECTED


async def test_only_owner_can_press_buttons(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, status=ChatStatus.PENDING)

    await press(dispatcher, bot, "approve", user_id=5)

    assert await chat_status(sessionmaker) is ChatStatus.PENDING
    [answer] = telegram.calls(AnswerCallbackQuery)
    assert answer.show_alert is True


async def test_chats_lists_pending_and_approved_with_actions(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session, chat_id=-1, status=ChatStatus.APPROVED)
    await seed_chat(session, chat_id=-2, status=ChatStatus.PENDING)
    await seed_chat(session, chat_id=-3, status=ChatStatus.REJECTED)

    await dispatcher.feed_update(bot, private_update(OWNER, "/chats"))

    [reply] = telegram.calls(SendMessage)
    assert "-1" in reply.text and "-2" in reply.text and "-3" not in reply.text
    actions = [ChatAction.unpack(row[0].callback_data) for row in reply.reply_markup.inline_keyboard]
    assert [(a.action, a.chat_id) for a in actions] == [("revoke", -1), ("approve", -2), ("reject", -2)]


async def test_chats_is_owner_only(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, private_update(5, "/chats"))

    assert telegram.calls(SendMessage) == []


@pytest.mark.parametrize(("user_id", "fragment"), [(OWNER, "/chats"), (5, "This bot is private.")])
async def test_start(dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, user_id: int, fragment: str) -> None:
    await dispatcher.feed_update(bot, private_update(user_id, "/start"))
    [reply] = telegram.sent_texts(user_id)
    assert fragment in reply


@pytest.mark.parametrize("direction", ["from_old_chat", "from_new_chat"])
async def test_group_migration_moves_settings(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker, direction: str
) -> None:
    old_id, new_id = -5, -1005
    await seed_chat(session, chat_id=old_id)
    if direction == "from_old_chat":
        event = update(chat_id=old_id, chat_type="group", thread_id=None, migrate_to_chat_id=new_id)
    else:
        event = update(chat_id=new_id, thread_id=None, migrate_from_chat_id=old_id)

    await dispatcher.feed_update(bot, event)

    assert await chat_status(sessionmaker, old_id) is None
    assert await chat_status(sessionmaker, new_id) is ChatStatus.APPROVED
    async with sessionmaker() as s:
        assert (await s.get(Topic, (new_id, 7))).captioning_enabled is True
    async with sessionmaker() as s:
        assert await repo.migrate_chat(s, old_id, new_id) is False
