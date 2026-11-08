"""At most one action per key per interval, for notices that anyone can trigger repeatedly."""

import collections.abc as cabc
import time
import typing as t


class Cooldown:
    """In memory, so a restart allows one more; fine for notices, whose worst case is a repeat."""

    def __init__(self, interval_seconds: float, clock: cabc.Callable[[], float] = time.monotonic) -> None:
        self._interval = interval_seconds
        self._clock = clock
        self._last: dict[t.Hashable, float] = {}

    def ready(self, key: t.Hashable) -> bool:
        """True, and starts the interval, unless `key` was ready less than an interval ago."""
        now = self._clock()
        self._last = {k: last for k, last in self._last.items() if now - last < self._interval}
        if key in self._last:
            return False
        self._last[key] = now
        return True
