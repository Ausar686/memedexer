from memedexer.delivery.throttle import Throttle


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_allows_burst_up_to_limit_then_waits_for_window() -> None:
    clock = FakeClock()
    throttle = Throttle(limit=3, window=60, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await throttle.wait(-1)
        clock.now += 1
    assert clock.sleeps == []

    await throttle.wait(-1)
    assert clock.sleeps == [57.0]


async def test_chats_are_independent() -> None:
    clock = FakeClock()
    throttle = Throttle(limit=1, window=60, clock=clock, sleep=clock.sleep)

    await throttle.wait(-1)
    await throttle.wait(-2)

    assert clock.sleeps == []


async def test_old_sends_expire() -> None:
    clock = FakeClock()
    throttle = Throttle(limit=1, window=60, clock=clock, sleep=clock.sleep)

    await throttle.wait(-1)
    clock.now += 60
    await throttle.wait(-1)

    assert clock.sleeps == []
