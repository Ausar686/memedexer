"""Sample frames from a video with PyAV; runs only inside the `decoder` child process."""

import io

import av
import PIL.Image
import PIL.ImageChops
import PIL.ImageStat

from memedexer.pipeline.images import MAX_SIDE
from memedexer.pipeline.sandbox import FRAME_POSITIONS

# FFmpeg demuxers (by any of their names) for the containers people send videos and GIFs in. FFmpeg can parse hundreds
# more, including playlists that reference other files; each one is attack surface no meme needs.
DEMUXERS = ("mov", "matroska", "avi", "mpegts", "mpeg", "flv", "asf", "gif", "h264", "hevc")
# Mean per-channel difference (0-255) between 16×16 colour thumbnails below which two frames show the same picture,
# e.g. a still image with music. Colour, since different hues can share a grayscale level.
DUPLICATE_THRESHOLD = 6.0
_FINGERPRINT_SIZE = (16, 16)


def sample_frames(data: bytes, duration_hint: float | None = None) -> list[PIL.Image.Image]:
    """Up to `len(FRAME_POSITIONS)` distinct, upright pictures; `ValueError` when the video can't be decoded."""
    try:
        return _sample(data, duration_hint)
    except av.FFmpegError as exc:
        raise ValueError(exc.strerror or str(exc)) from exc


def _sample(data: bytes, duration_hint: float | None) -> list[PIL.Image.Image]:
    with _open(data) as container:
        if not container.streams.video:
            raise ValueError("no video stream")
        stream = container.streams.video[0]
        duration = _duration(container, stream) or duration_hint
        targets = [duration * position for position in FRAME_POSITIONS] if duration else [0.0]
        try:
            pictures = [_seek_picture(container, stream, target) for target in targets]
        except av.FFmpegError:
            pictures = None
    if pictures is None:
        # Some inputs, like raw streams, can't seek, and a failed seek leaves nothing to decode: reopen the input
        # and take the frames in one forward pass instead.
        pictures = _scan(data, targets)
    pictures = [picture for picture in pictures if picture is not None]
    if not pictures:
        raise ValueError("no decodable frames")
    return distinct(pictures)


def distinct(pictures: list[PIL.Image.Image]) -> list[PIL.Image.Image]:
    kept: list[PIL.Image.Image] = []
    fingerprints: list[PIL.Image.Image] = []
    for picture in pictures:
        fingerprint = picture.convert("RGB").resize(_FINGERPRINT_SIZE)
        if all(_difference(fingerprint, other) >= DUPLICATE_THRESHOLD for other in fingerprints):
            kept.append(picture)
            fingerprints.append(fingerprint)
    return kept


def _difference(a: PIL.Image.Image, b: PIL.Image.Image) -> float:
    channels = PIL.ImageStat.Stat(PIL.ImageChops.difference(a, b)).mean
    return sum(channels) / len(channels)


def _open(data: bytes) -> av.container.InputContainer:
    try:
        return av.open(io.BytesIO(data), options={"format_whitelist": ",".join(DEMUXERS)})
    except av.ArgumentError as exc:
        # FFmpeg reports a recognised but non-allowlisted container as an invalid argument.
        raise ValueError("unsupported container format") from exc


def _duration(container: av.container.InputContainer, stream: av.VideoStream) -> float | None:
    if stream.duration is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return container.duration / av.time_base
    return None


def _seek_picture(
    container: av.container.InputContainer, stream: av.VideoStream, target: float
) -> PIL.Image.Image | None:
    """The first frame shown at or after `target` seconds, or the last frame when the video ends earlier."""
    container.seek(int(target / stream.time_base), stream=stream)
    frame = None
    for frame in container.decode(stream):
        if frame.time is None or frame.time >= target:
            break
    return None if frame is None else _upright(frame)


def _scan(data: bytes, targets: list[float]) -> list[PIL.Image.Image]:
    """`_seek_picture` for every target in one forward pass; frame times come from the frame rate if unstamped."""
    pictures: list[PIL.Image.Image] = []
    pending = list(targets)
    last = None
    with _open(data) as container:
        stream = container.streams.video[0]
        rate = stream.guessed_rate or stream.average_rate
        for index, frame in enumerate(container.decode(stream)):
            last = frame
            time = frame.time if frame.time is not None else (float(index / rate) if rate else None)
            while pending and (time is None or time >= pending[0]):
                pictures.append(_upright(frame))
                pending.pop(0)
            if not pending:
                break
        if pending and last is not None:
            pictures.append(_upright(last))
    return pictures


def _upright(frame: av.VideoFrame) -> PIL.Image.Image:
    # Scale while converting so a huge frame never exists as a full-size RGB picture.
    scale = min(1.0, MAX_SIDE / max(frame.width, frame.height))
    picture = frame.to_image(width=max(1, round(frame.width * scale)), height=max(1, round(frame.height * scale)))
    # Phones store portrait video sideways plus a display rotation, counter-clockwise in degrees.
    return picture.rotate(frame.rotation, expand=True) if frame.rotation else picture
