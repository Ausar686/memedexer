import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memedexer.app import build_dispatcher
from memedexer.config import Settings
from memedexer.storage import db
from tests.telegram.fake_api import FakeTelegram


class SpyWorker:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return db.create_sessionmaker(engine)


@pytest.fixture
def worker() -> SpyWorker:
    return SpyWorker()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def bot(telegram: FakeTelegram) -> Bot:
    return Bot("42:TEST", session=telegram, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def dispatcher(sessionmaker: async_sessionmaker[AsyncSession], worker: SpyWorker, settings: Settings) -> Dispatcher:
    return build_dispatcher(sessionmaker=sessionmaker, worker=worker, settings=settings)
