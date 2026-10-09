"""Sliding-window send limit, applied per chat."""

import asyncio
import collections
import time
import typing as t

# Telegram allows bots about 20 messages per minute in a group.
MESSAGES_PER_WINDOW = 20
WINDOW_SECONDS = 60.0


class Throttle:
    """Not task-safe on its own: each chat's sends are serialized by its delivery queue."""

    def __init__(
        self,
        limit: int = MESSAGES_PER_WINDOW,
        window: float = WINDOW_SECONDS,
        clock: t.Callable[[], float] = time.monotonic,
        sleep: t.Callable[[float], t.Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._sent: dict[int, collections.deque[float]] = collections.defaultdict(collections.deque)

    async def wait(self, chat_id: int) -> None:
        """Block until a message to `chat_id` fits in the window, then record it."""
        sent = self._sent[chat_id]
        while True:
            now = self._clock()
            while sent and now - sent[0] >= self._window:
                sent.popleft()
            if len(sent) < self._limit:
                sent.append(now)
                return None
            await self._sleep(self._window - (now - sent[0]))
