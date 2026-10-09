import pathlib

import sqlalchemy as sa

from memedexer.storage import db


async def test_sqlite_parent_dir_is_created_and_pragmas_applied(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "nested" / "dir" / "app.db"
    engine = db.create_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.connect() as connection:
        foreign_keys = await connection.scalar(sa.text("PRAGMA foreign_keys"))
        journal_mode = await connection.scalar(sa.text("PRAGMA journal_mode"))
    await engine.dispose()

    assert path.parent.is_dir()
    assert (foreign_keys, journal_mode) == (1, "wal")
