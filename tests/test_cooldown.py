from memedexer.cooldown import Cooldown


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_one_per_key_per_interval() -> None:
    clock = Clock()
    cooldown = Cooldown(60, clock)

    assert [cooldown.ready("a"), cooldown.ready("a"), cooldown.ready("b")] == [True, False, True]
    clock.now = 59.9
    assert cooldown.ready("a") is False
    clock.now = 60.0
    assert cooldown.ready("a") is True


def test_expired_keys_are_forgotten() -> None:
    clock = Clock()
    cooldown = Cooldown(60, clock)
    for key in range(1000):
        cooldown.ready(key)

    clock.now = 61
    cooldown.ready("new")

    assert list(cooldown._last) == ["new"]
