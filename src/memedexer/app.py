"""Wires settings, storage, providers, the worker and the Telegram dispatcher together."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from memedexer.captioning.registry import build_providers
from memedexer.config import Settings
from memedexer.delivery.service import Delivery
from memedexer.pipeline.worker import Worker
from memedexer.storage import db
from memedexer.telegram.admin import build_admin_router
from memedexer.telegram.downloader import BotDownloader
from memedexer.telegram.handlers import build_router
from memedexer.telegram.membership import build_membership_router
from memedexer.telegram.setup import prepare_bot

log = logging.getLogger(__name__)

# Together they stay within compose's 30 s stop_grace_period, after which Docker kills the process.
WORKER_STOP_TIMEOUT_SECONDS = 15.0
DELIVERY_CLOSE_TIMEOUT_SECONDS = 10.0


def build_dispatcher(**context: object) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_routers(build_membership_router(), build_admin_router(), build_router())
    for key, value in context.items():
        dispatcher[key] = value
    return dispatcher


async def run(settings: Settings) -> None:
    engine = db.create_engine(settings.database_url.get_secret_value())
    await db.upgrade(engine)
    sessionmaker = db.create_sessionmaker(engine)
    bot = Bot(settings.telegram_bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    downloader = BotDownloader(bot)
    delivery = Delivery(bot=bot, sessionmaker=sessionmaker, settings=settings, downloader=downloader)
    worker = Worker(
        sessionmaker=sessionmaker,
        settings=settings,
        providers=build_providers(settings),
        downloader=downloader,
        listener=delivery,
    )
    dispatcher = build_dispatcher(sessionmaker=sessionmaker, worker=worker, settings=settings)
    await prepare_bot(bot, settings)
    await delivery.start()

    worker_task = asyncio.create_task(worker.run(), name="worker")
    stop_tasks: set[asyncio.Task] = set()
    shutting_down = False

    def on_worker_exit(task: asyncio.Task) -> None:
        # A dead worker would leave the bot accepting jobs nobody processes; stop polling so `run` re-raises and exits.
        if shutting_down or task.cancelled():
            return
        log.error("worker stopped; shutting down")
        stop = asyncio.create_task(dispatcher.stop_polling())
        stop_tasks.add(stop)
        stop.add_done_callback(stop_tasks.discard)

    worker_task.add_done_callback(on_worker_exit)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        shutting_down = True
        worker.stop()
        try:
            await finish(worker_task, WORKER_STOP_TIMEOUT_SECONDS)
        finally:
            await delivery.close(DELIVERY_CLOSE_TIMEOUT_SECONDS)
            await engine.dispose()
    return None


async def finish(task: asyncio.Task, timeout: float) -> None:
    """Wait for a task that was asked to stop, cancelling it only if it overruns; re-raises its own failure."""
    await asyncio.wait({task}, timeout=timeout)
    if not task.done():
        log.warning("%s didn't stop within %.0f s; cancelling it", task.get_name(), timeout)
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return None


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(settings))
