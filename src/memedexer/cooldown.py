"""At most one action per key per interval, for notices that anyone can trigger repeatedly."""

import collections
import collections.abc as cabc
import time
import typing as t


class Cooldown:
    """In memory, so a restart allows one more; fine for notices, whose worst case is a repeat."""

    def __init__(
        self,
        interval_seconds: float,
        clock: cabc.Callable[[], float] = time.monotonic,
        *,
        count_suppressed: bool = False,
    ) -> None:
        """With `count_suppressed`, calls that weren't ready are counted until `pop_suppressed` takes the count."""
        self._interval = interval_seconds
        self._clock = clock
        self._count_suppressed = count_suppressed
        self._last: dict[t.Hashable, float] = {}
        self._suppressed: collections.Counter[t.Hashable] = collections.Counter()

    def ready(self, key: t.Hashable) -> bool:
        """True, and starts the interval, unless `key` was ready less than an interval ago."""
        now = self._clock()
        self._last = {k: last for k, last in self._last.items() if now - last < self._interval}
        if key in self._last:
            if self._count_suppressed:
                self._suppressed[key] += 1
            return False
        self._last[key] = now
        return True

    def pop_suppressed(self, key: t.Hashable) -> int:
        """How often `key` wasn't ready since this was last asked."""
        return self._suppressed.pop(key, 0)
