"""Group message handlers that turn incoming images and videos into captioning jobs."""

import asyncio
import logging
import typing as t

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.config import Settings
from memedexer.cooldown import Cooldown
from memedexer.storage import repo
from memedexer.storage.models import Chat, ChatStatus, Job, JobTrigger, Topic
from memedexer.telegram.media import IncomingMedia, extract_media, save_media, topic_id
from memedexer.telegram.membership import describe_chat, notify_owner

log = logging.getLogger(__name__)

_in_group = F.chat.type.in_({"group", "supergroup"})
_has_media = F.photo | F.document | F.video | F.animation

# Past this many waiting or running captions, a chat's new media is skipped until the queue drains, so one chat can't
# pile up unbounded work (and spend) faster than it's captioned.
MAX_QUEUED_JOBS_PER_CHAT = 50
BACKLOG_ALERT_INTERVAL_SECONDS = 60 * 60


class Notifier(t.Protocol):
    def notify(self) -> None: ...


class ReplyHider(t.Protocol):
    async def hide_replies(self, chat_id: int, message_id: int) -> None: ...


class BacklogFull(Exception):
    def __init__(self, chat: Chat) -> None:
        super().__init__(f"chat {chat.id} has {MAX_QUEUED_JOBS_PER_CHAT} captions queued")
        self.chat = chat


def build_router() -> Router:
    router = Router(name="ingestion")
    router.message.register(on_media, _in_group, _has_media)
    router.edited_message.register(on_edited_media, _in_group, _has_media)
    return router


async def on_media(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
    worker: Notifier,
    bot: Bot,
    settings: Settings,
    backlog_alerts: Cooldown,
    enqueue_lock: asyncio.Lock,
) -> None:
    try:
        await ingest(message, JobTrigger.MESSAGE, sessionmaker, worker, enqueue_lock)
    except BacklogFull as full:
        await alert_backlog(full.chat, bot, settings, backlog_alerts)


async def on_edited_media(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
    worker: Notifier,
    delivery: ReplyHider,
    bot: Bot,
    settings: Settings,
    backlog_alerts: Cooldown,
    enqueue_lock: asyncio.Lock,
) -> None:
    try:
        await ingest(message, JobTrigger.EDIT, sessionmaker, worker, enqueue_lock, delivery)
    except BacklogFull as full:
        await alert_backlog(full.chat, bot, settings, backlog_alerts)


async def alert_backlog(chat: Chat, bot: Bot, settings: Settings, backlog_alerts: Cooldown) -> None:
    log.warning("skipping media in chat %d: %d captions are queued", chat.id, MAX_QUEUED_JOBS_PER_CHAT)
    if not backlog_alerts.ready(chat.id):
        return None
    text = (
        f"⚠️ {describe_chat(chat)} has {MAX_QUEUED_JOBS_PER_CHAT} captions queued, so new media there is skipped "
        "until they're done."
    )
    if skipped := backlog_alerts.pop_suppressed(chat.id):
        text += f" {skipped} more were skipped since the last notice."
    await notify_owner(bot, settings, text)
    return None


async def ingest(
    message: Message,
    trigger: JobTrigger,
    sessionmaker: async_sessionmaker[AsyncSession],
    worker: Notifier,
    enqueue_lock: asyncio.Lock,
    delivery: ReplyHider | None = None,
) -> bool:
    """Enqueue a job if the chat is approved and the topic has captioning on; True when one was enqueued.

    An edit that keeps the file but adds a spoiler hides the replies already posted instead. Raises `BacklogFull` when
    the chat has `MAX_QUEUED_JOBS_PER_CHAT` jobs queued.
    """
    media = extract_media(message)
    if media is None:
        return False
    thread_id = topic_id(message)
    async with sessionmaker() as session:
        chat = await repo.get_chat(session, message.chat.id)
        if chat is None or chat.status is not ChatStatus.APPROVED:
            return False
        topic = await session.get(Topic, (chat.id, thread_id))
        if topic is None or not topic.captioning_enabled:
            return False
        if trigger is JobTrigger.EDIT:
            # Edits that only touch the text keep the same file; only replaced media needs a new caption.
            previous = await repo.latest_job_for_message(session, chat.id, message.message_id)
            if previous is not None and previous.file_unique_id == media.file_unique_id:
                if message.has_media_spoiler and delivery is not None:
                    await delivery.hide_replies(chat.id, message.message_id)
                return False
        job = await enqueue_within_cap(
            session,
            enqueue_lock,
            chat,
            media,
            thread_id=thread_id,
            message_id=message.message_id,
            trigger=trigger,
            has_spoiler=bool(message.has_media_spoiler),
        )
    log.info("enqueued job %d (%s) for chat %d message %d", job.id, trigger, chat.id, message.message_id)
    worker.notify()
    return True


async def enqueue_within_cap(
    session: AsyncSession, enqueue_lock: asyncio.Lock, chat: Chat, media: IncomingMedia, **job: t.Any
) -> Job:
    """Save the media and enqueue its job, unless the chat has `MAX_QUEUED_JOBS_PER_CHAT` queued (`BacklogFull`).

    Updates are handled concurrently; counting and inserting under one lock keeps a burst from all seeing room for one
    more.
    """
    async with enqueue_lock:
        if await repo.count_queued_jobs(session, chat.id) >= MAX_QUEUED_JOBS_PER_CHAT:
            raise BacklogFull(chat)
        await save_media(session, media)
        enqueued = await repo.enqueue_job(session, chat_id=chat.id, file_unique_id=media.file_unique_id, **job)
        await session.commit()
    return enqueued
