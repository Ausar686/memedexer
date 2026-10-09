"""Fetches files through the Bot API for the worker."""

import io

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from memedexer.pipeline.worker import DownloadError


class BotDownloader:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def download(self, file_id: str) -> bytes:
        buffer = io.BytesIO()
        try:
            await self._bot.download(file_id, destination=buffer)
        except TelegramBadRequest as exc:
            raise DownloadError(f"telegram rejected the download: {exc}", retryable=False) from exc
        except (TelegramAPIError, aiohttp.ClientError, TimeoutError) as exc:
            raise DownloadError(f"download failed: {exc}", retryable=True) from exc
        return buffer.getvalue()
