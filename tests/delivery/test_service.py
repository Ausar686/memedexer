import asyncio
import datetime as dt
import decimal
import typing as t

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import EditMessageMedia, SendAnimation, SendDocument, SendMessage, SendPhoto, SendVideo
from aiogram.types import BufferedInputFile, InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memedexer.config import Provider, Settings
from memedexer.delivery.service import MAX_SEND_ATTEMPTS, Delivery, message_link
from memedexer.delivery.throttle import Throttle
from memedexer.pipeline.worker import MAX_DOWNLOAD_BYTES, DownloadError, Worker
from memedexer.storage import db, repo
from memedexer.storage.models import (
    NO_TOPIC,
    Caption,
    ChatStatus,
    Job,
    JobStatus,
    JobTrigger,
    Media,
    MediaType,
    utcnow,
)
from tests.factories import CHAT_ID, THREAD_ID, seed_chat, seed_job
from tests.fakes import MEME, FakeDownloader, FakeProvider, caption_result
from tests.telegram.fake_api import FakeTelegram

OWNER = 1
class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return db.create_sessionmaker(engine)


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def bot(telegram: FakeTelegram) -> Bot:
    return Bot("42:TEST", session=telegram, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def downloader() -> FakeDownloader:
    return FakeDownloader()


@pytest.fixture
def delivery(
    bot: Bot, sessionmaker: async_sessionmaker, settings: Settings, downloader: FakeDownloader, sleeps: Sleeps
) -> Delivery:
    return Delivery(
        bot=bot,
        sessionmaker=sessionmaker,
        settings=settings,
        downloader=downloader,
        throttle=Throttle(sleep=sleeps),
        sleep=sleeps,
    )


async def captioned_job(
    session: AsyncSession,
    *,
    message_id: int = 100,
    thread_id: int = THREAD_ID,
    file_unique_id: str = "uniq",
    reply_file_id: str | None = "photo-file",
    has_spoiler: bool = False,
    thumbnail_only: bool = False,
    media_type: MediaType = MediaType.PHOTO,
    mime_type: str = "image/png",
    file_size: int | None = None,
) -> Job:
    job = await seed_job(
        session,
        message_id=message_id,
        thread_id=thread_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        mime_type=mime_type,
        file_size=file_size,
    )
    job.has_spoiler = has_spoiler
    job.thumbnail_only = thumbnail_only
    media = await session.get(Media, file_unique_id)
    media.reply_file_id = reply_file_id
    caption = Caption(
        file_unique_id=file_unique_id,
        provider="anthropic",
        model="claude-haiku-5-5",
        text="WHEN ALL TESTS PASS",
        description="A smiley face. It smiles.",
        tags=["#tests"],
        languages=["en"],
        kind="meme",
    )
    session.add(caption)
    await session.flush()
    job.caption_id = caption.id
    repo.finish_job(job, JobStatus.DONE)
    await session.commit()
    return job


async def load_job(sessionmaker: async_sessionmaker, job_id: int) -> Job:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def test_posts_silent_photo_reply_in_topic(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendPhoto)
    assert (sent.chat_id, sent.photo, sent.message_thread_id) == (CHAT_ID, "photo-file", THREAD_ID)
    assert sent.caption == (
        "<blockquote expandable>WHEN ALL TESTS PASS</blockquote>\nA smiley face. It smiles.\n#tests"
    )
    assert (sent.reply_parameters.message_id, sent.reply_parameters.allow_sending_without_reply) == (100, False)
    assert sent.disable_notification is True
    assert sent.parse_mode == ParseMode.HTML
    assert (await load_job(sessionmaker, job.id)).reply_message_id == 1000


async def test_chat_without_topics_has_no_thread(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session, thread_id=NO_TOPIC)
    job = await captioned_job(session, thread_id=NO_TOPIC)

    await delivery.deliver(job.id)

    assert telegram.calls(SendPhoto)[0].message_thread_id is None


SPOILER_CAPTION = (
    "<blockquote expandable><tg-spoiler>WHEN ALL TESTS PASS</tg-spoiler></blockquote>\n"
    "<tg-spoiler>A smiley face. It smiles.</tg-spoiler>\n"
    "<tg-spoiler>#tests</tg-spoiler>"
)


async def test_spoiler_reply_hides_image_and_caption(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, has_spoiler=True)

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendPhoto)
    assert sent.has_spoiler is True
    assert sent.caption == SPOILER_CAPTION


async def test_hide_replies_blurs_posted_replies_and_flags_pending_ones(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    posted = await captioned_job(session)
    pending = await captioned_job(session)
    other = await captioned_job(session, message_id=101)
    await delivery.deliver(posted.id)
    await delivery.deliver(other.id)

    await delivery.hide_replies(CHAT_ID, 100)
    await delivery.hide_replies(CHAT_ID, 100)

    [edit] = telegram.calls(EditMessageMedia)
    assert (edit.chat_id, edit.message_id) == (CHAT_ID, (await load_job(sessionmaker, posted.id)).reply_message_id)
    assert (edit.media.media, edit.media.has_spoiler, edit.media.caption) == ("photo-file", True, SPOILER_CAPTION)
    assert edit.media.parse_mode == ParseMode.HTML
    assert [(await load_job(sessionmaker, j.id)).has_spoiler for j in (posted, pending, other)] == [True, True, False]

    await delivery.deliver(pending.id)
    assert telegram.calls(SendPhoto)[-1].has_spoiler is True


async def test_reply_sent_while_the_source_became_a_spoiler_is_hidden(
    bot: Bot,
    telegram: FakeTelegram,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    downloader: FakeDownloader,
    sleeps: Sleeps,
    session: AsyncSession,
) -> None:
    class SpoilerDuringSend:
        """hide_replies ran after the job was read but before its reply id was stored, so it couldn't blur it."""

        async def wait(self, chat_id: int) -> None:
            async with sessionmaker() as s:
                (await s.get(Job, job.id)).has_spoiler = True
                await s.commit()

    delivery = Delivery(
        bot=bot,
        sessionmaker=sessionmaker,
        settings=settings,
        downloader=downloader,
        throttle=SpoilerDuringSend(),
        sleep=sleeps,
    )
    await seed_chat(session)
    job = await captioned_job(session)

    await delivery.deliver(job.id)

    assert telegram.calls(SendPhoto)[0].has_spoiler is False
    [edit] = telegram.calls(EditMessageMedia)
    assert (edit.message_id, edit.media.has_spoiler) == (1000, True)


async def test_failing_to_hide_a_reply_alerts_the_owner(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)
    await delivery.deliver(job.id)
    request = EditMessageMedia(media=InputMediaPhoto(media="x"))
    telegram.errors[EditMessageMedia] = TelegramNetworkError(request, "connection reset")

    await delivery.hide_replies(CHAT_ID, 100)

    assert len(telegram.calls(EditMessageMedia)) == MAX_SEND_ATTEMPTS
    [alert] = telegram.calls(SendMessage)
    assert alert.chat_id == OWNER
    assert "Couldn't hide a spoiler reply" in alert.text


async def test_deleted_reply_is_skipped_without_an_alert(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)
    await delivery.deliver(job.id)
    request = EditMessageMedia(media=InputMediaPhoto(media="x"))
    telegram.errors[EditMessageMedia] = TelegramBadRequest(request, "message to edit not found")

    await delivery.hide_replies(CHAT_ID, 100)

    assert len(telegram.calls(EditMessageMedia)) == 1
    assert telegram.calls(SendMessage) == []


async def test_document_is_uploaded_once_then_reused(
    delivery: Delivery,
    telegram: FakeTelegram,
    downloader: FakeDownloader,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
) -> None:
    await seed_chat(session)
    first = await captioned_job(session, message_id=1, reply_file_id=None)
    second = await captioned_job(session, message_id=2, reply_file_id=None)

    await delivery.deliver(first.id)
    await delivery.deliver(second.id)

    upload, reuse = telegram.calls(SendPhoto)
    assert isinstance(upload.photo, BufferedInputFile)
    assert upload.photo.data == MEME
    assert reuse.photo == "photo-1000"
    assert downloader.file_ids == ["file-uniq"]
    async with sessionmaker() as s:
        assert (await s.get(Media, "uniq")).reply_file_id == "photo-1000"


async def test_video_is_re_sent_by_file_id(
    delivery: Delivery, telegram: FakeTelegram, downloader: FakeDownloader, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, media_type=MediaType.VIDEO, reply_file_id="file-uniq", has_spoiler=True)

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendVideo)
    assert (sent.video, sent.has_spoiler, sent.message_thread_id) == ("file-uniq", True, THREAD_ID)
    assert (sent.reply_parameters.message_id, sent.disable_notification) == (100, True)
    assert sent.caption == SPOILER_CAPTION
    assert downloader.file_ids == []


async def test_animation_is_re_sent_as_an_animation(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, media_type=MediaType.ANIMATION, reply_file_id="file-uniq")

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendAnimation)
    assert (sent.animation, sent.has_spoiler) == ("file-uniq", False)
    assert telegram.calls(SendPhoto) == telegram.calls(SendVideo) == []


async def test_video_document_is_uploaded_once_as_a_video_then_reused(
    delivery: Delivery,
    telegram: FakeTelegram,
    downloader: FakeDownloader,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
) -> None:
    await seed_chat(session)
    common = {"media_type": MediaType.VIDEO_DOCUMENT, "mime_type": "video/quicktime", "reply_file_id": None}
    first = await captioned_job(session, message_id=1, **common)
    second = await captioned_job(session, message_id=2, **common)

    await delivery.deliver(first.id)
    await delivery.deliver(second.id)

    upload, reuse = telegram.calls(SendVideo)
    assert isinstance(upload.video, BufferedInputFile)
    assert (upload.video.filename, upload.video.data) == ("uniq.mov", MEME)
    assert reuse.video == "video-1000"
    assert downloader.file_ids == ["file-uniq"]
    async with sessionmaker() as s:
        assert (await s.get(Media, "uniq")).reply_file_id == "video-1000"


async def replied_and_edited(
    session: AsyncSession, *, edited_reply_file_id: str | None = "photo-b", **media: t.Any
) -> Job:
    """A message whose first image got reply 555, then was edited to other media: the edit's job, undelivered."""
    first = await captioned_job(session, file_unique_id="a", reply_file_id="photo-a")
    first.reply_message_id = 555
    edit = await captioned_job(session, file_unique_id="b", reply_file_id=edited_reply_file_id, **media)
    edit.trigger = JobTrigger.EDIT
    await session.commit()
    return edit


async def test_an_edit_replaces_the_reply_in_place(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session)

    await delivery.deliver(job.id)

    [edit] = telegram.calls(EditMessageMedia)
    assert (edit.chat_id, edit.message_id, edit.media.media) == (CHAT_ID, 555, "photo-b")
    assert edit.media.caption == (
        "<blockquote expandable>WHEN ALL TESTS PASS</blockquote>\nA smiley face. It smiles.\n#tests"
    )
    assert telegram.calls(SendPhoto) == []
    assert (await load_job(sessionmaker, job.id)).reply_message_id == 555


async def test_editing_back_to_the_same_reply_is_not_an_error(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session)
    request = EditMessageMedia(media=InputMediaPhoto(media="x"))
    telegram.errors[EditMessageMedia] = TelegramBadRequest(request, "Bad Request: message is not modified")

    await delivery.deliver(job.id)

    stored = await load_job(sessionmaker, job.id)
    assert (stored.reply_message_id, stored.delivery_error) == (555, None)
    assert telegram.calls(SendPhoto) == []


async def test_a_deleted_reply_is_replaced_by_a_new_one(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session)
    request = EditMessageMedia(media=InputMediaPhoto(media="x"))
    telegram.errors[EditMessageMedia] = TelegramBadRequest(request, "Bad Request: message to edit not found")

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendPhoto)
    assert sent.photo == "photo-b"
    assert (await load_job(sessionmaker, job.id)).reply_message_id == 1000


async def test_an_edited_image_document_is_uploaded_into_the_reply(
    delivery: Delivery,
    telegram: FakeTelegram,
    downloader: FakeDownloader,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session, edited_reply_file_id=None)

    await delivery.deliver(job.id)

    [edit] = telegram.calls(EditMessageMedia)
    assert isinstance(edit.media.media, BufferedInputFile)
    assert downloader.file_ids == ["file-b"]
    async with sessionmaker() as s:
        assert (await s.get(Media, "b")).reply_file_id == "edited-photo-555"


@pytest.mark.parametrize(
    ("media", "input_media", "file"),
    [
        ({"media_type": MediaType.ANIMATION, "edited_reply_file_id": "gif-b"}, InputMediaAnimation, "gif-b"),
        ({"media_type": MediaType.VIDEO, "edited_reply_file_id": "video-b"}, InputMediaVideo, "video-b"),
        (
            {"media_type": MediaType.VIDEO_DOCUMENT, "edited_reply_file_id": None, "file_size": MAX_DOWNLOAD_BYTES + 1},
            InputMediaDocument,
            "file-b",
        ),
    ],
    ids=["gif", "video", "video-document-too-big"],
)
async def test_an_edit_to_other_media_types_replaces_the_reply_with_their_kind(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, media: dict, input_media: type, file: str
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session, **media)

    await delivery.deliver(job.id)

    [edit] = telegram.calls(EditMessageMedia)
    assert (type(edit.media), edit.media.media, edit.message_id) == (input_media, file, 555)


@pytest.mark.parametrize("trigger", [JobTrigger.MESSAGE, JobTrigger.RECAPTION])
async def test_only_edits_replace_replies(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, trigger: JobTrigger
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session)
    job.trigger = trigger
    await session.commit()

    await delivery.deliver(job.id)

    assert telegram.calls(EditMessageMedia) == []
    assert len(telegram.calls(SendPhoto)) == 1


async def test_hiding_a_reply_shared_by_edits_shows_the_latest_media_once(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await replied_and_edited(session)
    job.reply_message_id = 555
    await session.commit()

    await delivery.hide_replies(CHAT_ID, 100)

    [hide] = telegram.calls(EditMessageMedia)
    assert (hide.message_id, hide.media.media, hide.media.has_spoiler) == (555, "photo-b", True)


async def test_video_document_too_big_to_upload_goes_back_as_a_document(
    delivery: Delivery, telegram: FakeTelegram, downloader: FakeDownloader, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(
        session, media_type=MediaType.VIDEO_DOCUMENT, reply_file_id=None, file_size=MAX_DOWNLOAD_BYTES + 1
    )

    await delivery.deliver(job.id)

    [sent] = telegram.calls(SendDocument)
    assert (sent.document, sent.reply_parameters.message_id) == ("file-uniq", 100)
    assert downloader.file_ids == []


async def test_thumbnail_only_caption_is_posted_and_the_owner_told(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, media_type=MediaType.VIDEO, reply_file_id="file-uniq", thumbnail_only=True)

    await delivery.job_finished(job)
    await delivery.join()

    assert len(telegram.calls(SendVideo)) == 1
    [notice] = telegram.calls(SendMessage)
    assert notice.chat_id == OWNER
    headline, link = notice.text.splitlines()
    assert "over the Bot API's 20 MB download limit" in headline
    assert link == f"https://t.me/c/{str(CHAT_ID)[4:]}/100"


async def test_hiding_a_video_reply_edits_it_as_a_video(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, media_type=MediaType.VIDEO, reply_file_id="file-uniq")
    await delivery.deliver(job.id)

    await delivery.hide_replies(CHAT_ID, 100)

    [edit] = telegram.calls(EditMessageMedia)
    assert isinstance(edit.media, InputMediaVideo)
    assert (edit.media.media, edit.media.has_spoiler) == ("file-uniq", True)


async def test_hiding_skips_a_reply_sent_back_as_a_document(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    # The message first held a video document too big to re-upload, then was edited to a photo.
    document = await captioned_job(
        session,
        file_unique_id="vdoc",
        media_type=MediaType.VIDEO_DOCUMENT,
        reply_file_id=None,
        file_size=MAX_DOWNLOAD_BYTES + 1,
    )
    photo = await captioned_job(session)
    await delivery.deliver(document.id)
    await delivery.deliver(photo.id)

    await delivery.hide_replies(CHAT_ID, 100)

    [edit] = telegram.calls(EditMessageMedia)
    assert (edit.message_id, edit.media.media) == (1001, "photo-file")
    assert telegram.calls(SendMessage) == []


async def test_retry_after_is_honored(
    delivery: Delivery, telegram: FakeTelegram, sleeps: Sleeps, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)
    telegram.errors[SendPhoto] = [TelegramRetryAfter(SendPhoto(chat_id=CHAT_ID, photo="x"), "flood", retry_after=7)]

    await delivery.deliver(job.id)

    assert sleeps.calls == [7]
    assert len(telegram.calls(SendPhoto)) == 2
    assert (await load_job(sessionmaker, job.id)).reply_message_id is not None


async def test_deleted_original_is_given_up_silently(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)
    request = SendPhoto(chat_id=CHAT_ID, photo="x")
    telegram.errors[SendPhoto] = TelegramBadRequest(request, "message to be replied not found")

    await delivery.deliver(job.id)
    await delivery.deliver(job.id)

    stored = await load_job(sessionmaker, job.id)
    assert "message to be replied not found" in stored.delivery_error
    assert stored.reply_message_id is None
    assert len(telegram.calls(SendPhoto)) == 1
    assert telegram.calls(SendMessage) == []


async def test_persistent_network_errors_give_up_and_alert_owner(
    delivery: Delivery, telegram: FakeTelegram, sleeps: Sleeps, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)
    telegram.errors[SendPhoto] = TelegramNetworkError(SendPhoto(chat_id=CHAT_ID, photo="x"), "connection reset")

    await delivery.deliver(job.id)

    assert len(telegram.calls(SendPhoto)) == MAX_SEND_ATTEMPTS
    assert sleeps.calls == [5.0, 10.0, 15.0, 20.0]
    assert (await load_job(sessionmaker, job.id)).delivery_error.startswith("gave up after 4 attempts")
    [alert] = telegram.calls(SendMessage)
    assert alert.chat_id == OWNER
    assert "Couldn't post a caption" in alert.text
    assert f"https://t.me/c/{str(CHAT_ID)[4:]}/100" in alert.text


async def test_broken_document_alerts_owner(
    delivery: Delivery, telegram: FakeTelegram, downloader: FakeDownloader, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session, reply_file_id=None)
    downloader.result = DownloadError("file is too big", retryable=False)

    await delivery.deliver(job.id)

    assert telegram.calls(SendPhoto) == []
    assert "file is too big" in telegram.calls(SendMessage)[0].text


async def test_unapproved_chat_is_skipped(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    await seed_chat(session, status=ChatStatus.REVOKED)
    job = await captioned_job(session)

    await delivery.deliver(job.id)

    assert telegram.calls(SendPhoto) == []
    assert (await load_job(sessionmaker, job.id)).delivery_error == "chat is not approved"


async def test_finished_jobs_are_queued_and_delivered(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    jobs = [await captioned_job(session, message_id=i) for i in range(3)]

    for job in jobs:
        await delivery.job_finished(job)
    await delivery.join()
    await delivery.close()

    assert [sent.reply_parameters.message_id for sent in telegram.calls(SendPhoto)] == [0, 1, 2]


async def test_start_recovers_recent_undelivered_replies(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    recent = await captioned_job(session, message_id=1)
    stale = await captioned_job(session, message_id=2)
    stale.finished_at = utcnow() - dt.timedelta(days=2)
    await session.commit()

    await delivery.start()
    await delivery.join()
    await delivery.close()

    assert [sent.reply_parameters.message_id for sent in telegram.calls(SendPhoto)] == [recent.message_id]


@pytest.mark.parametrize(("status", "verb"), [(JobStatus.FAILED, "failed"), (JobStatus.REFUSED, "refused")])
async def test_failures_alert_owner_once_per_cooldown(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, status: JobStatus, verb: str
) -> None:
    await seed_chat(session)
    jobs = [await seed_job(session, message_id=i) for i in range(2)]
    for job in jobs:
        repo.finish_job(job, status, error="provider said <no>")
    await session.commit()

    for job in jobs:
        await delivery.job_finished(job)

    [alert] = telegram.calls(SendMessage)
    assert alert.chat_id == OWNER
    assert f"Captioning {verb} in" in alert.text
    assert "provider said &lt;no&gt;" in alert.text


async def test_budget_alert_only_for_first_over_budget_job(
    delivery: Delivery, telegram: FakeTelegram, settings: Settings, session: AsyncSession
) -> None:
    settings.cost_limit_daily_usd = decimal.Decimal("0.0001")
    await seed_chat(session)
    spent = await seed_job(session, message_id=1)
    spent.cost_microusd = 100
    repo.finish_job(spent, JobStatus.DONE)
    over = [await seed_job(session, message_id=i) for i in (2, 3)]
    for job in over:
        repo.finish_job(job, JobStatus.BUDGET_EXCEEDED, error="daily limit reached")
    await session.commit()

    for job in reversed(over):
        await delivery.job_finished(job)

    [alert] = telegram.calls(SendMessage)
    assert "reached its daily budget of $0.00" in alert.text


async def test_crashing_delivery_does_not_stop_the_queue(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_chat(session)
    broken = await captioned_job(session, message_id=2)
    fine = await captioned_job(session, message_id=3)
    deliver = delivery.deliver

    async def flaky_deliver(job_id: int) -> None:
        if job_id == broken.id:
            raise RuntimeError("boom")
        await deliver(job_id)

    monkeypatch.setattr(delivery, "deliver", flaky_deliver)

    await delivery.job_finished(broken)
    await delivery.job_finished(fine)
    await delivery.join()
    await delivery.close()

    assert [sent.reply_parameters.message_id for sent in telegram.calls(SendPhoto)] == [3]


@pytest.mark.parametrize(
    ("chat_id", "expected"), [(-1001234567890, "https://t.me/c/1234567890/5"), (-12345, None), (77, None)]
)
def test_message_link(chat_id: int, expected: str | None) -> None:
    assert message_link(chat_id, 5) == expected


async def test_worker_and_delivery_end_to_end(
    delivery: Delivery,
    telegram: FakeTelegram,
    settings: Settings,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    downloader: FakeDownloader,
) -> None:
    await seed_chat(session)
    job = await seed_job(session, message_id=42)
    worker = Worker(
        sessionmaker=sessionmaker,
        settings=settings,
        providers={Provider.ANTHROPIC: FakeProvider(caption_result())},
        downloader=downloader,
        listener=delivery,
    )

    await worker.run_once()
    await delivery.join()
    await delivery.close()

    [sent] = telegram.calls(SendPhoto)
    assert sent.reply_parameters.message_id == 42
    assert "WHEN ALL TESTS PASS" in sent.caption
    stored = await load_job(sessionmaker, job.id)
    assert (stored.status, stored.reply_message_id) == (JobStatus.DONE, 1000)


async def test_close_delivers_queued_replies_first(
    delivery: Delivery, telegram: FakeTelegram, session: AsyncSession
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)

    await delivery.job_finished(job)
    await delivery.close()

    assert [sent.reply_parameters.message_id for sent in telegram.calls(SendPhoto)] == [job.message_id]


async def test_close_abandons_replies_after_timeout(
    delivery: Delivery,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_chat(session)
    job = await captioned_job(session)

    async def stuck_deliver(job_id: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(delivery, "deliver", stuck_deliver)
    await delivery.job_finished(job)

    await asyncio.wait_for(delivery.close(timeout=0.05), 5)

    assert "abandoning unfinished replies" in caplog.text
    stored = await load_job(sessionmaker, job.id)
    assert (stored.reply_message_id, stored.delivery_error) == (None, None)
