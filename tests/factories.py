from sqlalchemy.ext.asyncio import AsyncSession

from memedexer.storage import repo
from memedexer.storage.models import NO_TOPIC, ChatStatus, Job, JobTrigger

CHAT_ID = -1001
THREAD_ID = 7


async def seed_chat(
    session: AsyncSession,
    *,
    chat_id: int = CHAT_ID,
    status: ChatStatus = ChatStatus.APPROVED,
    thread_id: int = THREAD_ID,
    enabled: bool = True,
) -> None:
    chat = await repo.upsert_chat(session, chat_id, title="memes", is_forum=thread_id != NO_TOPIC)
    chat.status = status
    topic = await repo.get_or_create_topic(session, chat_id, thread_id)
    topic.captioning_enabled = enabled
    await session.commit()


async def seed_job(
    session: AsyncSession,
    *,
    chat_id: int = CHAT_ID,
    thread_id: int = THREAD_ID,
    message_id: int = 100,
    file_unique_id: str = "uniq",
    mime_type: str = "image/png",
    trigger: JobTrigger = JobTrigger.MESSAGE,
) -> Job:
    await repo.upsert_media(
        session,
        file_unique_id=file_unique_id,
        file_id=f"file-{file_unique_id}",
        mime_type=mime_type,
        width=None,
        height=None,
        file_size=None,
    )
    job = await repo.enqueue_job(
        session,
        chat_id=chat_id,
        thread_id=thread_id,
        message_id=message_id,
        file_unique_id=file_unique_id,
        trigger=trigger,
    )
    await session.commit()
    return job
