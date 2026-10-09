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
