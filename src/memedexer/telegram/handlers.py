"""Group message handlers that turn incoming images into captioning jobs."""

import logging
import typing as t

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memedexer.storage import repo
from memedexer.storage.models import ChatStatus, JobTrigger, Topic
from memedexer.telegram.media import extract_image, topic_id

log = logging.getLogger(__name__)

_in_group = F.chat.type.in_({"group", "supergroup"})
_has_image = F.photo | F.document


class Notifier(t.Protocol):
    def notify(self) -> None: ...


def build_router() -> Router:
    router = Router(name="ingestion")
    router.message.register(on_image, _in_group, _has_image)
    router.edited_message.register(on_edited_image, _in_group, _has_image)
    return router


async def on_image(message: Message, sessionmaker: async_sessionmaker[AsyncSession], worker: Notifier) -> None:
    await ingest(message, JobTrigger.MESSAGE, sessionmaker, worker)


async def on_edited_image(message: Message, sessionmaker: async_sessionmaker[AsyncSession], worker: Notifier) -> None:
    await ingest(message, JobTrigger.EDIT, sessionmaker, worker)


async def ingest(
    message: Message, trigger: JobTrigger, sessionmaker: async_sessionmaker[AsyncSession], worker: Notifier
) -> bool:
    """Enqueue a job if the chat is approved and the topic has captioning on; True when one was enqueued."""
    image = extract_image(message)
    if image is None:
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
            # Edits that only touch the text keep the same file; only a replaced image needs a new caption.
            previous = await repo.latest_job_for_message(session, chat.id, message.message_id)
            if previous is not None and previous.file_unique_id == image.file_unique_id:
                return False
        await repo.upsert_media(
            session,
            file_unique_id=image.file_unique_id,
            file_id=image.file_id,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            file_size=image.file_size,
            photo_file_id=image.photo_file_id,
        )
        job = await repo.enqueue_job(
            session,
            chat_id=chat.id,
            thread_id=thread_id,
            message_id=message.message_id,
            file_unique_id=image.file_unique_id,
            trigger=trigger,
        )
        await session.commit()
    log.info("enqueued job %d (%s) for chat %d message %d", job.id, trigger, chat.id, message.message_id)
    worker.notify()
    return True
