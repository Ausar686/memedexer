import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine

from memedexer.storage import db
from memedexer.storage.models import Base


def _schema_diff(connection: sa.Connection) -> list:
    return compare_metadata(MigrationContext.configure(connection), Base.metadata)


def _tables(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


async def test_migrations_match_models(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.run_sync(_schema_diff) == []


async def test_upgrade_is_idempotent(engine: AsyncEngine) -> None:
    await db.upgrade(engine)
    async with engine.connect() as connection:
        assert await connection.run_sync(_schema_diff) == []


async def test_downgrade_to_base_and_back(engine: AsyncEngine) -> None:
    def downgrade(connection: sa.Connection) -> None:
        config = db.alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")

    async with engine.begin() as connection:
        await connection.run_sync(downgrade)
    async with engine.connect() as connection:
        assert await connection.run_sync(_tables) == {"alembic_version"}

    await db.upgrade(engine)
    async with engine.connect() as connection:
        assert set(Base.metadata.tables) <= await connection.run_sync(_tables)


async def test_0002_backfills_existing_rows(database_url: str) -> None:
    engine = db.create_engine(database_url)
    await db.upgrade(engine, "0001")
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO chats (id, is_forum, status, created_at, updated_at) "
                "VALUES (-1, 0, 'approved', '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO media (file_unique_id, file_id, width, height, created_at) "
                "VALUES ('u', 'f', 1, 1, '2026-10-01 00:00:00')"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO jobs (chat_id, thread_id, message_id, file_unique_id, trigger, status, attempts, "
                "input_tokens, output_tokens, cost_microusd, created_at, updated_at) "
                "VALUES (-1, 0, 1, 'u', 'message', 'pending', 0, 0, 0, 0, '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
            )
        )

    await db.upgrade(engine)

    async with engine.connect() as connection:
        available_at = await connection.scalar(sa.text("SELECT available_at FROM jobs"))
        mime_type = await connection.scalar(sa.text("SELECT mime_type FROM media"))
    await engine.dispose()
    assert (str(available_at), mime_type) == ("2026-10-01 00:00:00", "image/jpeg")


async def test_0004_backfills_photo_file_ids(database_url: str) -> None:
    engine = db.create_engine(database_url)
    await db.upgrade(engine, "0003")
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO media (file_unique_id, file_id, mime_type, width, height, created_at) VALUES "
                "('photo', 'photo-file', 'image/jpeg', 1280, 960, '2026-10-01 00:00:00'), "
                "('doc', 'doc-file', 'image/png', NULL, NULL, '2026-10-01 00:00:00')"
            )
        )

    await db.upgrade(engine)

    async with engine.connect() as connection:
        rows = dict((await connection.execute(sa.text("SELECT file_unique_id, photo_file_id FROM media"))).all())
    await engine.dispose()
    assert rows == {"photo": "photo-file", "doc": None}
