"""Video sampling, run in-process; `test_sandbox` covers it through the real child process."""

import PIL.Image
import pytest

from memedexer.pipeline import frames
from tests import videos

SCENES = ["red", "green", "blue", "yellow", "white"] * 2


def corner(picture: PIL.Image.Image) -> str:
    """The scene colour name nearest to the picture's corner pixel."""
    palette = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
               "white": (255, 255, 255)}
    pixel = picture.getpixel((5, 5))
    return min(palette, key=lambda name: sum(abs(a - b) for a, b in zip(pixel, palette[name], strict=True)))


def test_seekable_video_samples_by_position(monkeypatch: pytest.MonkeyPatch) -> None:
    # Seeking skips decoding the whole video; the forward scan is only a fallback.
    monkeypatch.setattr(frames, "_scan", None)

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


@pytest.mark.parametrize("container", ["mp4", "mov", "matroska", "avi", "mpegts", "mpeg", "flv", "asf", "h264"])
def test_common_containers_are_allowed(container: str) -> None:
    with frames._open(videos.clip(["red"], container=container)) as opened:
        assert opened.streams.video


def test_ffmpeg_errors_become_value_errors_with_a_readable_reason() -> None:
    with pytest.raises(ValueError, match="^Invalid data found when processing input$"):
        frames.sample_frames(b"not a video")


def test_other_containers_are_refused() -> None:
    with pytest.raises(ValueError, match="unsupported container format"):
        frames.sample_frames(videos.clip(["red"], container="nut"))


@pytest.mark.parametrize(("container", "codec"), [("mpegts", "h264"), ("mpeg", "mpeg2video")])
def test_positions_count_from_the_first_timestamp(container: str, codec: str) -> None:
    # MPEG streams start at a non-zero timestamp and seek imprecisely; targets that ignored the start landed early,
    # or before the first frame.
    data = videos.clip(SCENES, container=container, codec=codec)
    with frames._open(data) as opened:
        assert opened.streams.video[0].start_time > 0

    assert [corner(p) for p in frames.sample_frames(data)] == [
        "green",
        "red",
        "white",
    ]


def test_a_one_second_mpeg_ts_clip_has_a_frame() -> None:
    assert [corner(p) for p in frames.sample_frames(videos.clip(["red"], container="mpegts"))] == ["red"]
