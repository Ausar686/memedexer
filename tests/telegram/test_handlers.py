import pytest
import sqlalchemy as sa
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.cooldown import Cooldown
from memedexer.storage.models import NO_TOPIC, ChatStatus, Job, JobStatus, JobTrigger, Media, MediaType
from memedexer.telegram.handlers import BACKLOG_ALERT_INTERVAL_SECONDS, MAX_QUEUED_JOBS_PER_CHAT
from tests.factories import CHAT_ID, THREAD_ID, seed_chat, seed_job
from tests.telegram.conftest import SpyReplies, SpyWorker
from tests.telegram.fake_api import FakeTelegram
from tests.telegram.updates import PHOTO_SIZES, animation, document, update, video


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
    assert (media.file_id, media.media_type, media.mime_type, media.width, media.height) == (
        "y",
        MediaType.PHOTO,
        "image/jpeg",
        1280,
        960,
    )
    assert media.reply_file_id == "y"
    assert worker.notified == 1


@pytest.mark.parametrize(
    ("message", "unique", "media_type"),
    [
        ({"video": video()}, "vid", MediaType.VIDEO),
        ({"animation": animation(), "document": animation()}, "gif", MediaType.ANIMATION),
        ({"document": {**document("video/mp4", unique="vdoc"), "thumbnail": video()["thumbnail"]}}, "vdoc",
         MediaType.VIDEO_DOCUMENT),
    ],
    ids=["video", "animation", "video-document"],
)
async def test_videos_are_enqueued_with_their_details(
    dispatcher: Dispatcher,
    bot: Bot,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    message: dict,
    unique: str,
    media_type: MediaType,
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, update(**message))

    [job] = await jobs(sessionmaker)
    assert job.file_unique_id == unique
    async with sessionmaker() as s:
        media = await s.get(Media, unique)
    assert (media.media_type, media.thumbnail_file_id) == (media_type, "thumb")
    assert media.reply_file_id == (None if media_type is MediaType.VIDEO_DOCUMENT else f"file-{unique}")


async def test_video_note_and_sticker_are_ignored(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    note = {"file_id": "n", "file_unique_id": "un", "length": 240, "duration": 5}
    sticker = {"file_id": "s", "file_unique_id": "us", "type": "regular", "width": 512, "height": 512,
               "is_animated": False, "is_video": True}

    await dispatcher.feed_update(bot, update(1, video_note=note))
    await dispatcher.feed_update(bot, update(2, sticker=sticker))

    assert await jobs(sessionmaker) == []


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


@pytest.mark.parametrize("spoiler", [True, False])
async def test_spoiler_flag_is_recorded_on_the_job(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker, spoiler: bool
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, update(photo=PHOTO_SIZES, has_media_spoiler=spoiler))

    [job] = await jobs(sessionmaker)
    assert job.has_spoiler is spoiler


async def test_edit_adding_a_spoiler_hides_the_replies_instead_of_recaptioning(
    dispatcher: Dispatcher,
    bot: Bot,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    worker: SpyWorker,
    replies: SpyReplies,
) -> None:
    await seed_chat(session)
    await dispatcher.feed_update(bot, update(1, photo=PHOTO_SIZES))

    await dispatcher.feed_update(bot, update(2, edited=True, edit_date=1, photo=PHOTO_SIZES, has_media_spoiler=True))

    assert replies.hidden == [(CHAT_ID, 100)]
    assert len(await jobs(sessionmaker)) == 1
    assert worker.notified == 1


async def test_edit_removing_a_spoiler_keeps_the_replies_hidden(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker, replies: SpyReplies
) -> None:
    await seed_chat(session)
    await dispatcher.feed_update(bot, update(1, photo=PHOTO_SIZES, has_media_spoiler=True))

    await dispatcher.feed_update(bot, update(2, edited=True, edit_date=1, photo=PHOTO_SIZES))

    [job] = await jobs(sessionmaker)
    assert job.has_spoiler is True
    assert replies.hidden == []


async def test_edit_replacing_the_image_with_a_spoiler_enqueues_a_spoiler_job(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker, replies: SpyReplies
) -> None:
    await seed_chat(session)
    await dispatcher.feed_update(bot, update(1, photo=PHOTO_SIZES))
    new_photo = [{"file_id": "n", "file_unique_id": "un", "width": 800, "height": 600}]

    await dispatcher.feed_update(bot, update(2, edited=True, edit_date=1, photo=new_photo, has_media_spoiler=True))

    first, second = await jobs(sessionmaker)
    assert (first.has_spoiler, second.has_spoiler, second.file_unique_id) == (False, True, "un")
    assert replies.hidden == []


async def test_edit_of_uncaptioned_message_is_enqueued(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)

    await dispatcher.feed_update(bot, update(edited=True, edit_date=1, photo=PHOTO_SIZES))

    [job] = await jobs(sessionmaker)
    assert job.trigger is JobTrigger.EDIT


async def test_full_queue_skips_new_media_and_tells_the_owner_hourly(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegram,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    worker: SpyWorker,
) -> None:
    await seed_chat(session)
    for message_id in range(MAX_QUEUED_JOBS_PER_CHAT):
        job = await seed_job(session, message_id=message_id)
        job.status = JobStatus.RUNNING if message_id == 0 else JobStatus.PENDING
    await session.commit()

    now = [0.0]
    dispatcher["backlog_alerts"] = Cooldown(BACKLOG_ALERT_INTERVAL_SECONDS, lambda: now[0], count_suppressed=True)

    for update_id in range(1, 4):
        await dispatcher.feed_update(bot, update(update_id, message_id=500 + update_id, photo=PHOTO_SIZES))
    now[0] = BACKLOG_ALERT_INTERVAL_SECONDS
    await dispatcher.feed_update(bot, update(4, message_id=504, photo=PHOTO_SIZES))

    assert len(await jobs(sessionmaker)) == MAX_QUEUED_JOBS_PER_CHAT
    assert worker.notified == 0
    first, second = telegram.calls(SendMessage)
    assert first.chat_id == 1
    assert f"has {MAX_QUEUED_JOBS_PER_CHAT} captions queued, so new media there is skipped" in first.text
    assert "more were skipped" not in first.text
    assert second.text.endswith("2 more were skipped since the last notice.")


async def test_finished_jobs_do_not_count_toward_the_queue(
    dispatcher: Dispatcher, bot: Bot, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    for message_id in range(MAX_QUEUED_JOBS_PER_CHAT):
        job = await seed_job(session, message_id=message_id)
        job.status = JobStatus.DONE
    await session.commit()

    await dispatcher.feed_update(bot, update(message_id=500, photo=PHOTO_SIZES))

    assert len(await jobs(sessionmaker)) == MAX_QUEUED_JOBS_PER_CHAT + 1
