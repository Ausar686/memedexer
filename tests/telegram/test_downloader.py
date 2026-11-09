import typing as t

import aiohttp
import pytest
import yarl
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetFile

from memedexer.pipeline.worker import DownloadError
from memedexer.telegram.downloader import DOWNLOAD_TIMEOUT_SECONDS, BotDownloader

TOKEN = "123:SECRET"
FILE_URL = f"https://api.telegram.org/file/bot{TOKEN}/videos/file_1.mp4"


class FakeBot:
    token = TOKEN

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


@pytest.mark.parametrize(
    ("message", "too_big"),
    [("Bad Request: file is too big", True), ("Bad Request: File is too big", True), ("wrong file_id", False)],
)
async def test_files_over_the_limit_are_marked(message: str, too_big: bool) -> None:
    with pytest.raises(DownloadError) as excinfo:
        await BotDownloader(FakeBot(TelegramBadRequest(GetFile(file_id="f"), message))).download("f")
    assert excinfo.value.too_big is too_big


async def test_http_errors_do_not_leak_the_token_in_the_file_url() -> None:
    url = yarl.URL(FILE_URL)
    error = aiohttp.ClientResponseError(
        aiohttp.RequestInfo(url, "GET", {}, url), (), status=502, message="Bad Gateway"
    )

    with pytest.raises(DownloadError) as excinfo:
        await BotDownloader(FakeBot(error)).download("f")

    assert str(excinfo.value) == (
        "download failed: 502, message='Bad Gateway', url='https://api.telegram.org/file/bot<redacted>/videos/file_1.mp4'"
    )
