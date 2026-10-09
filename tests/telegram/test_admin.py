import datetime as dt

import pytest
import sqlalchemy as sa
from aiogram import Bot, Dispatcher
from aiogram.methods import GetChatMember
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.config import Settings
from memedexer.storage import repo
from memedexer.storage.models import DESCRIPTION_LANGUAGE_MAX_LENGTH, NO_TOPIC, Chat, ChatStatus, Job, JobTrigger, Topic
from tests.factories import CHAT_ID, THREAD_ID, seed_chat, seed_job
from tests.telegram.conftest import SpyWorker
from tests.telegram.fake_api import FakeTelegram
from tests.telegram.updates import PHOTO_SIZES, message_data, update

ADMIN = 10
MEMBER = 11


@pytest.fixture(autouse=True)
def admins(telegram: FakeTelegram) -> None:
    telegram.admins = {ADMIN}


async def command(dispatcher: Dispatcher, bot: Bot, text: str, *, user_id: int = ADMIN, **kwargs: object) -> None:
    await dispatcher.feed_update(bot, update(text=text, user_id=user_id, **kwargs))


async def load(sessionmaker: async_sessionmaker[AsyncSession], model: type, key: object) -> object:
    async with sessionmaker() as session:
        return await session.get(model, key)


async def test_captions_on_and_off_for_current_topic(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, enabled=False)

    await command(dispatcher, bot, "/captions on")
    assert (await load(sessionmaker, Topic, (CHAT_ID, THREAD_ID))).captioning_enabled is True
    await command(dispatcher, bot, "/captions@memedexer_bot off", thread_id=8)
    assert (await load(sessionmaker, Topic, (CHAT_ID, 8))).captioning_enabled is False

    assert telegram.sent_texts(CHAT_ID) == ["Captioning is now on here.", "Captioning is now off here."]


async def test_non_admin_cannot_change_settings(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, enabled=False)

    for text in ["/captions on", "/model claude-sonnet-5-5", "/language Russian"]:
        await command(dispatcher, bot, text, user_id=MEMBER)

    assert (await load(sessionmaker, Topic, (CHAT_ID, THREAD_ID))).captioning_enabled is False
    chat = await load(sessionmaker, Chat, CHAT_ID)
    assert (chat.model, chat.description_language) == (None, None)
    assert telegram.sent_texts(CHAT_ID) == ["Only chat admins can change this."] * 3
    assert {call.user_id for call in telegram.calls(GetChatMember)} == {MEMBER}


async def test_owner_and_anonymous_admin_skip_member_lookup(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, enabled=False)

    await command(dispatcher, bot, "/captions on", user_id=1)
    await command(dispatcher, bot, "/captions off", user_id=MEMBER, sender_chat={"id": CHAT_ID, "type": "supergroup"})

    assert telegram.calls(GetChatMember) == []
    assert telegram.sent_texts(CHAT_ID) == ["Captioning is now on here.", "Captioning is now off here."]


async def test_model_for_topic_and_chat(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    await seed_chat(session)

    await command(dispatcher, bot, "/model claude-sonnet-5-5")
    await command(dispatcher, bot, "/model chat claude-opus-5-5")
    topic = await load(sessionmaker, Topic, (CHAT_ID, THREAD_ID))
    chat = await load(sessionmaker, Chat, CHAT_ID)
    assert (topic.provider, topic.model) == ("anthropic", "claude-sonnet-5-5")
    assert (chat.provider, chat.model) == ("anthropic", "claude-opus-5-5")

    await command(dispatcher, bot, "/model reset")
    topic = await load(sessionmaker, Topic, (CHAT_ID, THREAD_ID))
    assert (topic.provider, topic.model) == (None, None)
    assert telegram.sent_texts(CHAT_ID)[-1] == "Model override for this topic removed."


@pytest.mark.parametrize(
    "text",
    ["/model gpt-6-luna", "/model nonexistent", "/model", "/model chat", "/model a b"],
    ids=["provider-without-key", "unknown", "no-args", "chat-no-model", "too-many-args"],
)
async def test_model_rejects_bad_input(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    text: str,
) -> None:
    await seed_chat(session)

    await command(dispatcher, bot, text)

    topic = await load(sessionmaker, Topic, (CHAT_ID, THREAD_ID))
    assert topic.model is None
    [reply] = telegram.sent_texts(CHAT_ID)
    available = reply.split("Available")[-1]
    assert "claude-haiku-5-5" in available and "gpt-6-luna" not in available


async def test_language(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)

    await command(dispatcher, bot, "/language Brazilian Portuguese")
    assert (await load(sessionmaker, Chat, CHAT_ID)).description_language == "Brazilian Portuguese"
    await command(dispatcher, bot, "/language <script>")
    assert (await load(sessionmaker, Chat, CHAT_ID)).description_language == "Brazilian Portuguese"
    await command(dispatcher, bot, "/language reset")
    assert (await load(sessionmaker, Chat, CHAT_ID)).description_language is None

    replies = telegram.sent_texts(CHAT_ID)
    assert replies[0] == "Descriptions will be written in Brazilian Portuguese."
    assert replies[1].startswith("Usage:")
    assert replies[2] == "Descriptions will use each meme's own language."


async def test_settings_shows_effective_values_and_spend(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    chat = await repo.get_chat(session, CHAT_ID)
    chat.provider, chat.model, chat.description_language = "anthropic", "claude-sonnet-5-5", "<Russian>"
    job = await seed_job(session)
    job.cost_microusd, job.finished_at = 1_234_567, dt.datetime.now(dt.UTC)
    await session.commit()

    await command(dispatcher, bot, "/settings", user_id=MEMBER)

    [reply] = telegram.sent_texts(CHAT_ID)
    assert "<b>Captioning here:</b> on" in reply
    assert "<code>claude-sonnet-5-5</code> (set for this chat)" in reply
    assert "&lt;Russian&gt;" in reply
    assert "daily $1.23 / $20.00" in reply


async def test_settings_in_topic_without_row(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)

    await command(dispatcher, bot, "/settings", thread_id=99)

    [reply] = telegram.sent_texts(CHAT_ID)
    assert "<b>Captioning here:</b> off" in reply
    assert "(default)" in reply
    assert "the meme's language" in reply


@pytest.mark.parametrize("status", [ChatStatus.PENDING, ChatStatus.REVOKED])
async def test_commands_in_unapproved_chat(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, status: ChatStatus
) -> None:
    await seed_chat(session, status=status)

    await command(dispatcher, bot, "/settings")
    await command(dispatcher, bot, "/captions on")

    assert telegram.sent_texts(CHAT_ID) == ["This chat is waiting for the bot owner's approval."] * 2


async def test_recaption_enqueues_job_for_replied_image(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    worker: SpyWorker,
) -> None:
    await seed_chat(session)
    original = message_data(message_id=55, photo=PHOTO_SIZES)

    await command(dispatcher, bot, "/recaption", reply_to=original)

    async with sessionmaker() as s:
        [job] = list(await s.scalars(sa.select(Job)))
    assert (job.message_id, job.thread_id, job.file_unique_id) == (55, THREAD_ID, "uy")
    assert job.trigger is JobTrigger.RECAPTION
    assert worker.notified == 1
    assert telegram.sent_texts(CHAT_ID) == ["Queued a fresh caption."]


async def test_recaption_needs_an_image_reply(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, worker: SpyWorker
) -> None:
    await seed_chat(session, thread_id=NO_TOPIC)

    await command(dispatcher, bot, "/recaption", thread_id=None)
    await command(dispatcher, bot, "/recaption", thread_id=None, reply_to=message_data(message_id=3, text="hi"))

    assert worker.notified == 0
    assert telegram.sent_texts(CHAT_ID) == ["Reply to an image with <code>/recaption</code>."] * 2


async def test_language_length_matches_column(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    longest = "L" * DESCRIPTION_LANGUAGE_MAX_LENGTH

    await command(dispatcher, bot, f"/language {longest}x")
    assert (await load(sessionmaker, Chat, CHAT_ID)).description_language is None
    await command(dispatcher, bot, f"/language {longest}")
    assert (await load(sessionmaker, Chat, CHAT_ID)).description_language == longest

    assert Chat.__table__.c.description_language.type.length == DESCRIPTION_LANGUAGE_MAX_LENGTH
