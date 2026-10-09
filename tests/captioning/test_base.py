import pytest

from memedexer.captioning.base import (
    CaptionError,
    Image,
    Usage,
    caption_json_schema,
    is_retryable_status,
    normalize_tag,
    normalize_tags,
    parse_payload,
)
from tests.captioning.fakes import PAYLOAD_JSON


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cats", "#cats"),
        ("#cats", "#cats"),
        ("  first try ", "#first_try"),
        ("drake-meme", "#drake_meme"),
        ("мемы_про_код", "#мемы_про_код"),
        ("2024_memes", "#2024_memes"),
        ("123", None),
        ("1_2", None),
        ("#", None),
        ("!!!", None),
    ],
)
def test_normalize_tag(raw: str, expected: str | None) -> None:
    assert normalize_tag(raw) == expected


def test_normalize_tags_dedupes_case_insensitively_and_caps() -> None:
    raw = ["Cats", "#cats", "dogs", "123", "birds", "fish", "frogs"]
    assert normalize_tags(raw) == ["#Cats", "#dogs", "#birds", "#fish"]


def test_parse_payload_normalizes() -> None:
    usage = Usage(10, 2)
    result = parse_payload(PAYLOAD_JSON, usage)
    assert result.text == "WHEN ALL TESTS PASS\nON THE FIRST TRY"
    assert result.tags == ["#testing", "#first_try"]
    assert result.languages == ["en"]
    assert result.kind == "meme"
    assert result.usage is usage


@pytest.mark.parametrize("raw", [None, "not json", '{"text": "x"}', PAYLOAD_JSON.replace('"meme"', '"video"')])
def test_parse_payload_rejects_invalid_and_keeps_usage(raw: str | None) -> None:
    usage = Usage(10, 2)
    with pytest.raises(CaptionError) as excinfo:
        parse_payload(raw, usage)
    assert excinfo.value.retryable is False
    assert excinfo.value.usage is usage


def test_schema_is_strict() -> None:
    schema = caption_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == {"text", "description", "tags", "languages", "kind"}
    assert schema["properties"]["kind"]["enum"] == ["meme", "screenshot", "photo", "other"]


def test_image_rejects_unsupported_media_type() -> None:
    with pytest.raises(ValueError, match="image/bmp"):
        Image(b"", "image/bmp")


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (404, False), (408, True), (409, True), (429, True), (500, True), (529, True)],
)
def test_is_retryable_status(status: int, retryable: bool) -> None:
    assert is_retryable_status(status) is retryable
