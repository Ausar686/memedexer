"""Background job processing: claim, dedup, budget-check, download, sample frames, caption, record."""

import asyncio
import datetime as dt
import logging
import typing as t

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.captioning.base import CaptionError, CaptionProvider, CaptionRefused, Image, Usage
from memedexer.captioning.catalog import ModelSpec, cost_microusd
from memedexer.config import Provider, Settings
from memedexer.pipeline import budget
from memedexer.pipeline.images import ImageError
from memedexer.pipeline.resolve import resolve_caption_settings
from memedexer.pipeline.sandbox import VideoError, extract_frames, prepare_image
from memedexer.redaction import redact
from memedexer.storage import repo
from memedexer.storage.models import (
    TERMINAL_JOB_STATUSES,
    VIDEO_MEDIA_TYPES,
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
# Bot API `getFile` refuses anything larger.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class DownloadError(Exception):
    """`too_big` when Telegram refused the file for the download limit, whatever size the message stated."""

    def __init__(self, message: str, *, retryable: bool, too_big: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.too_big = too_big


class Downloader(t.Protocol):
    async def download(self, file_id: str) -> bytes: ...


class JobListener(t.Protocol):
    async def job_finished(self, job: Job) -> None: ...


class NullListener:
    async def job_finished(self, job: Job) -> None:
        return None


def over_download_limit(file_size: int | None) -> bool:
    return file_size is not None and file_size > MAX_DOWNLOAD_BYTES


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
        self._stopping = asyncio.Event()

    def notify(self) -> None:
        """Wake idle loops after a job is enqueued instead of waiting for the next poll."""
        self._wake.set()

    def stop(self) -> None:
        """Ask every loop to finish its current job and exit, so `run()` returns without interrupting a query.

        Cancelling mid-query instead aborts SQLAlchemy's connection reset and can leave a connection close that
        never completes, hanging the event loop's shutdown.
        """
        self._stopping.set()
        self._wake.set()

    async def run(self) -> None:
        async with self._sessionmaker() as session:
            exhausted = await repo.fail_exhausted_interrupted_jobs(session, MAX_ATTEMPTS)
            requeued = await repo.requeue_interrupted_jobs(session)
            await session.commit()
        for job in exhausted:
            log.error("job %d was interrupted on its last attempt; failing it", job.id)
            await self._notify_listener(job)
        if requeued:
            log.info("requeued %d interrupted jobs", requeued)
        await asyncio.gather(*(self._loop() for _ in range(self._settings.worker_concurrency)))

    async def _loop(self) -> None:
        while not self._stopping.is_set():
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
                error = redact(f"internal error: {exc!r}", self._settings.secret_values)
                repo.finish_job(job, JobStatus.FAILED, error=error)
            await session.commit()
        if job.status in TERMINAL_JOB_STATUSES:
            await self._notify_listener(job)
        return True

    async def _notify_listener(self, job: Job) -> None:
        try:
            await self._listener.job_finished(job)
        except Exception:
            log.exception("listener failed for job %d", job.id)

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

        limit = await budget.exceeded_limit(session, chat.id, self._settings, utcnow())
        if limit is not None:
            repo.finish_job(job, JobStatus.BUDGET_EXCEEDED, error=f"{limit} limit reached")
            return

        provider = self._providers.get(resolved.model.provider)
        if provider is None:
            repo.finish_job(job, JobStatus.FAILED, error=f"no API key for {resolved.model.provider!s}")
            return

        media = await session.get(Media, job.file_unique_id)
        # Don't hold a database transaction open across the slow download and API call.
        await session.commit()
        try:
            images = await self._load(job, media)
        except DownloadError as exc:
            self._fail(job, str(exc), retryable=exc.retryable)
            return
        except (ImageError, VideoError) as exc:
            self._fail(job, str(exc), retryable=False)
            return

        try:
            result = await provider.caption(
                images, model=resolved.model, description_language=resolved.description_language
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

    async def _load(self, job: Job, media: Media) -> list[Image]:
        """The picture to caption, or a video's sampled frames (its thumbnail when it's too big to download)."""
        if media.media_type not in VIDEO_MEDIA_TYPES:
            data = await self._downloader.download(media.file_id)
            return [await prepare_image(data)]
        if over_download_limit(media.file_size):
            return await self._thumbnail(job, media)
        try:
            data = await self._downloader.download(media.file_id)
        except DownloadError as exc:
            # Messages don't always state a size; Telegram's refusal is the final word.
            if not exc.too_big:
                raise
            return await self._thumbnail(job, media)
        return await extract_frames(data, media.duration)

    async def _thumbnail(self, job: Job, media: Media) -> list[Image]:
        if media.thumbnail_file_id is None:
            raise VideoError("the video is over the Bot API's 20 MB download limit and has no thumbnail")
        job.thumbnail_only = True
        data = await self._downloader.download(media.thumbnail_file_id)
        return [await prepare_image(data)]

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
