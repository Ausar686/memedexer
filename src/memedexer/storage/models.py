"""ORM schema; the table contracts are documented in this package's README."""

import datetime as dt
import enum
import typing as t

import sqlalchemy as sa
from sqlalchemy import orm

NO_TOPIC = 0


class ChatStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class JobTrigger(enum.StrEnum):
    MESSAGE = "message"
    EDIT = "edit"
    RECAPTION = "recaption"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    REFUSED = "refused"
    BUDGET_EXCEEDED = "budget_exceeded"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.REFUSED, JobStatus.BUDGET_EXCEEDED}
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UTCDateTime(sa.TypeDecorator[dt.datetime]):
    """Stores naive UTC and returns aware UTC, since SQLite drops tzinfo."""

    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: sa.Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are ambiguous; pass an aware datetime")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value: dt.datetime | None, dialect: sa.Dialect) -> dt.datetime | None:
        return None if value is None else value.replace(tzinfo=dt.UTC)


def _str_enum(cls: type[enum.StrEnum]) -> sa.Enum:
    return sa.Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class Base(orm.DeclarativeBase):
    metadata = sa.MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: t.ClassVar = {
        dt.datetime: UTCDateTime(),
        ChatStatus: _str_enum(ChatStatus),
        JobTrigger: _str_enum(JobTrigger),
        JobStatus: _str_enum(JobStatus),
    }


class Timestamped:
    created_at: orm.Mapped[dt.datetime] = orm.mapped_column(default=utcnow)
    updated_at: orm.Mapped[dt.datetime] = orm.mapped_column(default=utcnow, onupdate=utcnow)


class Chat(Timestamped, Base):
    __tablename__ = "chats"

    id: orm.Mapped[int] = orm.mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
    title: orm.Mapped[str | None]
    is_forum: orm.Mapped[bool] = orm.mapped_column(default=False)
    status: orm.Mapped[ChatStatus] = orm.mapped_column(default=ChatStatus.PENDING)
    added_by_user_id: orm.Mapped[int | None] = orm.mapped_column(sa.BigInteger)
    description_language: orm.Mapped[str | None] = orm.mapped_column(sa.String(16))
    provider: orm.Mapped[str | None] = orm.mapped_column(sa.String(32))
    model: orm.Mapped[str | None] = orm.mapped_column(sa.String(128))


class Topic(Timestamped, Base):
    __tablename__ = "topics"

    chat_id: orm.Mapped[int] = orm.mapped_column(
        sa.BigInteger, sa.ForeignKey("chats.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    thread_id: orm.Mapped[int] = orm.mapped_column(primary_key=True, autoincrement=False)
    captioning_enabled: orm.Mapped[bool] = orm.mapped_column(default=False)
    provider: orm.Mapped[str | None] = orm.mapped_column(sa.String(32))
    model: orm.Mapped[str | None] = orm.mapped_column(sa.String(128))


class Media(Base):
    __tablename__ = "media"

    file_unique_id: orm.Mapped[str] = orm.mapped_column(sa.String(64), primary_key=True)
    file_id: orm.Mapped[str] = orm.mapped_column(sa.String(256))
    mime_type: orm.Mapped[str] = orm.mapped_column(sa.String(64))
    width: orm.Mapped[int | None]
    height: orm.Mapped[int | None]
    file_size: orm.Mapped[int | None]
    created_at: orm.Mapped[dt.datetime] = orm.mapped_column(default=utcnow)


class Caption(Base):
    __tablename__ = "captions"
    __table_args__ = (sa.Index("ix_captions_media_created", "file_unique_id", "created_at"),)

    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    file_unique_id: orm.Mapped[str] = orm.mapped_column(sa.ForeignKey("media.file_unique_id"))
    provider: orm.Mapped[str] = orm.mapped_column(sa.String(32))
    model: orm.Mapped[str] = orm.mapped_column(sa.String(128))
    text: orm.Mapped[str] = orm.mapped_column(sa.Text)
    description: orm.Mapped[str] = orm.mapped_column(sa.Text)
    tags: orm.Mapped[list[str]] = orm.mapped_column(sa.JSON)
    languages: orm.Mapped[list[str]] = orm.mapped_column(sa.JSON)
    kind: orm.Mapped[str] = orm.mapped_column(sa.String(32))
    created_at: orm.Mapped[dt.datetime] = orm.mapped_column(default=utcnow)


class Job(Timestamped, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.Index("ix_jobs_status_available", "status", "available_at", "id"),
        sa.Index("ix_jobs_chat_message", "chat_id", "message_id"),
        sa.Index("ix_jobs_chat_finished", "chat_id", "finished_at"),
    )

    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    chat_id: orm.Mapped[int] = orm.mapped_column(
        sa.BigInteger, sa.ForeignKey("chats.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    thread_id: orm.Mapped[int]
    message_id: orm.Mapped[int]
    file_unique_id: orm.Mapped[str] = orm.mapped_column(sa.ForeignKey("media.file_unique_id"))
    trigger: orm.Mapped[JobTrigger]
    status: orm.Mapped[JobStatus] = orm.mapped_column(default=JobStatus.PENDING)
    attempts: orm.Mapped[int] = orm.mapped_column(default=0)
    error: orm.Mapped[str | None] = orm.mapped_column(sa.Text)
    caption_id: orm.Mapped[int | None] = orm.mapped_column(sa.ForeignKey("captions.id"))
    reply_message_id: orm.Mapped[int | None]
    input_tokens: orm.Mapped[int] = orm.mapped_column(default=0)
    output_tokens: orm.Mapped[int] = orm.mapped_column(default=0)
    cost_microusd: orm.Mapped[int] = orm.mapped_column(sa.BigInteger, default=0)
    available_at: orm.Mapped[dt.datetime] = orm.mapped_column(default=utcnow)
    finished_at: orm.Mapped[dt.datetime | None]
