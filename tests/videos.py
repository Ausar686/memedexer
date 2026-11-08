"""Builds small video files in memory for tests, so no binary fixtures are needed."""

import io
import pathlib

import av
import PIL.Image
import PIL.ImageDraw

ROTATED = (pathlib.Path(__file__).parent / "fixtures" / "rotated.mp4").read_bytes()


def clip(
    colors: list[str],
    *,
    size: tuple[int, int] = (320, 240),
    fps: int = 10,
    seconds_each: float = 1.0,
    text: str | None = None,
    container: str = "mp4",
    codec: str = "h264",
) -> bytes:
    """One solid-colour scene per entry in `colors`, optionally with the same `text` drawn over every frame."""
    out = io.BytesIO()
    with av.open(out, "w", format=container) as output:
        stream = output.add_stream(codec, rate=fps)
        stream.width, stream.height, stream.pix_fmt = *size, "yuv420p"
        for color in colors:
            picture = PIL.Image.new("RGB", size, color)
            if text:
                draw = PIL.ImageDraw.Draw(picture)
                draw.text((20, 20), text, fill="white", stroke_width=3, stroke_fill="black", font_size=size[0] // 16)
            for _ in range(round(fps * seconds_each)):
                for packet in stream.encode(av.VideoFrame.from_image(picture)):
                    output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return out.getvalue()


def gif(colors: list[str], *, size: tuple[int, int] = (120, 90), frame_ms: int = 200) -> bytes:
    out = io.BytesIO()
    frames = [PIL.Image.new("RGB", size, color) for color in colors]
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=frame_ms, loop=0)
    return out.getvalue()


def audio_only() -> bytes:
    out = io.BytesIO()
    with av.open(out, "w", format="mp4") as output:
        stream = output.add_stream("aac", rate=8000)
        frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.sample_rate = 8000
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return out.getvalue()
