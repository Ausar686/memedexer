import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from memedexer.storage import repo
from memedexer.storage.models import NO_TOPIC, Caption, ChatStatus, Job, JobStatus, JobTrigger

T0 = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)


async def _chat(session: AsyncSession, chat_id: int = -100) -> None:
    await repo.upsert_chat(session, chat_id, title="memes", is_forum=True, added_by_user_id=7)


async def _media(session: AsyncSession, file_unique_id: str = "u1") -> None:
    await repo.upsert_media(session, file_unique_id=file_unique_id, file_id="f1", width=10, height=20, file_size=None)


async def _job(session: AsyncSession, chat_id: int = -100, message_id: int = 1, file_unique_id: str = "u1") -> Job:
    return await repo.enqueue_job(
        session,
        chat_id=chat_id,
        thread_id=NO_TOPIC,
        message_id=message_id,
        file_unique_id=file_unique_id,
        trigger=JobTrigger.MESSAGE,
    )


def _caption(created_at: dt.datetime, text: str) -> Caption:
    return Caption(
        file_unique_id="u1",
        provider="anthropic",
        model="claude-haiku-5-5",
        text=text,
        description="d",
        tags=["#a"],
        languages=["en"],
        kind="meme",
        created_at=created_at,
    )


async def test_new_chat_is_pending_and_keeps_adder(session: AsyncSession) -> None:
    chat = await repo.upsert_chat(session, -100, title="a", is_forum=False, added_by_user_id=7)
    assert chat.status is ChatStatus.PENDING

    chat.status = ChatStatus.APPROVED
    chat = await repo.upsert_chat(session, -100, title="b", is_forum=True, added_by_user_id=8)
    assert (chat.title, chat.is_forum, chat.added_by_user_id, chat.status) == ("b", True, 7, ChatStatus.APPROVED)


async def test_list_chats_filters_by_status(session: AsyncSession) -> None:
    await _chat(session, -1)
    approved = await repo.upsert_chat(session, -2, title=None, is_forum=False)
    approved.status = ChatStatus.APPROVED
    await session.flush()

    assert [c.id for c in await repo.list_chats(session, ChatStatus.APPROVED)] == [-2]
    assert {c.id for c in await repo.list_chats(session)} == {-1, -2}


async def test_topic_defaults_to_disabled_and_is_reused(session: AsyncSession) -> None:
    await _chat(session)
    topic = await repo.get_or_create_topic(session, -100, 5)
    assert topic.captioning_enabled is False
    assert (topic.provider, topic.model) == (None, None)
    assert await repo.get_or_create_topic(session, -100, 5) is topic


async def test_upsert_media_refreshes_file_id(session: AsyncSession) -> None:
    await _media(session)
    media = await repo.upsert_media(session, file_unique_id="u1", file_id="f2", width=10, height=20, file_size=3)
    assert (media.file_id, media.file_size) == ("f2", 3)


async def test_latest_caption_is_newest(session: AsyncSession) -> None:
    await _media(session)
    session.add_all([_caption(T0, "old"), _caption(T0 + dt.timedelta(minutes=1), "new")])
    await session.flush()

    caption = await repo.latest_caption(session, "u1")
    assert caption is not None and caption.text == "new"
    assert caption.created_at.tzinfo is dt.UTC
    assert await repo.latest_caption(session, "missing") is None


async def test_claim_next_job_is_fifo_and_counts_attempts(session: AsyncSession) -> None:
    await _chat(session)
    await _media(session)
    first, second = await _job(session, message_id=1), await _job(session, message_id=2)

    claimed = await repo.claim_next_job(session)
    assert claimed is not None and claimed.id == first.id
    assert (claimed.status, claimed.attempts) == (JobStatus.RUNNING, 1)

    claimed = await repo.claim_next_job(session)
    assert claimed is not None and claimed.id == second.id
    assert await repo.claim_next_job(session) is None


async def test_requeue_interrupted_jobs(session: AsyncSession) -> None:
    await _chat(session)
    await _media(session)
    job = await _job(session)
    await repo.claim_next_job(session)

    assert await repo.requeue_interrupted_jobs(session) == 1
    claimed = await repo.claim_next_job(session)
    assert claimed is not None and claimed.id == job.id and claimed.attempts == 2


async def test_finish_job_rejects_non_terminal_status(session: AsyncSession) -> None:
    await _chat(session)
    await _media(session)
    job = await _job(session)

    with pytest.raises(ValueError, match="running is not a terminal"):
        repo.finish_job(job, JobStatus.RUNNING)

    repo.finish_job(job, JobStatus.FAILED, error="boom")
    assert (job.status, job.error) == (JobStatus.FAILED, "boom")
    assert job.finished_at is not None and job.finished_at.tzinfo is dt.UTC


async def test_latest_job_for_message(session: AsyncSession) -> None:
    await _chat(session)
    await _media(session)
    await _job(session, message_id=1)
    newest = await _job(session, message_id=1)
    await _job(session, message_id=2)

    job = await repo.latest_job_for_message(session, -100, 1)
    assert job is not None and job.id == newest.id
    assert await repo.latest_job_for_message(session, -100, 3) is None


async def test_spent_counts_only_chat_and_period(session: AsyncSession) -> None:
    await _chat(session, -1)
    await _chat(session, -2)
    await _media(session)
    for chat_id, cost, finished_at in [
        (-1, 100, T0),
        (-1, 20, T0 + dt.timedelta(hours=1)),
        (-1, 5_000, T0 - dt.timedelta(seconds=1)),
        (-2, 7, T0),
    ]:
        job = await _job(session, chat_id=chat_id)
        job.cost_microusd, job.finished_at = cost, finished_at
    await _job(session, chat_id=-1)
    await session.flush()

    assert await repo.spent_microusd(session, -1, T0) == 120
    assert await repo.spent_microusd(session, -3, T0) == 0


async def test_foreign_keys_are_enforced(session: AsyncSession) -> None:
    await _media(session)
    with pytest.raises(sa.exc.IntegrityError):
        await _job(session, chat_id=-999)


async def test_naive_datetimes_are_rejected(session: AsyncSession) -> None:
    await _media(session)
    session.add(_caption(T0.replace(tzinfo=None), "x"))
    with pytest.raises(sa.exc.StatementError, match="naive datetimes"):
        await session.flush()
