import asyncio
import pathlib

import pytest
import sqlalchemy as sa
from aiogram import Dispatcher

from memedexer import app
from memedexer.config import Settings
from memedexer.pipeline.worker import Worker


@pytest.fixture
def app_settings(settings: Settings, tmp_path: pathlib.Path) -> Settings:
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    return settings


async def test_run_migrates_and_shuts_down_cleanly(app_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    worker_started = asyncio.Event()
    polling_kwargs: dict[str, object] = {}

    async def fake_worker_run(self: Worker) -> None:
        worker_started.set()
        await asyncio.Event().wait()

    async def fake_polling(self: Dispatcher, bot: object, **kwargs: object) -> None:
        polling_kwargs.update(kwargs)
        await worker_started.wait()

    monkeypatch.setattr(Worker, "run", fake_worker_run)
    monkeypatch.setattr(Dispatcher, "start_polling", fake_polling)

    await asyncio.wait_for(app.run(app_settings), timeout=5)

    assert {"message", "edited_message"} <= set(polling_kwargs["allowed_updates"])
    engine = sa.create_engine(app_settings.database_url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        assert "jobs" in sa.inspect(connection).get_table_names()
    engine.dispose()


async def test_worker_crash_stops_polling_and_propagates(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped = asyncio.Event()

    async def crashing_worker_run(self: Worker) -> None:
        raise RuntimeError("worker died")

    async def fake_polling(self: Dispatcher, bot: object, **kwargs: object) -> None:
        await stopped.wait()

    async def fake_stop(self: Dispatcher) -> None:
        stopped.set()

    monkeypatch.setattr(Worker, "run", crashing_worker_run)
    monkeypatch.setattr(Dispatcher, "start_polling", fake_polling)
    monkeypatch.setattr(Dispatcher, "stop_polling", fake_stop)

    with pytest.raises(RuntimeError, match="worker died"):
        await asyncio.wait_for(app.run(app_settings), timeout=5)

    assert stopped.is_set()
