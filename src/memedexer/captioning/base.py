"""Provider-neutral captioning contract: input images, output caption, errors."""

import collections.abc as cabc
import dataclasses
import re
import typing as t

import pydantic

from memedexer.captioning.catalog import ModelSpec

SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MAX_TAGS = 4
# The model's output is as untrusted as the image it read: text on an image can steer it. These bound what gets stored
# and posted; a real caption stays well within them.
MAX_TEXT_CHARS = 4000
MAX_DESCRIPTION_CHARS = 500
MAX_TAG_CHARS = 64
MAX_LANGUAGES = 5
CaptionKind = t.Literal["meme", "screenshot", "photo", "other"]

_NON_TAG_CHARS = re.compile(r"[^\w]+")
_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")
# Control characters (line breaks and tabs aside) and the bidirectional overrides and isolates, which can make posted
# text read differently from what it contains. Zero-width joiners stay: emoji sequences need them.
_UNSAFE_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


@dataclasses.dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str

    def __post_init__(self) -> None:
        if self.media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError(f"unsupported media type {self.media_type!r}")


@dataclasses.dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class CaptionPayload(pydantic.BaseModel):
    """The structured output requested from every provider."""

    model_config = pydantic.ConfigDict(extra="forbid")

    text: str
    description: str
    tags: list[str]
    languages: list[str]
    kind: CaptionKind


@dataclasses.dataclass(frozen=True)
class CaptionResult:
    text: str
    description: str
    tags: list[str]
    languages: list[str]
    kind: CaptionKind
    usage: Usage


class CaptionError(Exception):
    """A failed captioning call; `usage` is set when the provider billed it anyway."""

    def __init__(self, message: str, *, retryable: bool, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.usage = usage


class CaptionRefused(CaptionError):
    def __init__(self, message: str, *, usage: Usage | None = None) -> None:
        super().__init__(message, retryable=False, usage=usage)


class CaptionProvider(t.Protocol):
    async def caption(
        self, images: cabc.Sequence[Image], *, model: ModelSpec, description_language: str | None
    ) -> CaptionResult:
        """Caption one picture, or a video from several of its frames in playback order."""
        ...


def caption_json_schema() -> dict[str, t.Any]:
    return CaptionPayload.model_json_schema()


def clean_text(raw: str, limit: int) -> str:
    """Drop unsafe characters and cap the length, marking a cut with an ellipsis."""
    text = _UNSAFE_CHARS.sub("", raw).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def normalize_tag(raw: str) -> str | None:
    """Make a Telegram hashtag: word characters only, words joined by `_`, not all digits, at most `MAX_TAG_CHARS`."""
    body = _NON_TAG_CHARS.sub("_", raw.strip().lstrip("#")).strip("_")[:MAX_TAG_CHARS].rstrip("_")
    if not body or body.replace("_", "").isdigit():
        return None
    return f"#{body}"


def normalize_tags(raw: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = normalize_tag(item)
        if tag is None or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        tags.append(tag)
    return tags[:MAX_TAGS]


def parse_payload(raw: str | None, usage: Usage) -> CaptionResult:
    """Validate a provider's JSON reply and normalize it into a `CaptionResult`."""
    if raw is None:
        raise CaptionError("response has no text content", retryable=False, usage=usage)
    try:
        payload = CaptionPayload.model_validate_json(raw)
    except pydantic.ValidationError as exc:
        raise CaptionError(f"response does not match the caption schema: {exc}", retryable=False, usage=usage) from exc
    return CaptionResult(
        text=clean_text(payload.text, MAX_TEXT_CHARS),
        description=clean_text(payload.description, MAX_DESCRIPTION_CHARS),
        tags=normalize_tags(payload.tags),
        languages=normalize_languages(payload.languages),
        kind=payload.kind,
        usage=usage,
    )


def normalize_languages(raw: list[str]) -> list[str]:
    """ISO 639 codes only, lowercase, deduplicated, at most `MAX_LANGUAGES`."""
    codes = (code.strip().lower() for code in raw)
    return list(dict.fromkeys(code for code in codes if _LANGUAGE_CODE.fullmatch(code)))[:MAX_LANGUAGES]


def is_retryable_status(status_code: int) -> bool:
    return status_code in (408, 409, 429) or status_code >= 500
