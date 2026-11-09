"""The decoder's own logic, run in-process; `test_video` covers it through the real child process."""

import io
import pathlib
import resource
import subprocess
import sys

import PIL.Image
import pytest

from memedexer.pipeline import frames
from memedexer.pipeline.video import read_frames
from tests import videos

SCENES = ["red", "green", "blue", "yellow", "white"] * 2


def corner(picture: PIL.Image.Image) -> str:
    """The scene colour name nearest to the picture's corner pixel."""
    palette = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
               "white": (255, 255, 255)}
    pixel = picture.getpixel((5, 5))
    return min(palette, key=lambda name: sum(abs(a - b) for a, b in zip(pixel, palette[name], strict=True)))


def test_seekable_video_samples_by_position() -> None:
    assert [corner(p) for p in frames.sample_frames(videos.clip(SCENES))] == ["green", "red", "white"]


def test_unseekable_stream_uses_telegram_duration_and_the_frame_rate() -> None:
    raw = videos.clip(SCENES, container="h264")

    assert [corner(p) for p in frames.sample_frames(raw, duration_hint=10.0)] == ["green", "red", "white"]


def test_stream_without_any_duration_gives_its_first_frame() -> None:
    raw = videos.clip(SCENES, container="h264")

    assert [corner(p) for p in frames.sample_frames(raw)] == ["red"]


def test_targets_past_the_end_take_the_last_frame() -> None:
    raw = videos.clip(["red", "green"], container="h264")

    assert [corner(p) for p in frames.sample_frames(raw, duration_hint=60.0)] == ["green"]


def test_main_limits_resources_and_writes_framed_jpegs(monkeypatch: pytest.MonkeyPatch) -> None:
    limits: dict[int, tuple[int, int]] = {}
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: limits.__setitem__(kind, value))
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "argv", ["frames", "--duration", "10"])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(videos.clip(SCENES, container="h264"))))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout))

    frames.main()
    sys.stdout.flush()

    assert limits[resource.RLIMIT_CPU] == (frames.CPU_LIMIT_SECONDS, frames.CPU_LIMIT_SECONDS + 1)
    assert limits[resource.RLIMIT_AS] == (frames.MEMORY_LIMIT_BYTES, frames.MEMORY_LIMIT_BYTES)
    assert limits[resource.RLIMIT_FSIZE] == (0, 0)
    assert limits[resource.RLIMIT_NOFILE] == (frames.OPEN_FILES_LIMIT, frames.OPEN_FILES_LIMIT)
    decoded = [PIL.Image.open(io.BytesIO(frame)) for frame in read_frames(stdout.getvalue())]
    assert [(picture.format, corner(picture)) for picture in decoded] == [
        ("JPEG", "green"),
        ("JPEG", "red"),
        ("JPEG", "white"),
    ]


def test_memory_limit_is_skipped_where_the_os_refuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[int] = []

    def setrlimit(kind: int, value: tuple[int, int]) -> None:
        if kind == resource.RLIMIT_AS:
            raise ValueError("current limit exceeds maximum limit")
        applied.append(kind)

    monkeypatch.setattr(resource, "setrlimit", setrlimit)

    frames._limit_resources()

    assert applied == [resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE]


@pytest.mark.parametrize("container", ["mp4", "mov", "matroska", "avi", "mpegts", "mpeg", "flv", "asf", "h264"])
def test_common_containers_are_allowed(container: str) -> None:
    with frames._open(videos.clip(["red"], container=container)) as opened:
        assert opened.streams.video


def test_other_containers_are_refused() -> None:
    with pytest.raises(ValueError, match="unsupported container format"):
        frames.sample_frames(videos.clip(["red"], container="nut"))


def test_limited_child_cannot_write_files(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "db"
    target.write_bytes(b"")
    script = (
        "from memedexer.pipeline.frames import _limit_resources; _limit_resources(); "
        f"open({str(target)!r}, 'ab', buffering=0).write(b'x')"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True)

    assert b"File too large" in result.stderr
    assert target.read_bytes() == b""
