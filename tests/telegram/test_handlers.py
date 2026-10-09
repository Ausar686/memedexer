import pytest
import sqlalchemy as sa
from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.storage.models import NO_TOPIC, ChatStatus, Job, JobTrigger, Media
from tests.factories import THREAD_ID, seed_chat
from tests.telegram.conftest import SpyWorker
from tests.telegram.updates import PHOTO_SIZES, document, update


async def jobs(sessionmaker: async_sessionmaker[AsyncSession]) -> list[Job]:
    async with sessionmaker() as session:
        return list(await session.scalars(sa.select(Job).order_by(Job.id)))


async def test_photo_in_enabled_topic_is_enqueued(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker, worker: SpyWorker
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, update(photo=PHOTO_SIZES))

    [job] = await jobs(sessionmaker)
    assert (job.thread_id, job.message_id, job.file_unique_id) == (THREAD_ID, 100, "uy")
    assert job.trigger is JobTrigger.MESSAGE
    async with sessionmaker() as s:
        media = await s.get(Media, "uy")
    assert (media.file_id, media.mime_type, media.width, media.height) == ("y", "image/jpeg", 1280, 960)
    assert media.photo_file_id == "y"
    assert worker.notified == 1


async def test_document_in_chat_without_topics(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, thread_id=NO_TOPIC)

    await dispatcher.feed_update(bot, update(chat_type="group", thread_id=None, document=document()))

    [job] = await jobs(sessionmaker)
    assert (job.thread_id, job.file_unique_id) == (NO_TOPIC, "doc")


@pytest.mark.parametrize(
    ("seed", "message"),
    [
        ({"status": ChatStatus.PENDING}, {}),
        ({"status": ChatStatus.REVOKED}, {}),
        ({"enabled": False}, {}),
        ({}, {"thread_id": 8}),
        ({}, {"chat_id": -5}),
        ({}, {"document": document("application/pdf"), "photo": None}),
    ],
    ids=["pending-chat", "revoked-chat", "disabled-topic", "other-topic", "unknown-chat", "not-an-image"],
)
async def test_ignored(
    dispatcher: Dispatcher,
    bot: Bot,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    worker: SpyWorker,
    seed: dict,
    message: dict,
) -> None:
    await seed_chat(session, **seed)

    await dispatcher.feed_update(bot, update(**({"photo": PHOTO_SIZES} | message)))

    assert await jobs(sessionmaker) == []
    assert worker.notified == 0


async def test_private_chat_is_ignored(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, chat_id=5, thread_id=NO_TOPIC)

    await dispatcher.feed_update(bot, update(chat_id=5, chat_type="private", thread_id=None, photo=PHOTO_SIZES))

    assert await jobs(sessionmaker) == []


async def test_edit_with_new_image_is_enqueued_as_edit(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    await dispatcher.feed_update(bot, update(1, photo=PHOTO_SIZES))

    await dispatcher.feed_update(bot, update(2, edited=True, edit_date=1, document=document(unique="new")))

    first, second = await jobs(sessionmaker)
    assert (second.message_id, second.file_unique_id, second.trigger) == (100, "new", JobTrigger.EDIT)
    assert first.file_unique_id == "uy"


async def test_edit_keeping_the_image_is_ignored(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    await dispatcher.feed_update(bot, update(1, photo=PHOTO_SIZES))

    await dispatcher.feed_update(bot, update(2, edited=True, edit_date=1, photo=PHOTO_SIZES))

    assert len(await jobs(sessionmaker)) == 1


async def test_edit_of_uncaptioned_message_is_enqueued(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, update(edited=True, edit_date=1, photo=PHOTO_SIZES))

    [job] = await jobs(sessionmaker)
    assert job.trigger is JobTrigger.EDIT
