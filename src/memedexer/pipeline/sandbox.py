"""Decode untrusted images and videos in a separate, resource-limited process (see `decoder`) and read them back.

Decoding runs Pillow's and FFmpeg's native code on files anyone in a chat can send. In a child process a crash or a hang
fails one job instead of the bot, a decompression bomb hits the child's memory limit instead of the bot's, the child can
be killed on timeout (a thread can't), and it never sees the bot's secrets.
"""

import asyncio
import contextlib
import os
import signal
import struct
import sys
import typing as t

from memedexer.captioning.base import Image
from memedexer.pipeline.images import MAX_BYTES, ImageError

# Late enough to skip fade-ins and title cards, early enough to skip outros and end cards.
FRAME_POSITIONS = (0.1, 0.5, 0.9)
DECODE_TIMEOUT_SECONDS = 15.0
ERROR_CHARS = 300
# The most a well-behaved child writes; a compromised one could otherwise fill the bot's memory.
MAX_OUTPUT_BYTES = len(FRAME_POSITIONS) * (4 + MAX_BYTES)
STDERR_TAIL_BYTES = 64 * 1024
REAP_TIMEOUT_SECONDS = 5.0

_COMMAND = (sys.executable, "-I", "-m", "memedexer.pipeline.decoder")
_LENGTH = struct.Struct(">I")
_CHUNK_BYTES = 64 * 1024
_SIGNATURES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


class VideoError(ValueError):
    pass


_ERRORS: dict[str, type[ValueError]] = {"image": ImageError, "video": VideoError}


async def prepare_image(data: bytes) -> Image:
    """The image as the provider gets it: as is when small, else a JPEG within `images.MAX_SIDE`."""
    [picture] = await _decode(["image"], data, count=1)
    return picture


async def extract_frames(data: bytes, duration_hint: float | None = None) -> list[Image]:
    """Up to `len(FRAME_POSITIONS)` distinct, upright JPEG frames in playback order."""
    args = ["video", "--duration", str(duration_hint)] if duration_hint else ["video"]
    return await _decode(args, data, count=len(FRAME_POSITIONS))


async def _decode(args: list[str], data: bytes, *, count: int) -> list[Image]:
    """Run the decoder with `args` (`image` or `video …`), raising `ImageError` or `VideoError` respectively."""
    kind = args[0]
    error = _ERRORS[kind]
    # An empty environment: the child needs no settings, and must not hold the bot's tokens. Its own process group lets
    # a kill reach anything it starts, too.
    process = await asyncio.create_subprocess_exec(
        *_COMMAND,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
        start_new_session=True,
    )
    collected = asyncio.ensure_future(_collect(process, data))
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(collected), DECODE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise error(f"decoding took longer than {DECODE_TIMEOUT_SECONDS:.0f} s") from exc
    finally:
        await _reap(process, collected)
    try:
        if stdout is None:
            raise ValueError("decoder output is too large")
        if process.returncode != 0:
            raise ValueError(_failure(process.returncode, stderr, kind))
        return [Image(picture, _media_type(picture)) for picture in read_frames(stdout, count)]
    except ValueError as exc:
        raise error(str(exc)) from None


async def _collect(process: asyncio.subprocess.Process, data: bytes) -> tuple[bytes | None, bytes]:
    """Feed the child and read both pipes to the end; stdout is `None` past `MAX_OUTPUT_BYTES`, stderr is its tail.

    Reading on past a limit (and discarding) matters: asyncio reports the child's exit only once its pipes close.
    """

    async def feed() -> None:
        try:
            process.stdin.write(data)
            await process.stdin.drain()
            process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the child exited without reading everything; its exit code tells why
        return None

    async def read_stdout() -> bytes | None:
        output: bytearray | None = bytearray()
        while chunk := await process.stdout.read(_CHUNK_BYTES):
            if output is not None:
                output += chunk
                if len(output) > MAX_OUTPUT_BYTES:
                    output = None
                    _kill(process)
        return None if output is None else bytes(output)

    async def read_stderr_tail() -> bytes:
        tail = b""
        while chunk := await process.stderr.read(_CHUNK_BYTES):
            tail = (tail + chunk)[-STDERR_TAIL_BYTES:]
        return tail

    _, stdout, stderr = await asyncio.gather(feed(), read_stdout(), read_stderr_tail())
    return stdout, stderr


async def _reap(process: asyncio.subprocess.Process, collected: asyncio.Future) -> None:
    """Kill the child's process group unless it exited, then wait for its pipes to drain and for its exit status."""
    if process.returncode is None:
        _kill(process)
    done, _ = await asyncio.wait({collected}, timeout=REAP_TIMEOUT_SECONDS)
    if not done:
        # Something outside the group (it left with setsid) still holds a pipe; stop waiting for it.
        collected.cancel()
        return None
    if not collected.cancelled():
        collected.exception()  # retrieved here when the caller timed out or was cancelled first
    await process.wait()
    return None


def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    return None


def write_frames(out: t.BinaryIO, frames: list[bytes]) -> None:
    for frame in frames:
        out.write(_LENGTH.pack(len(frame)))
        out.write(frame)
    return None


def read_frames(data: bytes, count: int = len(FRAME_POSITIONS)) -> list[bytes]:
    """Parse the child's output, rejecting anything a well-behaved child can't produce."""
    frames: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + _LENGTH.size > len(data):
            raise ValueError("decoder output is truncated")
        (size,) = _LENGTH.unpack_from(data, offset)
        offset += _LENGTH.size
        if size > MAX_BYTES or offset + size > len(data):
            raise ValueError("decoder output is malformed")
        frames.append(data[offset : offset + size])
        offset += size
    if not 0 < len(frames) <= count:
        raise ValueError(f"decoder returned {len(frames)} pictures")
    return frames


def _media_type(picture: bytes) -> str:
    """Checked by signature, since the child's output is as untrusted as its input."""
    if picture[:4] == b"RIFF" and picture[8:12] == b"WEBP":
        return "image/webp"
    for signature, media_type in _SIGNATURES.items():
        if picture.startswith(signature):
            return media_type
    raise ValueError("decoder output is not an image")


def _failure(returncode: int, stderr: bytes, noun: str) -> str:
    if returncode < 0:
        return f"decoder was killed by {signal.Signals(-returncode).name}"
    lines = stderr.decode(errors="replace").strip().splitlines()
    return f"cannot decode {noun}: {lines[-1][:ERROR_CHARS]}" if lines else f"decoder exited with {returncode}"
