"""Wires settings, storage, providers, the worker and the Telegram dispatcher together."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from memedexer.captioning.registry import build_providers
from memedexer.config import Settings
from memedexer.pipeline.worker import Worker
from memedexer.storage import db
from memedexer.telegram.downloader import BotDownloader
from memedexer.telegram.handlers import build_router
from memedexer.telegram.membership import build_membership_router

log = logging.getLogger(__name__)


def build_dispatcher(**context: object) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_routers(build_membership_router(), build_router())
    for key, value in context.items():
        dispatcher[key] = value
    return dispatcher


async def run(settings: Settings) -> None:
    engine = db.create_engine(settings.database_url)
    await db.upgrade(engine)
    sessionmaker = db.create_sessionmaker(engine)
    bot = Bot(settings.telegram_bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    worker = Worker(
        sessionmaker=sessionmaker,
        settings=settings,
        providers=build_providers(settings),
        downloader=BotDownloader(bot),
    )
    dispatcher = build_dispatcher(sessionmaker=sessionmaker, worker=worker, settings=settings)

    worker_task = asyncio.create_task(worker.run(), name="worker")
    stop_tasks: set[asyncio.Task] = set()

    def on_worker_exit(task: asyncio.Task) -> None:
        # A dead worker would leave the bot accepting jobs nobody processes; stop polling so `run` re-raises and exits.
        if task.cancelled():
            return
        log.error("worker stopped; shutting down")
        stop = asyncio.create_task(dispatcher.stop_polling())
        stop_tasks.add(stop)
        stop.add_done_callback(stop_tasks.discard)

    worker_task.add_done_callback(on_worker_exit)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        await engine.dispose()
    return None


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(settings))
