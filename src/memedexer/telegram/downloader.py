"""Fetches files through the Bot API for the worker."""

import io

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from memedexer.pipeline.worker import DownloadError
from memedexer.redaction import redact

# aiogram's 30 s default is too short for a 20 MB video on a slow link; the worker retries a timeout anyway.
DOWNLOAD_TIMEOUT_SECONDS = 120


class BotDownloader:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def download(self, file_id: str) -> bytes:
        buffer = io.BytesIO()
        try:
            await self._bot.download(file_id, destination=buffer, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except TelegramBadRequest as exc:
            raise DownloadError(self._message("telegram rejected the download", exc), retryable=False) from exc
        except (TelegramAPIError, aiohttp.ClientError, TimeoutError) as exc:
            raise DownloadError(self._message("download failed", exc), retryable=True) from exc
        return buffer.getvalue()

    def _message(self, headline: str, exc: Exception) -> str:
        # File URLs embed the bot token, and aiohttp puts the URL into its HTTP errors.
        return redact(f"{headline}: {exc}", [self._bot.token])
