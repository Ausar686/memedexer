import asyncio
import io
import struct
import sys

import PIL.Image
import pytest

from memedexer.captioning.base import Image
from memedexer.pipeline import video
from memedexer.pipeline.frames import distinct
from memedexer.pipeline.images import MAX_BYTES, MAX_SIDE
from memedexer.pipeline.video import VideoError, extract_frames, read_frames
from tests import videos

RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 128, 0), (0, 0, 255), (255, 255, 255)


def pictures(frames: list[Image]) -> list[PIL.Image.Image]:
    assert all(frame.media_type == "image/jpeg" for frame in frames)
    return [PIL.Image.open(io.BytesIO(frame.data)) for frame in frames]


def close_to(pixel: tuple[int, ...], color: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= 12 for a, b in zip(pixel, color, strict=True))


@pytest.fixture
def command(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Swaps the decoder for a stand-in script and records the processes started."""
    started: list[asyncio.subprocess.Process] = []
    original = asyncio.create_subprocess_exec

    async def spawn(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    def use(script: str) -> list[asyncio.subprocess.Process]:
        monkeypatch.setattr(video, "_COMMAND", (sys.executable, "-c", script))
        return started

    return use


async def test_samples_frames_at_ten_fifty_and_ninety_percent() -> None:
    # Ten one-second scenes: 1.0 s is green, 5.0 s red, 9.0 s white.
    data = videos.clip(["red", "green", "blue", "yellow", "white"] * 2)

    frames = pictures(await extract_frames(data))

    assert [close_to(p.getpixel((5, 5)), c) for p, c in zip(frames, (GREEN, RED, WHITE), strict=True)] == [True] * 3


async def test_a_still_picture_with_sound_costs_one_frame() -> None:
    assert len(await extract_frames(videos.clip(["red"] * 10))) == 1


async def test_rotated_phone_video_comes_out_upright() -> None:
    # Stored 320×160 with the left half red, and a 90° counter-clockwise display rotation.
    [frame] = pictures(await extract_frames(videos.ROTATED))

    assert frame.size == (160, 320)
    assert close_to(frame.getpixel((80, 5)), BLUE)
    assert close_to(frame.getpixel((80, 314)), RED)


async def test_large_frames_are_scaled_to_the_model_limit() -> None:
    [frame] = pictures(await extract_frames(videos.clip(["red"], size=(2400, 1200))))

    assert frame.size == (MAX_SIDE, MAX_SIDE // 2)


async def test_real_gifs_are_sampled() -> None:
    frames = pictures(await extract_frames(videos.gif(["red", "green", "blue", "white", "red"] * 2)))

    assert 1 < len(frames) <= len(video.FRAME_POSITIONS)


async def test_text_survives_into_the_frames() -> None:
    frames = pictures(await extract_frames(videos.clip(["navy", "maroon", "olive"], text="WHEN THE CODE WORKS")))

    assert len(frames) == 3
    for frame in frames:
        assert any(close_to(color, WHITE) for _, color in frame.getcolors(frame.width * frame.height))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a video", "cannot decode video: av.error.InvalidDataError"),
        (b"", "cannot decode video: av.error.InvalidDataError"),
        (videos.audio_only(), "cannot decode video: ValueError: no video stream"),
        (videos.clip(["red"], container="nut"), "cannot decode video: ValueError: unsupported container format"),
    ],
    ids=["garbage", "empty", "audio-only", "unsupported-container"],
)
async def test_undecodable_input_is_a_video_error(data: bytes, message: str) -> None:
    with pytest.raises(VideoError, match=message):
        await extract_frames(data)


async def test_a_hanging_decoder_is_killed(command, monkeypatch: pytest.MonkeyPatch) -> None:
    started = command("import time; time.sleep(60)")
    monkeypatch.setattr(video, "DECODE_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(VideoError, match="took longer than 0 s"):
        await extract_frames(b"video")

    assert started[0].returncode is not None


async def test_a_crashing_decoder_fails_only_the_job(command) -> None:
    command("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")

    with pytest.raises(VideoError, match="killed by SIGSEGV"):
        await extract_frames(b"video")


async def test_the_decoder_gets_no_secrets(command, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:SECRET")
    command("import os, sys; sys.stderr.write(' '.join(sorted(os.environ)) or 'empty'); sys.exit(3)")

    with pytest.raises(VideoError) as error:
        await extract_frames(b"video")

    assert "TELEGRAM_BOT_TOKEN" not in str(error.value)


async def test_cancelling_the_job_kills_the_decoder(command) -> None:
    started = command("import time; time.sleep(60)")
    task = asyncio.create_task(extract_frames(b"video"))
    while not started:
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started[0].returncode is not None


async def test_duration_hint_is_passed_to_the_decoder(command) -> None:
    command("import sys; sys.stderr.write(' '.join(sys.argv[1:])); sys.exit(1)")

    with pytest.raises(VideoError, match="--duration 12.0"):
        await extract_frames(b"video", 12.0)


async def test_a_flooding_decoder_is_stopped(command) -> None:
    started = command("import sys\nwhile True: sys.stdout.buffer.write(bytes(1 << 20))")

    with pytest.raises(VideoError, match="output is too large"):
        await extract_frames(b"video")

    assert started[0].returncode is not None


async def test_a_decoder_that_floods_then_hangs_is_stopped(command) -> None:
    started = command("import sys, time; sys.stdout.buffer.write(bytes(1 << 20)); sys.stdout.flush(); time.sleep(60)")

    with pytest.raises(VideoError, match="took longer"):
        await extract_frames(b"video")

    assert started[0].returncode is not None


async def test_processes_the_decoder_starts_are_killed_with_it(command, monkeypatch: pytest.MonkeyPatch) -> None:
    # The grandchild inherits the pipes; if it survived, reading them would never end.
    command("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)")
    monkeypatch.setattr(video, "DECODE_TIMEOUT_SECONDS", 0.5)

    with pytest.raises(VideoError, match="took longer"):
        await asyncio.wait_for(extract_frames(b"video"), 5)


async def test_only_the_tail_of_a_long_error_is_kept(command) -> None:
    command("import sys; sys.stderr.write('noise\\n' * 100_000 + 'the real error'); sys.exit(1)")

    with pytest.raises(VideoError, match="cannot decode video: the real error$"):
        await extract_frames(b"video")


async def test_a_decoder_that_ignores_its_input_is_not_an_error_by_itself(command) -> None:
    command("import sys; sys.exit(2)")

    with pytest.raises(VideoError, match="decoder exited with 2"):
        await extract_frames(bytes(10 << 20))


def frame_bytes(*frames: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(frame)) + frame for frame in frames)


def test_read_frames_round_trips() -> None:
    assert read_frames(frame_bytes(b"a", b"bc")) == [b"a", b"bc"]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (frame_bytes(b"abc")[:-1], "malformed"),
        (b"\x00\x00", "truncated"),
        (struct.pack(">I", MAX_BYTES + 1), "malformed"),
        (frame_bytes(*[b"x"] * 4), "returned 4 frames"),
        (b"", "returned 0 frames"),
    ],
    ids=["short-frame", "short-length", "oversized", "too-many", "none"],
)
def test_read_frames_rejects_what_a_well_behaved_decoder_cannot_produce(output: bytes, message: str) -> None:
    with pytest.raises(VideoError, match=message):
        read_frames(output)


def test_distinct_drops_near_duplicates_but_keeps_hues_of_equal_brightness() -> None:
    red, noisy_red = PIL.Image.new("RGB", (64, 64), RED), PIL.Image.new("RGB", (64, 64), (250, 4, 3))
    # Pure green at 128 and pure red share a grayscale level, which is why fingerprints keep colour.
    green = PIL.Image.new("RGB", (64, 64), GREEN)

    assert distinct([red, noisy_red, green]) == [red, green]
