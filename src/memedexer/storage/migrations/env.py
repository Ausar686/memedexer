import asyncio

import sqlalchemy as sa
from alembic import context

from memedexer.config import DatabaseSettings
from memedexer.storage import db
from memedexer.storage.models import Base

config = context.config
target_metadata = Base.metadata


def _configure(**kwargs: object) -> None:
    context.configure(target_metadata=target_metadata, render_as_batch=True, **kwargs)


def run_migrations_offline() -> None:
    _configure(url=DatabaseSettings().database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: sa.Connection) -> None:
    db.suspend_foreign_keys(connection)
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = db.create_engine(DatabaseSettings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
        await connection.run_sync(db.restore_foreign_keys)
    await engine.dispose()


def run_migrations_online() -> None:
    # The app passes its own connection (see storage.db.upgrade) because it already runs an event loop.
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
