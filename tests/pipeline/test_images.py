"""Image fitting, run in-process; `test_sandbox` covers it through the real child process."""

import io
import pathlib

import PIL.Image
import pytest

from memedexer.pipeline.images import MAX_BYTES, MAX_PIXELS, MAX_SIDE, ImageError, fit_image

MEME = pathlib.Path(__file__).parent.parent / "fixtures" / "meme.png"


def encode(image: PIL.Image.Image, fmt: str, **options: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **options)
    return out.getvalue()


def test_small_image_passes_through() -> None:
    data = MEME.read_bytes()
    image = fit_image(data)
    assert (image.data, image.media_type) == (data, "image/png")


@pytest.mark.parametrize(("fmt", "media_type"), [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_type_comes_from_the_content(fmt: str, media_type: str) -> None:
    assert fit_image(encode(PIL.Image.new("RGB", (64, 64), "blue"), fmt)).media_type == media_type


def test_large_image_is_downscaled_to_jpeg() -> None:
    data = encode(PIL.Image.new("RGBA", (4000, 1000), (255, 0, 0, 128)), "PNG")
    image = fit_image(data)
    assert image.media_type == "image/jpeg"
    with PIL.Image.open(io.BytesIO(image.data)) as result:
        assert result.size == (MAX_SIDE, MAX_SIDE // 4)
        assert result.mode == "RGB"
        assert result.getpixel((10, 10)) == pytest.approx((255, 127, 127), abs=3)  # flattened onto white


def test_palette_images_are_downscaled_smoothly() -> None:
    # Alternating black and white columns: averaging gives gray, dropping pixels would give pure black or white.
    stripes = PIL.Image.new("P", (3200, 100))
    stripes.putpalette([0, 0, 0, 255, 255, 255])
    stripes.putdata([x % 2 for _ in range(100) for x in range(3200)])

    with PIL.Image.open(io.BytesIO(fit_image(encode(stripes, "PNG")).data)) as result:
        assert 90 < result.getpixel((50, 20))[0] < 165


def test_oversized_file_is_reencoded(monkeypatch: pytest.MonkeyPatch) -> None:
    data = MEME.read_bytes()
    monkeypatch.setattr("memedexer.pipeline.images.MAX_BYTES", len(data) - 1)
    image = fit_image(data)
    assert image.media_type == "image/jpeg"
    assert len(image.data) <= MAX_BYTES


def test_decompression_bomb_is_refused_before_decoding() -> None:
    # 17 KB on disk, 144 megapixels decoded: over 1.5 GB of memory before this check existed.
    bomb = encode(PIL.Image.new("1", (12000, 12000)), "PNG", optimize=True)
    assert 12000 * 12000 > MAX_PIXELS

    with pytest.raises(ImageError, match="image is too large"):
        fit_image(bomb)


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "ICO", "TGA", "PCX", "GIF"])
def test_other_formats_are_refused_whatever_the_declared_type(fmt: str) -> None:
    with pytest.raises(ImageError, match="not a JPEG, PNG or WebP image"):
        fit_image(encode(PIL.Image.new("RGB", (64, 64), "blue"), fmt))


def test_garbage_is_refused_with_a_stable_reason() -> None:
    with pytest.raises(ImageError, match="^not a JPEG, PNG or WebP image$"):
        fit_image(b"not an image")


@pytest.mark.parametrize("size", [(64, 64), (4000, 1000)], ids=["passed-through", "re-encoded"])
def test_truncated_image_is_corrupt(size: tuple[int, int]) -> None:
    data = encode(PIL.Image.effect_noise(size, 64), "PNG")

    with pytest.raises(ImageError, match="corrupt image"):
        fit_image(data[: len(data) // 2])
