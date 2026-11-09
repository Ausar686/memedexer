"""Fit untrusted images into what the provider APIs accept; runs only inside the `decoder` child process."""

import io
import warnings

import PIL.Image

from memedexer.captioning.base import Image

# Models downscale anything larger anyway, and the token cost grows with pixels.
MAX_SIDE = 1568
# Anthropic caps base64 images at 5 MB; base64 inflates by 4/3.
MAX_BYTES = 3_750_000
JPEG_QUALITY = 90
# A tall screenshot or a 48 MP photo fits; a decompression bomb (a few KB that decode to gigabytes) doesn't.
MAX_PIXELS = 64_000_000
# Only what Telegram users send as images. Pillow can parse dozens of other formats, whatever the declared type.
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageError(ValueError):
    pass


def fit_image(data: bytes) -> Image:
    """Pass small images through untouched; re-encode the rest as JPEG within the limits.

    The type comes from the content, not from what the sender declared.
    """
    try:
        with warnings.catch_warnings():
            # Pillow warns about likely bombs it would still decode; the pixel limit below refuses them instead.
            warnings.simplefilter("ignore", PIL.Image.DecompressionBombWarning)
            source = PIL.Image.open(io.BytesIO(data), formats=list(FORMATS))
        with source:
            width, height = source.size
            if width * height > MAX_PIXELS:
                raise ImageError(f"image is too large ({width}×{height} pixels)")
            if max(source.size) <= MAX_SIDE and len(data) <= MAX_BYTES:
                source.verify()
                return Image(data, FORMATS[source.format])
            return encode_jpeg(source)
    except PIL.UnidentifiedImageError as exc:
        raise ImageError("not a JPEG, PNG or WebP image") from exc
    except PIL.Image.DecompressionBombError as exc:
        raise ImageError("image is too large") from exc
    except (OSError, SyntaxError, EOFError) as exc:
        # Pillow reports truncated or corrupt data with any of these.
        raise ImageError(f"corrupt image: {exc}") from exc


def encode_jpeg(source: PIL.Image.Image) -> Image:
    """Re-encode a decoded picture as a JPEG within `MAX_SIDE`."""
    return Image(_to_jpeg(source), "image/jpeg")


def _to_jpeg(source: PIL.Image.Image) -> bytes:
    if source.mode in ("1", "P"):
        # Palette pixels can't be averaged, so a palette image would be downscaled by dropping pixels.
        source = source.convert("RGBA")
    # Shrink before converting, so a large picture never exists in full size twice; JPEGs even decode at reduced scale.
    source.thumbnail((MAX_SIDE, MAX_SIDE))
    image = source.convert("RGBA")
    flattened = PIL.Image.new("RGB", image.size, "white")
    flattened.paste(image, mask=image.getchannel("A"))
    out = io.BytesIO()
    flattened.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
