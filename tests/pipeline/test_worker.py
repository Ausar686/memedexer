import asyncio
import datetime as dt
import decimal
import pathlib

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memedexer.captioning.base import CaptionError, CaptionRefused, CaptionResult, Image, Usage
from memedexer.captioning.catalog import ModelSpec
from memedexer.config import Provider, Settings
from memedexer.pipeline.worker import MAX_ATTEMPTS, DownloadError, Worker, retry_delay
from memedexer.storage import db, repo
from memedexer.storage.models import Caption, ChatStatus, Job, JobStatus, JobTrigger, utcnow
from tests.factories import CHAT_ID, THREAD_ID, seed_chat, seed_job

MEME = (pathlib.Path(__file__).parent.parent / "fixtures" / "meme.png").read_bytes()
USAGE = Usage(1000, 100)


def caption_result() -> CaptionResult:
    return CaptionResult(
        text="WHEN ALL TESTS PASS", description="A smiley.", tags=["#tests"], languages=["en"], kind="meme", usage=USAGE
    )


class FakeProvider:
    def __init__(self, *results: CaptionResult | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[Image, ModelSpec, str | None]] = []

    async def caption(self, image: Image, *, model: ModelSpec, description_language: str | None) -> CaptionResult:
        self.calls.append((image, model, description_language))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class FakeDownloader:
    def __init__(self, result: bytes | Exception = MEME) -> None:
        self.result = result
        self.file_ids: list[str] = []

    async def download(self, file_id: str) -> bytes:
        self.file_ids.append(file_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SpyListener:
    def __init__(self) -> None:
        self.finished: list[tuple[int, JobStatus]] = []

    async def job_finished(self, job: Job) -> None:
        self.finished.append((job.id, job.status))


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return db.create_sessionmaker(engine)


def make_worker(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    provider: FakeProvider | None = None,
    downloader: FakeDownloader | None = None,
    listener: SpyListener | None = None,
) -> Worker:
    return Worker(
        sessionmaker=sessionmaker,
        settings=settings,
        providers={Provider.ANTHROPIC: provider or FakeProvider(caption_result())},
        downloader=downloader or FakeDownloader(),
        listener=listener,
    )


async def load_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> Job:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def make_due(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> None:
    async with sessionmaker() as session:
        await session.execute(sa.update(Job).where(Job.id == job_id).values(available_at=utcnow()))
        await session.commit()


async def test_captions_and_records_cost(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    provider, downloader, listener = FakeProvider(caption_result()), FakeDownloader(), SpyListener()

    assert await make_worker(sessionmaker, settings, provider, downloader, listener).run_once() is True

    done = await load_job(sessionmaker, job.id)
    assert done.status is JobStatus.DONE
    assert (done.input_tokens, done.output_tokens, done.cost_microusd) == (1000, 100, 100 + 50)
    assert done.finished_at is not None
    assert downloader.file_ids == ["file-uniq"]
    image, model, language = provider.calls[0]
    assert (image.media_type, model.id, language) == ("image/png", "claude-haiku-5-5", None)
    assert listener.finished == [(job.id, JobStatus.DONE)]
    async with sessionmaker() as s:
        caption = await s.get(Caption, done.caption_id)
    assert (caption.text, caption.tags, caption.provider, caption.model) == (
        "WHEN ALL TESTS PASS",
        ["#tests"],
        "anthropic",
        "claude-haiku-5-5",
    )


async def test_empty_queue(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    assert await make_worker(sessionmaker, settings).run_once() is False


async def test_uses_resolved_model_and_language(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    chat = await repo.get_chat(session, CHAT_ID)
    chat.description_language = "Russian"
    topic = await repo.get_or_create_topic(session, CHAT_ID, THREAD_ID)
    topic.provider, topic.model = "anthropic", "claude-sonnet-5-5"
    await session.commit()
    await seed_job(session)
    provider = FakeProvider(caption_result())

    await make_worker(sessionmaker, settings, provider).run_once()

    _, model, language = provider.calls[0]
    assert (model.id, language) == ("claude-sonnet-5-5", "Russian")


async def test_reuses_existing_caption_without_calling_provider(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    first = await seed_job(session, message_id=1)
    second = await seed_job(session, message_id=2)
    provider = FakeProvider(caption_result())
    worker = make_worker(sessionmaker, settings, provider)

    await worker.run_once()
    await worker.run_once()

    assert len(provider.calls) == 1
    reused = await load_job(sessionmaker, second.id)
    assert reused.status is JobStatus.DONE
    assert reused.caption_id == (await load_job(sessionmaker, first.id)).caption_id
    assert reused.cost_microusd == 0


async def test_recaption_bypasses_dedup(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    await seed_job(session, message_id=1)
    await seed_job(session, message_id=1, trigger=JobTrigger.RECAPTION)
    provider = FakeProvider(caption_result())
    worker = make_worker(sessionmaker, settings, provider)

    await worker.run_once()
    await worker.run_once()

    assert len(provider.calls) == 2


async def test_budget_exceeded_skips_provider(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    settings.cost_limit_daily_usd = decimal.Decimal("0.0001")
    await seed_chat(session)
    first = await seed_job(session, file_unique_id="a")
    second = await seed_job(session, file_unique_id="b")
    provider, listener = FakeProvider(caption_result()), SpyListener()
    worker = make_worker(sessionmaker, settings, provider, listener=listener)

    await worker.run_once()
    await worker.run_once()

    assert len(provider.calls) == 1
    skipped = await load_job(sessionmaker, second.id)
    assert (skipped.status, skipped.error) == (JobStatus.BUDGET_EXCEEDED, "daily limit reached")
    assert listener.finished == [(first.id, JobStatus.DONE), (second.id, JobStatus.BUDGET_EXCEEDED)]


async def test_retryable_error_is_rescheduled_and_charged(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    provider = FakeProvider(CaptionError("overloaded", retryable=True, usage=Usage(10, 0)), caption_result())
    listener = SpyListener()
    worker = make_worker(sessionmaker, settings, provider, listener=listener)

    before = utcnow()
    await worker.run_once()
    pending = await load_job(sessionmaker, job.id)
    assert (pending.status, pending.attempts, pending.error) == (JobStatus.PENDING, 1, "overloaded")
    assert pending.available_at >= before + retry_delay(1)
    assert pending.cost_microusd == 1
    assert listener.finished == []
    assert await worker.run_once() is False

    await make_due(sessionmaker, job.id)
    await worker.run_once()
    done = await load_job(sessionmaker, job.id)
    assert (done.status, done.attempts, done.cost_microusd) == (JobStatus.DONE, 2, 1 + 150)


async def test_retryable_error_gives_up_after_max_attempts(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    worker = make_worker(sessionmaker, settings, FakeProvider(CaptionError("overloaded", retryable=True)))

    for _ in range(MAX_ATTEMPTS):
        await make_due(sessionmaker, job.id)
        await worker.run_once()

    failed = await load_job(sessionmaker, job.id)
    assert (failed.status, failed.attempts) == (JobStatus.FAILED, MAX_ATTEMPTS)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (CaptionError("bad request", retryable=False, usage=Usage(10, 0)), JobStatus.FAILED),
        (CaptionRefused("refused (category: cyber)", usage=Usage(10, 0)), JobStatus.REFUSED),
    ],
)
async def test_permanent_errors_finish_and_charge(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    error: CaptionError,
    status: JobStatus,
) -> None:
    await seed_chat(session)
    job = await seed_job(session)

    await make_worker(sessionmaker, settings, FakeProvider(error)).run_once()

    finished = await load_job(sessionmaker, job.id)
    assert (finished.status, finished.error, finished.cost_microusd) == (status, str(error), 1)


@pytest.mark.parametrize(
    ("downloader", "status"),
    [
        (FakeDownloader(DownloadError("file is too big", retryable=False)), JobStatus.FAILED),
        (FakeDownloader(DownloadError("timeout", retryable=True)), JobStatus.PENDING),
        (FakeDownloader(b"not an image"), JobStatus.FAILED),
    ],
)
async def test_download_and_decode_failures(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    downloader: FakeDownloader,
    status: JobStatus,
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    provider = FakeProvider(caption_result())

    await make_worker(sessionmaker, settings, provider, downloader).run_once()

    assert (await load_job(sessionmaker, job.id)).status is status
    assert provider.calls == []


async def test_missing_provider_key_fails(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    topic = await repo.get_or_create_topic(session, CHAT_ID, THREAD_ID)
    topic.provider, topic.model = "openai", "gpt-6-luna"
    await session.commit()
    job = await seed_job(session)

    await make_worker(sessionmaker, settings).run_once()

    failed = await load_job(sessionmaker, job.id)
    assert (failed.status, failed.error) == (JobStatus.FAILED, "no API key for openai")


async def test_unapproved_chat_fails(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session, status=ChatStatus.REVOKED)
    job = await seed_job(session)

    await make_worker(sessionmaker, settings).run_once()

    assert (await load_job(sessionmaker, job.id)).error == "chat is not approved"


async def test_unexpected_exception_fails_job_and_keeps_worker_alive(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    worker = make_worker(sessionmaker, settings, FakeProvider(RuntimeError("boom")))

    assert await worker.run_once() is True

    failed = await load_job(sessionmaker, job.id)
    assert failed.status is JobStatus.FAILED
    assert "boom" in failed.error


async def test_listener_failure_does_not_break_processing(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    class BrokenListener:
        async def job_finished(self, job: Job) -> None:
            raise RuntimeError("listener down")

    await seed_chat(session)
    job = await seed_job(session)
    worker = make_worker(sessionmaker, settings, listener=BrokenListener())

    assert await worker.run_once() is True
    assert (await load_job(sessionmaker, job.id)).status is JobStatus.DONE


async def test_run_requeues_interrupted_jobs(
    session: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_chat(session)
    job = await seed_job(session)
    await repo.claim_next_job(session)
    await session.commit()
    settings.worker_concurrency = 1
    worker = make_worker(sessionmaker, settings)

    task = asyncio.create_task(worker.run())
    for _ in range(100):
        if (await load_job(sessionmaker, job.id)).status is JobStatus.DONE:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert (await load_job(sessionmaker, job.id)).status is JobStatus.DONE


def test_retry_delay_grows_and_caps() -> None:
    assert [retry_delay(n) for n in (1, 2, 3, 4, 10)] == [
        dt.timedelta(seconds=30),
        dt.timedelta(minutes=2),
        dt.timedelta(minutes=8),
        dt.timedelta(minutes=15),
        dt.timedelta(minutes=15),
    ]
