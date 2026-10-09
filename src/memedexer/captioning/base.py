"""Provider-neutral captioning contract: input image, output caption, errors."""

import dataclasses
import re
import typing as t

import pydantic

from memedexer.captioning.catalog import ModelSpec

SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MAX_TAGS = 4
CaptionKind = t.Literal["meme", "screenshot", "photo", "other"]

_NON_TAG_CHARS = re.compile(r"[^\w]+")


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
    async def caption(self, image: Image, *, model: ModelSpec, description_language: str | None) -> CaptionResult: ...


def caption_json_schema() -> dict[str, t.Any]:
    return CaptionPayload.model_json_schema()


def normalize_tag(raw: str) -> str | None:
    """Make a Telegram hashtag: word characters only, words joined by `_`, not all digits."""
    body = _NON_TAG_CHARS.sub("_", raw.strip().lstrip("#")).strip("_")
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
        text=payload.text.strip(),
        description=payload.description.strip(),
        tags=normalize_tags(payload.tags),
        languages=list(dict.fromkeys(code.strip().lower() for code in payload.languages if code.strip())),
        kind=payload.kind,
        usage=usage,
    )


def is_retryable_status(status_code: int) -> bool:
    return status_code in (408, 409, 429) or status_code >= 500
