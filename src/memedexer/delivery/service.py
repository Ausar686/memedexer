"""Posts captions as silent media replies and alerts the owner; plugs into the worker as its `JobListener`."""

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
from aiogram.types import (
    BufferedInputFile,
    InputFile,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    ReplyParameters,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.config import Settings
from memedexer.cooldown import Cooldown
from memedexer.delivery.format import format_caption
from memedexer.delivery.throttle import Throttle
from memedexer.pipeline import budget
from memedexer.pipeline.images import ImageError, prepare_image
from memedexer.pipeline.worker import Downloader, DownloadError, over_download_limit
from memedexer.storage import repo
from memedexer.storage.models import NO_TOPIC, Caption, Chat, ChatStatus, Job, JobStatus, Media, MediaType, utcnow
from memedexer.telegram.membership import describe_chat, notify_owner

log = logging.getLogger(__name__)

# Replies older than this are not worth posting after a restart; the chat has moved on.
UNDELIVERED_LOOKBACK = dt.timedelta(hours=24)
MAX_SEND_ATTEMPTS = 4
SEND_RETRY_SECONDS = 5.0
ALERT_INTERVAL_SECONDS = 60 * 60
ALERT_ERROR_CHARS = 500
CLOSE_TIMEOUT_SECONDS = 10.0
VIDEO_EXTENSIONS = {"video/quicktime": "mov", "video/webm": "webm", "video/x-matroska": "mkv"}


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
        self._alerts = Cooldown(ALERT_INTERVAL_SECONDS)

    async def start(self) -> None:
        """Re-queue replies a restart interrupted."""
        async with self._sessionmaker() as session:
            jobs = await repo.undelivered_jobs(session, utcnow() - UNDELIVERED_LOOKBACK)
        for job in jobs:
            self._enqueue(job.chat_id, job.id)
        return None

    async def close(self, timeout: float = CLOSE_TIMEOUT_SECONDS) -> None:
        """Let queued replies finish, then stop the idle queue tasks.

        Replies still pending after `timeout` are cancelled and retried by `start()` on the next run.
        """
        try:
            await asyncio.wait_for(self.join(), timeout)
        except TimeoutError:
            log.warning("abandoning unfinished replies after %.0f s; they'll be retried on the next start", timeout)
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
                if job.thumbnail_only:
                    await self._notice_thumbnail(job)
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
            reply = await self._send(job, media, _caption_html(caption, spoiler=job.has_spoiler))
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
            if stored_media.reply_file_id is None:
                stored_media.reply_file_id = _reply_file_id(media.media_type, reply)
            await session.commit()
            # Re-read after the commit: the source may have become a spoiler while this reply was being sent, too
            # late for hide_replies to see the reply.
            await session.refresh(stored, ["has_spoiler"])
        if stored.has_spoiler and not job.has_spoiler:
            await self._hide(job_id)
        return None

    async def hide_replies(self, chat_id: int, message_id: int) -> None:
        """Blur the replies to a message whose media was marked as a spoiler after it was captioned."""
        async with self._sessionmaker() as session:
            exposed = [job for job in await repo.jobs_for_message(session, chat_id, message_id) if not job.has_spoiler]
            for job in exposed:
                job.has_spoiler = True
            await session.commit()
        for job in exposed:
            if job.reply_message_id is not None:
                await self._hide(job.id)
        return None

    async def _hide(self, job_id: int) -> None:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            chat = await session.get(Chat, job.chat_id)
            caption = await session.get(Caption, job.caption_id)
            media = await session.get(Media, job.file_unique_id)
        if media.reply_file_id is None:
            # The reply went back as a document (a video too big to re-upload), and documents can't be blurred.
            log.warning("reply of job %d is a document and can't be hidden", job_id)
            return None

        input_media = {
            MediaType.PHOTO: InputMediaPhoto,
            MediaType.IMAGE_DOCUMENT: InputMediaPhoto,
            MediaType.VIDEO: InputMediaVideo,
            MediaType.VIDEO_DOCUMENT: InputMediaVideo,
            MediaType.ANIMATION: InputMediaAnimation,
        }[media.media_type]

        async def edit() -> object:
            await self._throttle.wait(job.chat_id)
            return await self._bot.edit_message_media(
                input_media(
                    media=media.reply_file_id,
                    caption=_caption_html(caption, spoiler=True),
                    parse_mode=ParseMode.HTML,
                    has_spoiler=True,
                ),
                chat_id=job.chat_id,
                message_id=job.reply_message_id,
            )

        try:
            await self._with_retries(edit)
        except DeliveryFailed as exc:
            log.warning("reply of job %d not hidden: %s", job_id, exc)
            if exc.alert:
                await self._notify(chat, job, f"⚠️ Couldn't hide a spoiler reply in {describe_chat(chat)}", str(exc))
        return None

    async def _send(self, job: Job, media: Media, caption: str) -> Message:
        reply: dict[str, t.Any] = {
            "chat_id": job.chat_id,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
            "message_thread_id": None if job.thread_id == NO_TOPIC else job.thread_id,
            "reply_parameters": ReplyParameters(message_id=job.message_id, allow_sending_without_reply=False),
            "disable_notification": True,
        }

        async def send() -> Message:
            match media.media_type:
                case MediaType.PHOTO | MediaType.IMAGE_DOCUMENT:
                    photo = media.reply_file_id or await self._upload_image(media)
                    await self._throttle.wait(job.chat_id)
                    return await self._bot.send_photo(photo=photo, has_spoiler=job.has_spoiler, **reply)
                case MediaType.ANIMATION:
                    await self._throttle.wait(job.chat_id)
                    return await self._bot.send_animation(
                        animation=media.reply_file_id, has_spoiler=job.has_spoiler, **reply
                    )
                case MediaType.VIDEO_DOCUMENT if media.reply_file_id is None and over_download_limit(media.file_size):
                    # Too big to download and re-upload as a video, so it goes back as the file it came as.
                    await self._throttle.wait(job.chat_id)
                    return await self._bot.send_document(document=media.file_id, **reply)
                case MediaType.VIDEO | MediaType.VIDEO_DOCUMENT:
                    video = media.reply_file_id or await self._upload_video(media)
                    await self._throttle.wait(job.chat_id)
                    return await self._bot.send_video(video=video, has_spoiler=job.has_spoiler, **reply)

        return await self._with_retries(send)

    async def _with_retries[T](self, call: t.Callable[[], t.Awaitable[T]]) -> T:
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                return await call()
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

    async def _upload_image(self, media: Media) -> InputFile:
        """Image documents can't be re-sent as photos by file id, so upload the prepared image once."""
        data = await self._downloader.download(media.file_id)
        image = await asyncio.to_thread(prepare_image, data, media.mime_type)
        extension = image.media_type.removeprefix("image/")
        return BufferedInputFile(image.data, filename=f"{media.file_unique_id}.{extension}")

    async def _upload_video(self, media: Media) -> InputFile:
        """Video documents can't be re-sent as videos by file id, so upload the file once."""
        data = await self._downloader.download(media.file_id)
        extension = VIDEO_EXTENSIONS.get(media.mime_type, "mp4")
        return BufferedInputFile(data, filename=f"{media.file_unique_id}.{extension}")

    async def _notice_thumbnail(self, job: Job) -> None:
        async with self._sessionmaker() as session:
            chat = await session.get(Chat, job.chat_id)
        await self._notify(
            chat,
            job,
            f"ℹ️ A video in {describe_chat(chat)} is over the Bot API's 20 MB download limit, so it was captioned "
            "from its small preview only; text the preview doesn't show is missing.",
        )

    async def _alert_failure(self, job: Job) -> None:
        async with self._sessionmaker() as session:
            chat = await session.get(Chat, job.chat_id)
        verb = "refused" if job.status is JobStatus.REFUSED else "failed"
        await self._notify(chat, job, f"⚠️ Captioning {verb} in {describe_chat(chat)}", job.error or "")

    async def _alert_budget(self, job: Job) -> None:
        finished_at = job.finished_at or utcnow()
        async with self._sessionmaker() as session:
            chat = await session.get(Chat, job.chat_id)
            limit = await budget.exceeded_limit(session, job.chat_id, self._settings, finished_at)
            if limit is None:
                return
            since = budget.period_starts(finished_at)[limit.period]
            if await repo.earlier_budget_exceeded(session, job, since, any_chat=limit.total):
                return
        usd = limit.microusd / 1_000_000
        if limit.total:
            text = (
                f"💸 Spending across all chats reached the total {limit.period} budget of ${usd:.2f}. "
                "Captioning is paused in every chat until the period resets (UTC)."
            )
        else:
            text = (
                f"💸 {describe_chat(chat)} reached its {limit.period} budget of ${usd:.2f}. "
                "Captioning is paused there until the period resets (UTC)."
            )
        await notify_owner(self._bot, self._settings, text)

    async def _notify(self, chat: Chat, job: Job, headline: str, error: str = "") -> None:
        """DM the owner, at most once per chat and kind of alert per interval, so an outage or a flood of bad files
        doesn't flood them; the error text isn't part of the key, since it can differ for every file."""
        key = (chat.id, headline)
        if not self._alerts.ready(key):
            return
        lines = [headline]
        if error:
            lines.append(f"<code>{html.escape(error[:ALERT_ERROR_CHARS])}</code>")
        if suppressed := self._alerts.pop_suppressed(key):
            lines.append(f"({suppressed} more like this in the last hour weren't sent.)")
        if link := message_link(chat.id, job.message_id):
            lines.append(link)
        await notify_owner(self._bot, self._settings, "\n".join(lines))


def _caption_html(caption: Caption, *, spoiler: bool) -> str:
    return format_caption(caption.text, caption.description, caption.tags, spoiler=spoiler)


def _reply_file_id(media_type: MediaType, reply: Message) -> str | None:
    """The reply's own file id, when it has the type the media's send method needs."""
    match media_type:
        case MediaType.PHOTO | MediaType.IMAGE_DOCUMENT if reply.photo:
            return reply.photo[-1].file_id
        case MediaType.VIDEO | MediaType.VIDEO_DOCUMENT if reply.video:
            return reply.video.file_id
        case MediaType.ANIMATION if reply.animation:
            return reply.animation.file_id
    return None
