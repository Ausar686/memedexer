"""Posts captions as silent photo replies and alerts the owner; plugs into the worker as its `JobListener`."""

import asyncio
import contextlib
import datetime as dt
import html
import logging
import typing as t

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputFile, Message, ReplyParameters
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.config import Settings
from memedexer.delivery.format import format_caption
from memedexer.delivery.throttle import Throttle
from memedexer.pipeline import budget
from memedexer.pipeline.images import ImageError, prepare_image
from memedexer.pipeline.worker import Downloader, DownloadError
from memedexer.storage import repo
from memedexer.storage.models import NO_TOPIC, Caption, Chat, ChatStatus, Job, JobStatus, Media, utcnow
from memedexer.telegram.membership import describe_chat, notify_owner

log = logging.getLogger(__name__)

# Replies older than this are not worth posting after a restart; the chat has moved on.
UNDELIVERED_LOOKBACK = dt.timedelta(hours=24)
MAX_SEND_ATTEMPTS = 4
SEND_RETRY_SECONDS = 5.0
ALERT_COOLDOWN = dt.timedelta(hours=1)
ALERT_ERROR_CHARS = 500


class DeliveryFailed(Exception):
    def __init__(self, message: str, *, alert: bool) -> None:
        super().__init__(message)
        self.alert = alert


def message_link(chat_id: int, message_id: int) -> str | None:
    """Private `t.me/c/` links exist only for supergroups, whose ids are `-100` + the internal id."""
    raw = str(chat_id)
    return f"https://t.me/c/{raw[4:]}/{message_id}" if raw.startswith("-100") else None


class Delivery:
    def __init__(
        self,
        *,
        bot: Bot,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        downloader: Downloader,
        throttle: Throttle | None = None,
        sleep: t.Callable[[float], t.Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bot = bot
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._downloader = downloader
        self._throttle = throttle or Throttle()
        self._sleep = sleep
        self._queues: dict[int, asyncio.Queue[int]] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._last_alert: dict[tuple[int, str, str], dt.datetime] = {}

    async def start(self) -> None:
        """Re-queue replies a restart interrupted."""
        async with self._sessionmaker() as session:
            jobs = await repo.undelivered_jobs(session, utcnow() - UNDELIVERED_LOOKBACK)
        for job in jobs:
            self._enqueue(job.chat_id, job.id)
        return None

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return None

    async def join(self) -> None:
        """Wait until every queued reply has been handled."""
        for queue in list(self._queues.values()):
            await queue.join()
        return None

    async def job_finished(self, job: Job) -> None:
        match job.status:
            case JobStatus.DONE:
                self._enqueue(job.chat_id, job.id)
            case JobStatus.FAILED | JobStatus.REFUSED:
                await self._alert_failure(job)
            case JobStatus.BUDGET_EXCEEDED:
                await self._alert_budget(job)
        return None

    def _enqueue(self, chat_id: int, job_id: int) -> None:
        # One queue and task per chat keeps replies in order and lets one busy chat wait without blocking others.
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
            self._tasks[chat_id] = asyncio.create_task(self._drain(self._queues[chat_id]), name=f"delivery:{chat_id}")
        self._queues[chat_id].put_nowait(job_id)

    async def _drain(self, queue: asyncio.Queue[int]) -> None:
        while True:
            job_id = await queue.get()
            try:
                await self.deliver(job_id)
            except Exception:
                log.exception("delivering job %d crashed", job_id)
            finally:
                queue.task_done()

    async def deliver(self, job_id: int) -> None:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None or job.reply_message_id is not None or job.delivery_error is not None:
                return
            chat = await session.get(Chat, job.chat_id)
            if chat is None or chat.status is not ChatStatus.APPROVED:
                job.delivery_error = "chat is not approved"
                await session.commit()
                return
            caption = await session.get(Caption, job.caption_id)
            media = await session.get(Media, job.file_unique_id)

        try:
            reply = await self._send(job, media, format_caption(caption.text, caption.description, caption.tags))
        except DeliveryFailed as exc:
            async with self._sessionmaker() as session:
                stored = await session.get(Job, job_id)
                stored.delivery_error = str(exc)
                await session.commit()
            log.warning("job %d not delivered: %s", job_id, exc)
            if exc.alert:
                await self._notify(chat, job, f"⚠️ Couldn't post a caption in {describe_chat(chat)}", str(exc))
            return

        async with self._sessionmaker() as session:
            stored = await session.get(Job, job_id)
            stored.reply_message_id = reply.message_id
            stored_media = await session.get(Media, media.file_unique_id)
            if stored_media.photo_file_id is None and reply.photo:
                stored_media.photo_file_id = reply.photo[-1].file_id
            await session.commit()
        return None

    async def _send(self, job: Job, media: Media, caption: str) -> Message:
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                photo = media.photo_file_id or await self._upload_source(media)
                await self._throttle.wait(job.chat_id)
                return await self._bot.send_photo(
                    job.chat_id,
                    photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=None if job.thread_id == NO_TOPIC else job.thread_id,
                    reply_parameters=ReplyParameters(message_id=job.message_id, allow_sending_without_reply=False),
                    disable_notification=True,
                )
            except TelegramRetryAfter as exc:
                last_error = exc
                await self._sleep(exc.retry_after)
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                # The original was deleted or the bot lost access: nothing to retry and nothing the owner must do.
                raise DeliveryFailed(str(exc), alert=False) from exc
            except (ImageError, DownloadError) as exc:
                if isinstance(exc, ImageError) or not exc.retryable:
                    raise DeliveryFailed(str(exc), alert=True) from exc
                last_error = exc
                await self._sleep(SEND_RETRY_SECONDS * attempt)
            except (TelegramAPIError, aiohttp.ClientError, TimeoutError) as exc:
                last_error = exc
                await self._sleep(SEND_RETRY_SECONDS * attempt)
        raise DeliveryFailed(f"gave up after {MAX_SEND_ATTEMPTS} attempts: {last_error}", alert=True)

    async def _upload_source(self, media: Media) -> InputFile:
        """Image documents can't be re-sent as photos by file id, so upload the prepared image once."""
        data = await self._downloader.download(media.file_id)
        image = await asyncio.to_thread(prepare_image, data, media.mime_type)
        extension = image.media_type.removeprefix("image/")
        return BufferedInputFile(image.data, filename=f"{media.file_unique_id}.{extension}")

    async def _alert_failure(self, job: Job) -> None:
        async with self._sessionmaker() as session:
            chat = await session.get(Chat, job.chat_id)
        verb = "refused" if job.status is JobStatus.REFUSED else "failed"
        await self._notify(chat, job, f"⚠️ Captioning {verb} in {describe_chat(chat)}", job.error or "")

    async def _alert_budget(self, job: Job) -> None:
        finished_at = job.finished_at or utcnow()
        async with self._sessionmaker() as session:
            chat = await session.get(Chat, job.chat_id)
            period = await budget.exceeded_period(session, job.chat_id, self._settings, finished_at)
            if period is None:
                return
            if await repo.earlier_budget_exceeded(session, job, budget.period_starts(finished_at)[period]):
                return
        limit = budget.limits_microusd(self._settings)[period] / 1_000_000
        await notify_owner(
            self._bot,
            self._settings,
            f"💸 {describe_chat(chat)} reached its {period} budget of ${limit:.2f}. "
            "Captioning is paused there until the period resets (UTC).",
        )

    async def _notify(self, chat: Chat, job: Job, headline: str, error: str) -> None:
        """DM the owner, at most once per chat and error per cooldown so an outage doesn't flood them."""
        key = (chat.id, headline, error)
        now = utcnow()
        if (last := self._last_alert.get(key)) is not None and now - last < ALERT_COOLDOWN:
            return
        self._last_alert[key] = now
        lines = [headline, f"<code>{html.escape(error[:ALERT_ERROR_CHARS])}</code>"]
        if link := message_link(chat.id, job.message_id):
            lines.append(link)
        await notify_owner(self._bot, self._settings, "\n".join(lines))
