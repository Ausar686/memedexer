"""Fit downloaded images into what the provider APIs accept."""

import io

import PIL.Image

from memedexer.captioning.base import SUPPORTED_MEDIA_TYPES, Image

# Models downscale anything larger anyway, and the token cost grows with pixels.
MAX_SIDE = 1568
# Anthropic caps base64 images at 5 MB; base64 inflates by 4/3.
MAX_BYTES = 3_750_000
JPEG_QUALITY = 90


class ImageError(ValueError):
    pass


def prepare_image(data: bytes, media_type: str) -> Image:
    """Pass small supported images through untouched; re-encode the rest as JPEG within the limits."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as source:
            fits = max(source.size) <= MAX_SIDE and len(data) <= MAX_BYTES
            if fits and media_type in SUPPORTED_MEDIA_TYPES:
                return Image(data, media_type)
            return Image(_to_jpeg(source), "image/jpeg")
    except (PIL.UnidentifiedImageError, PIL.Image.DecompressionBombError, OSError) as exc:
        raise ImageError(f"cannot decode image: {exc}") from exc


def _to_jpeg(source: PIL.Image.Image) -> bytes:
    image = source.convert("RGBA")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    flattened = PIL.Image.new("RGB", image.size, "white")
    flattened.paste(image, mask=image.getchannel("A"))
    out = io.BytesIO()
    flattened.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
