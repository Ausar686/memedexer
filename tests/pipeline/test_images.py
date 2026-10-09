import io
import pathlib

import PIL.Image
import pytest

from memedexer.pipeline.images import MAX_BYTES, MAX_SIDE, ImageError, prepare_image

MEME = pathlib.Path(__file__).parent.parent / "fixtures" / "meme.png"


def encode(image: PIL.Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def test_small_supported_image_passes_through() -> None:
    data = MEME.read_bytes()
    image = prepare_image(data, "image/png")
    assert (image.data, image.media_type) == (data, "image/png")


def test_large_image_is_downscaled_to_jpeg() -> None:
    data = encode(PIL.Image.new("RGBA", (4000, 1000), (255, 0, 0, 128)), "PNG")
    image = prepare_image(data, "image/png")
    assert image.media_type == "image/jpeg"
    with PIL.Image.open(io.BytesIO(image.data)) as result:
        assert result.size == (MAX_SIDE, MAX_SIDE // 4)
        assert result.mode == "RGB"


def test_oversized_file_is_reencoded(monkeypatch: pytest.MonkeyPatch) -> None:
    data = MEME.read_bytes()
    monkeypatch.setattr("memedexer.pipeline.images.MAX_BYTES", len(data) - 1)
    image = prepare_image(data, "image/png")
    assert image.media_type == "image/jpeg"
    assert len(image.data) <= MAX_BYTES


def test_unsupported_format_is_converted() -> None:
    data = encode(PIL.Image.new("RGB", (100, 100), "blue"), "BMP")
    assert prepare_image(data, "image/bmp").media_type == "image/jpeg"


def test_garbage_raises_image_error() -> None:
    with pytest.raises(ImageError, match="cannot decode"):
        prepare_image(b"not an image", "image/jpeg")
