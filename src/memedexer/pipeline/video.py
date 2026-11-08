"""Sample a video's frames in a separate, resource-limited process (see `frames`) and read them back.

Decoding untrusted video runs FFmpeg's native code. In a child process a crash or a hang fails one job instead of the
bot, the child can be killed on timeout (a thread can't), and it never sees the bot's secrets.
"""

import asyncio
import signal
import struct
import sys
import typing as t

from memedexer.captioning.base import Image
from memedexer.pipeline.images import MAX_BYTES

# Late enough to skip fade-ins and title cards, early enough to skip outros and end cards.
FRAME_POSITIONS = (0.1, 0.5, 0.9)
DECODE_TIMEOUT_SECONDS = 15.0
ERROR_CHARS = 300

_COMMAND = (sys.executable, "-I", "-m", "memedexer.pipeline.frames")
_LENGTH = struct.Struct(">I")


class VideoError(ValueError):
    pass


async def extract_frames(data: bytes, duration_hint: float | None = None) -> list[Image]:
    """Up to `len(FRAME_POSITIONS)` distinct, upright JPEG frames in playback order."""
    args = [*_COMMAND, "--duration", str(duration_hint)] if duration_hint else [*_COMMAND]
    # An empty environment: the child needs no settings, and must not hold the bot's tokens.
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(data), DECODE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise VideoError(f"decoding took longer than {DECODE_TIMEOUT_SECONDS:.0f} s") from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise VideoError(_failure(process.returncode, stderr))
    return [Image(frame, "image/jpeg") for frame in read_frames(stdout)]


def write_frames(out: t.BinaryIO, frames: list[bytes]) -> None:
    for frame in frames:
        out.write(_LENGTH.pack(len(frame)))
        out.write(frame)
    return None


def read_frames(data: bytes) -> list[bytes]:
    """Parse the child's output, rejecting anything a well-behaved child can't produce."""
    frames: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + _LENGTH.size > len(data):
            raise VideoError("decoder output is truncated")
        (size,) = _LENGTH.unpack_from(data, offset)
        offset += _LENGTH.size
        if size > MAX_BYTES or offset + size > len(data):
            raise VideoError("decoder output is malformed")
        frames.append(data[offset : offset + size])
        offset += size
    if not 0 < len(frames) <= len(FRAME_POSITIONS):
        raise VideoError(f"decoder returned {len(frames)} frames")
    return frames


def _failure(returncode: int, stderr: bytes) -> str:
    if returncode < 0:
        return f"decoder was killed by {signal.Signals(-returncode).name}"
    lines = stderr.decode(errors="replace").strip().splitlines()
    return f"cannot decode video: {lines[-1][:ERROR_CHARS]}" if lines else f"decoder exited with {returncode}"
