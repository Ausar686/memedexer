import json

import pytest

from memedexer.captioning.base import (
    MAX_DESCRIPTION_CHARS,
    MAX_TAG_CHARS,
    MAX_TEXT_CHARS,
    CaptionError,
    Image,
    Usage,
    caption_json_schema,
    clean_text,
    is_retryable_status,
    normalize_languages,
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


def test_long_tags_are_cut_without_a_trailing_underscore() -> None:
    assert normalize_tag("a" * (MAX_TAG_CHARS - 1) + " bcd") == "#" + "a" * (MAX_TAG_CHARS - 1)


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("two\nlines\twith a tab", "two\nlines\twith a tab"),
        ("crlf\r\nline", "crlf\nline"),
        ("bell\x07 and nul\x00 and c1\x85", "bell and nul and c1"),
        ("price \u202e01$\u202c", "price 01$"),
        ("isolate \u2066x\u2069", "isolate x"),
        ("family 👨\u200d👩\u200d👧", "family 👨\u200d👩\u200d👧"),
    ],
    ids=["plain", "whitespace", "crlf", "controls", "override", "isolate", "zwj-emoji"],
)
def test_clean_text_drops_unsafe_characters(raw: str, expected: str) -> None:
    assert clean_text(raw, 100) == expected


def test_clean_text_caps_the_length_with_an_ellipsis() -> None:
    assert clean_text("word " * 10, 12) == "word word w…"
    assert len(clean_text("x" * 50, 12)) == 12


def test_normalize_languages_keeps_codes_only() -> None:
    raw = ["EN", " ru ", "en", "english", "zh-Hans", "", "uk\n", "es", "de", "fr", "it"]
    assert normalize_languages(raw) == ["en", "ru", "uk", "es", "de"]


def test_parse_payload_bounds_steered_output() -> None:
    raw = json.dumps(
        {
            "text": "\u202e" + "A" * (MAX_TEXT_CHARS * 2),
            "description": "D" * (MAX_DESCRIPTION_CHARS * 2),
            "tags": ["x" * 1000] * 50,
            "languages": ["ignore previous instructions"] * 100,
            "kind": "meme",
        }
    )

    result = parse_payload(raw, Usage())

    assert (len(result.text), len(result.description)) == (MAX_TEXT_CHARS, MAX_DESCRIPTION_CHARS)
    assert result.text.startswith("AAA")
    assert result.tags == ["#" + "x" * MAX_TAG_CHARS]
    assert result.languages == []


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
