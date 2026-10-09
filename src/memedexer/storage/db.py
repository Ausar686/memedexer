"""Engine/session construction and in-process schema migrations."""

import pathlib

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext import asyncio as sa_async

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_engine(url: str) -> sa_async.AsyncEngine:
    parsed = sa.make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    if is_sqlite and parsed.database not in (None, "", ":memory:"):
        pathlib.Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = sa_async.create_async_engine(url)
    if is_sqlite:
        sa.event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    return engine


def create_sessionmaker(engine: sa_async.AsyncEngine) -> sa_async.async_sessionmaker[sa_async.AsyncSession]:
    return sa_async.async_sessionmaker(engine, expire_on_commit=False)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


async def upgrade(engine: sa_async.AsyncEngine, revision: str = "head") -> None:
    """Migrate the schema on the given engine; safe to call on every startup."""

    def _run(connection: sa.Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    async with engine.begin() as connection:
        await connection.run_sync(_run)
    return None
