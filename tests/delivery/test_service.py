import datetime as dt
import decimal

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memedexer.config import Provider, Settings
from memedexer.delivery.service import MAX_SEND_ATTEMPTS, Delivery, message_link
from memedexer.delivery.throttle import Throttle
from memedexer.pipeline.worker import DownloadError, Worker
from memedexer.storage import db, repo
from memedexer.storage.models import NO_TOPIC, Caption, ChatStatus, Job, JobStatus, Media, utcnow
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
    photo_file_id: str | None = "photo-file",
) -> Job:
    job = await seed_job(session, message_id=message_id, thread_id=thread_id, file_unique_id=file_unique_id)
    media = await session.get(Media, file_unique_id)
    media.photo_file_id = photo_file_id
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


async def test_document_is_uploaded_once_then_reused(
    delivery: Delivery,
    telegram: FakeTelegram,
    downloader: FakeDownloader,
    session: AsyncSession,
    sessionmaker: async_sessionmaker,
) -> None:
    await seed_chat(session)
    first = await captioned_job(session, message_id=1, photo_file_id=None)
    second = await captioned_job(session, message_id=2, photo_file_id=None)

    await delivery.deliver(first.id)
    await delivery.deliver(second.id)

    upload, reuse = telegram.calls(SendPhoto)
    assert isinstance(upload.photo, BufferedInputFile)
    assert upload.photo.data == MEME
    assert reuse.photo == "photo-1000"
    assert downloader.file_ids == ["file-uniq"]
    async with sessionmaker() as s:
        assert (await s.get(Media, "uniq")).photo_file_id == "photo-1000"


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
    job = await captioned_job(session, photo_file_id=None)
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
