import typing as t

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetFile

from memedexer.pipeline.worker import DownloadError
from memedexer.telegram.downloader import DOWNLOAD_TIMEOUT_SECONDS, BotDownloader


class FakeBot:
    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.timeouts: list[int] = []

    async def download(self, file_id: str, destination: t.BinaryIO, timeout: int) -> None:
        self.timeouts.append(timeout)
        if isinstance(self.result, Exception):
            raise self.result
        destination.write(self.result)


async def test_returns_bytes_with_room_for_a_large_video() -> None:
    bot = FakeBot(b"img")

    assert await BotDownloader(bot).download("f") == b"img"
    assert bot.timeouts == [DOWNLOAD_TIMEOUT_SECONDS]


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TelegramBadRequest(GetFile(file_id="f"), "file is too big"), False),
        (TelegramNetworkError(GetFile(file_id="f"), "connection reset"), True),
        (aiohttp.ClientConnectionError("reset"), True),
        (TimeoutError(), True),
    ],
)
async def test_errors_are_classified(error: Exception, retryable: bool) -> None:
    with pytest.raises(DownloadError) as excinfo:
        await BotDownloader(FakeBot(error)).download("f")
    assert excinfo.value.retryable is retryable
