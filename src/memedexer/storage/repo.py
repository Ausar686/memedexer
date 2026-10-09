"""Query helpers over the ORM schema; callers own the session and commit."""

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

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


async def get_chat(session: AsyncSession, chat_id: int) -> Chat | None:
    return await session.get(Chat, chat_id)


async def upsert_chat(
    session: AsyncSession, chat_id: int, *, title: str | None, is_forum: bool, added_by_user_id: int | None = None
) -> Chat:
    """Refresh title/forum flag; a new chat starts PENDING and keeps its original `added_by_user_id`."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        chat = Chat(id=chat_id, added_by_user_id=added_by_user_id, status=ChatStatus.PENDING)
        session.add(chat)
    chat.title = title
    chat.is_forum = is_forum
    await session.flush()
    return chat


async def migrate_chat(session: AsyncSession, old_id: int, new_id: int) -> bool:
    """Move a group's row to its new supergroup id; topics and jobs follow via ON UPDATE CASCADE.

    A row already created for the new id (the bot may see the supergroup first) is discarded in favor of the old one.
    """
    if await session.get(Chat, old_id) is None:
        return False
    duplicate = await session.get(Chat, new_id)
    if duplicate is not None:
        await session.delete(duplicate)
        await session.flush()
    await session.execute(
        sa.update(Chat).where(Chat.id == old_id).values(id=new_id).execution_options(synchronize_session=False)
    )
    session.expunge_all()
    return True


async def list_chats(session: AsyncSession, status: ChatStatus | None = None) -> list[Chat]:
    query = sa.select(Chat).order_by(Chat.created_at)
    if status is not None:
        query = query.where(Chat.status == status)
    return list(await session.scalars(query))


async def get_or_create_topic(session: AsyncSession, chat_id: int, thread_id: int) -> Topic:
    topic = await session.get(Topic, (chat_id, thread_id))
    if topic is None:
        topic = Topic(chat_id=chat_id, thread_id=thread_id)
        session.add(topic)
        await session.flush()
    return topic


async def upsert_media(
    session: AsyncSession,
    *,
    file_unique_id: str,
    file_id: str,
    mime_type: str,
    width: int | None,
    height: int | None,
    file_size: int | None,
    photo_file_id: str | None = None,
) -> Media:
    """`file_id` rotates over time for the same file, so the latest one wins; a known `photo_file_id` is kept."""
    media = await session.get(Media, file_unique_id)
    if media is None:
        media = Media(file_unique_id=file_unique_id)
        session.add(media)
    media.file_id = file_id
    media.mime_type = mime_type
    media.width = width
    media.height = height
    media.file_size = file_size
    if photo_file_id is not None:
        media.photo_file_id = photo_file_id
    await session.flush()
    return media


async def latest_caption(session: AsyncSession, file_unique_id: str) -> Caption | None:
    query = (
        sa.select(Caption)
        .where(Caption.file_unique_id == file_unique_id)
        .order_by(Caption.created_at.desc(), Caption.id.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def enqueue_job(
    session: AsyncSession, *, chat_id: int, thread_id: int, message_id: int, file_unique_id: str, trigger: JobTrigger
) -> Job:
    job = Job(
        chat_id=chat_id, thread_id=thread_id, message_id=message_id, file_unique_id=file_unique_id, trigger=trigger
    )
    session.add(job)
    await session.flush()
    return job


async def claim_next_job(session: AsyncSession, now: dt.datetime | None = None) -> Job | None:
    """Atomically move the oldest due PENDING job to RUNNING and count the attempt."""
    now = now or utcnow()
    oldest = (
        sa.select(Job.id)
        .where(Job.status == JobStatus.PENDING, Job.available_at <= now)
        .order_by(Job.id)
        .limit(1)
        .scalar_subquery()
    )
    query = (
        sa.update(Job)
        .where(Job.id == oldest, Job.status == JobStatus.PENDING)
        .values(status=JobStatus.RUNNING, attempts=Job.attempts + 1, updated_at=now)
        .returning(Job)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    return await session.scalar(query)


async def requeue_interrupted_jobs(session: AsyncSession) -> int:
    """Return RUNNING jobs left by a crashed process to PENDING; call once at startup."""
    result = await session.execute(
        sa.update(Job).where(Job.status == JobStatus.RUNNING).values(status=JobStatus.PENDING, updated_at=utcnow())
    )
    return result.rowcount


def retry_job(job: Job, *, error: str, delay: dt.timedelta) -> Job:
    """Put a RUNNING job back in the queue, not to be claimed before `delay` passes."""
    job.status = JobStatus.PENDING
    job.error = error
    job.available_at = utcnow() + delay
    return job


def finish_job(job: Job, status: JobStatus, *, error: str | None = None) -> Job:
    if status not in TERMINAL_JOB_STATUSES:
        raise ValueError(f"{status!s} is not a terminal job status")
    job.status = status
    job.error = error
    job.finished_at = utcnow()
    return job


async def latest_job_for_message(session: AsyncSession, chat_id: int, message_id: int) -> Job | None:
    query = (
        sa.select(Job)
        .where(Job.chat_id == chat_id, Job.message_id == message_id)
        .order_by(Job.id.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def undelivered_jobs(session: AsyncSession, since: dt.datetime) -> list[Job]:
    """Captioned jobs whose reply was neither posted nor given up on, oldest first."""
    query = (
        sa.select(Job)
        .where(
            Job.status == JobStatus.DONE,
            Job.reply_message_id.is_(None),
            Job.delivery_error.is_(None),
            Job.finished_at >= since,
        )
        .order_by(Job.id)
    )
    return list(await session.scalars(query))


async def earlier_budget_exceeded(session: AsyncSession, job: Job, since: dt.datetime) -> int:
    """Over-budget jobs of the same chat in the period with a lower id.

    Comparing ids (not "any other job") makes exactly one job, the first, alert even when several finish at once.
    """
    query = sa.select(sa.func.count()).where(
        Job.chat_id == job.chat_id,
        Job.status == JobStatus.BUDGET_EXCEEDED,
        Job.finished_at >= since,
        Job.id < job.id,
    )
    return int(await session.scalar(query))


async def spent_microusd(session: AsyncSession, chat_id: int, since: dt.datetime) -> int:
    query = sa.select(sa.func.coalesce(sa.func.sum(Job.cost_microusd), 0)).where(
        Job.chat_id == chat_id, Job.finished_at >= since
    )
    return int(await session.scalar(query))
