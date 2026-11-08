"""Group message handlers that turn incoming images and videos into captioning jobs."""

import logging
import typing as t

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.storage import repo
from memedexer.storage.models import ChatStatus, JobTrigger, Topic
from memedexer.telegram.media import extract_media, save_media, topic_id

log = logging.getLogger(__name__)

_in_group = F.chat.type.in_({"group", "supergroup"})
_has_media = F.photo | F.document | F.video | F.animation


class Notifier(t.Protocol):
    def notify(self) -> None: ...


class ReplyHider(t.Protocol):
    async def hide_replies(self, chat_id: int, message_id: int) -> None: ...


def build_router() -> Router:
    router = Router(name="ingestion")
    router.message.register(on_media, _in_group, _has_media)
    router.edited_message.register(on_edited_media, _in_group, _has_media)
    return router


async def on_media(message: Message, sessionmaker: async_sessionmaker[AsyncSession], worker: Notifier) -> None:
    await ingest(message, JobTrigger.MESSAGE, sessionmaker, worker)


async def on_edited_media(
    message: Message, sessionmaker: async_sessionmaker[AsyncSession], worker: Notifier, delivery: ReplyHider
) -> None:
    await ingest(message, JobTrigger.EDIT, sessionmaker, worker, delivery)


async def ingest(
    message: Message,
    trigger: JobTrigger,
    sessionmaker: async_sessionmaker[AsyncSession],
    worker: Notifier,
    delivery: ReplyHider | None = None,
) -> bool:
    """Enqueue a job if the chat is approved and the topic has captioning on; True when one was enqueued.

    An edit that keeps the file but adds a spoiler hides the replies already posted instead.
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
        await save_media(session, media)
        job = await repo.enqueue_job(
            session,
            chat_id=chat.id,
            thread_id=thread_id,
            message_id=message.message_id,
            file_unique_id=media.file_unique_id,
            trigger=trigger,
            has_spoiler=bool(message.has_media_spoiler),
        )
        await session.commit()
    log.info("enqueued job %d (%s) for chat %d message %d", job.id, trigger, chat.id, message.message_id)
    worker.notify()
    return True
