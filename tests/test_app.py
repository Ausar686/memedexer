import asyncio
import pathlib

import pydantic
import pytest
import sqlalchemy as sa
from aiogram import Dispatcher

from memedexer import app
from memedexer.config import Settings
from memedexer.pipeline.worker import Worker


@pytest.fixture(autouse=True)
def no_bot_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(*args: object) -> None:
        return None

    monkeypatch.setattr(app, "prepare_bot", skip)


@pytest.fixture
def app_settings(settings: Settings, tmp_path: pathlib.Path) -> Settings:
    settings.database_url = pydantic.SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    return settings


async def test_run_migrates_and_stops_the_worker_gracefully(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    polling_kwargs: dict[str, object] = {}

    async def fake_polling(self: Dispatcher, bot: object, **kwargs: object) -> None:
        polling_kwargs.update(kwargs)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(Dispatcher, "start_polling", fake_polling)

    await asyncio.wait_for(app.run(app_settings), timeout=5)

    assert {"message", "edited_message"} <= set(polling_kwargs["allowed_updates"])
    assert "didn't stop" not in caplog.text
    assert "worker stopped; shutting down" not in caplog.text
    engine = sa.create_engine(app_settings.database_url.get_secret_value().replace("+aiosqlite", ""))
    with engine.connect() as connection:
        assert "jobs" in sa.inspect(connection).get_table_names()
    engine.dispose()


async def test_worker_ignoring_stop_is_cancelled_at_the_deadline(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cancelled = asyncio.Event()

    async def stubborn_worker_run(self: Worker) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fake_polling(self: Dispatcher, bot: object, **kwargs: object) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(Worker, "run", stubborn_worker_run)
    monkeypatch.setattr(Dispatcher, "start_polling", fake_polling)
    monkeypatch.setattr(app, "WORKER_STOP_TIMEOUT_SECONDS", 0.05)

    await asyncio.wait_for(app.run(app_settings), timeout=5)

    assert cancelled.is_set()
    assert "worker didn't stop within" in caplog.text


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
