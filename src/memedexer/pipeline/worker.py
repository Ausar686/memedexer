"""Background job processing: claim, dedup, budget-check, download, caption, record."""

import asyncio
import datetime as dt
import logging
import typing as t

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.captioning.base import CaptionError, CaptionProvider, CaptionRefused, Usage
from memedexer.captioning.catalog import ModelSpec, cost_microusd
from memedexer.config import Provider, Settings
from memedexer.pipeline import budget
from memedexer.pipeline.images import ImageError, prepare_image
from memedexer.pipeline.resolve import resolve_caption_settings
from memedexer.storage import repo
from memedexer.storage.models import (
    TERMINAL_JOB_STATUSES,
    Caption,
    Chat,
    ChatStatus,
    Job,
    JobStatus,
    JobTrigger,
    Media,
    Topic,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
RETRY_BASE_DELAY = dt.timedelta(seconds=30)
RETRY_MAX_DELAY = dt.timedelta(minutes=15)
IDLE_POLL_SECONDS = 5.0


class DownloadError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Downloader(t.Protocol):
    async def download(self, file_id: str) -> bytes: ...


class JobListener(t.Protocol):
    async def job_finished(self, job: Job) -> None: ...


class NullListener:
    async def job_finished(self, job: Job) -> None:
        return None


def retry_delay(attempts: int) -> dt.timedelta:
    return min(RETRY_BASE_DELAY * 4 ** (attempts - 1), RETRY_MAX_DELAY)


class Worker:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        providers: t.Mapping[Provider, CaptionProvider],
        downloader: Downloader,
        listener: JobListener | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._providers = providers
        self._downloader = downloader
        self._listener = listener or NullListener()
        self._wake = asyncio.Event()

    def notify(self) -> None:
        """Wake idle loops after a job is enqueued instead of waiting for the next poll."""
        self._wake.set()

    async def run(self) -> None:
        async with self._sessionmaker() as session:
            requeued = await repo.requeue_interrupted_jobs(session)
            await session.commit()
        if requeued:
            log.info("requeued %d interrupted jobs", requeued)
        await asyncio.gather(*(self._loop() for _ in range(self._settings.worker_concurrency)))

    async def _loop(self) -> None:
        while True:
            if await self.run_once():
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), IDLE_POLL_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()

    async def run_once(self) -> bool:
        """Process one due job; False when the queue has nothing to do right now."""
        async with self._sessionmaker() as session:
            job = await repo.claim_next_job(session)
            await session.commit()
            if job is None:
                return False
            try:
                await self._process(session, job)
            except Exception as exc:
                log.exception("job %d crashed", job.id)
                await session.rollback()
                job = await session.get(Job, job.id)
                repo.finish_job(job, JobStatus.FAILED, error=f"internal error: {exc!r}")
            await session.commit()
        if job.status in TERMINAL_JOB_STATUSES:
            try:
                await self._listener.job_finished(job)
            except Exception:
                log.exception("listener failed for job %d", job.id)
        return True

    async def _process(self, session: AsyncSession, job: Job) -> None:
        chat = await session.get(Chat, job.chat_id)
        if chat is None or chat.status is not ChatStatus.APPROVED:
            repo.finish_job(job, JobStatus.FAILED, error="chat is not approved")
            return
        topic = await session.get(Topic, (job.chat_id, job.thread_id))
        resolved = resolve_caption_settings(chat, topic, self._settings)

        if job.trigger is not JobTrigger.RECAPTION:
            cached = await repo.latest_caption(session, job.file_unique_id)
            if cached is not None:
                job.caption_id = cached.id
                repo.finish_job(job, JobStatus.DONE)
                return

        period = await budget.exceeded_period(session, chat.id, self._settings, utcnow())
        if period is not None:
            repo.finish_job(job, JobStatus.BUDGET_EXCEEDED, error=f"{period} limit reached")
            return

        provider = self._providers.get(resolved.model.provider)
        if provider is None:
            repo.finish_job(job, JobStatus.FAILED, error=f"no API key for {resolved.model.provider!s}")
            return

        media = await session.get(Media, job.file_unique_id)
        # Don't hold a database transaction open across the slow download and API call.
        await session.commit()
        try:
            data = await self._downloader.download(media.file_id)
            image = await asyncio.to_thread(prepare_image, data, media.mime_type)
        except DownloadError as exc:
            self._fail(job, str(exc), retryable=exc.retryable)
            return
        except ImageError as exc:
            self._fail(job, str(exc), retryable=False)
            return

        try:
            result = await provider.caption(
                image, model=resolved.model, description_language=resolved.description_language
            )
        except CaptionError as exc:
            self._charge(job, resolved.model, exc.usage)
            if isinstance(exc, CaptionRefused):
                repo.finish_job(job, JobStatus.REFUSED, error=str(exc))
            else:
                self._fail(job, str(exc), retryable=exc.retryable)
            return

        self._charge(job, resolved.model, result.usage)
        caption = Caption(
            file_unique_id=job.file_unique_id,
            provider=resolved.model.provider,
            model=resolved.model.id,
            text=result.text,
            description=result.description,
            tags=result.tags,
            languages=result.languages,
            kind=result.kind,
        )
        session.add(caption)
        await session.flush()
        job.caption_id = caption.id
        repo.finish_job(job, JobStatus.DONE)

    @staticmethod
    def _charge(job: Job, model: ModelSpec, usage: Usage | None) -> None:
        if usage is None:
            return
        job.input_tokens += usage.input_tokens
        job.output_tokens += usage.output_tokens
        job.cost_microusd += cost_microusd(model, usage.input_tokens, usage.output_tokens)

    @staticmethod
    def _fail(job: Job, error: str, *, retryable: bool) -> None:
        if retryable and job.attempts < MAX_ATTEMPTS:
            repo.retry_job(job, error=error, delay=retry_delay(job.attempts))
        else:
            repo.finish_job(job, JobStatus.FAILED, error=error)
