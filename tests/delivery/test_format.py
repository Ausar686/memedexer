import html
import re

import pytest

from memedexer.delivery.format import CAPTION_LIMIT, ELLIPSIS, first_sentence, format_caption, tg_len, truncate


def visible(caption: str) -> str:
    """What Telegram counts: the caption with markup removed and entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", caption))


def test_layout_and_escaping() -> None:
    caption = format_caption("WHEN <TESTS> PASS\nFIRST TRY", "A smiley & two lines.", ["#tests", "#smiley"])
    assert caption == (
        "<blockquote expandable>WHEN &lt;TESTS&gt; PASS\nFIRST TRY</blockquote>\n"
        "A smiley &amp; two lines.\n"
        "#tests #smiley"
    )


def test_empty_parts_are_omitted() -> None:
    assert format_caption("", "A cat photo.", []) == "A cat photo."
    assert format_caption("TEXT", "", ["#a"]) == "<blockquote expandable>TEXT</blockquote>\n#a"


def test_short_caption_keeps_full_description() -> None:
    caption = format_caption("hi", "First sentence. Second sentence.", ["#a"])
    assert "Second sentence." in caption


def test_long_caption_cuts_description_to_first_sentence_first() -> None:
    text = "x" * 990
    caption = format_caption(text, "Short one. Then a much longer second sentence here.", ["#tag"])
    assert visible(caption) == f"{text}\nShort one.\n#tag"
    assert tg_len(visible(caption)) <= CAPTION_LIMIT


def test_then_truncates_ocr_text_keeping_tags() -> None:
    text = "word " * 400
    caption = format_caption(text, "A description. More.", ["#one", "#two"])
    body = visible(caption)
    assert tg_len(body) == CAPTION_LIMIT
    assert body.endswith(f"{ELLIPSIS}\nA description.\n#one #two")


def test_then_truncates_description_when_it_alone_overflows() -> None:
    description = "y" * 2000
    caption = format_caption("", description, ["#keep"])
    body = visible(caption)
    assert tg_len(body) == CAPTION_LIMIT
    assert body.endswith(f"{ELLIPSIS}\n#keep")


@pytest.mark.parametrize("char", ["я", "😀"])
def test_limit_counts_utf16_units(char: str) -> None:
    caption = format_caption(char * 1500, "Desc.", ["#t"])
    assert tg_len(visible(caption)) <= CAPTION_LIMIT


def test_truncate() -> None:
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 6) == f"hello{ELLIPSIS}"
    assert truncate("😀😀😀", 4) == f"😀{ELLIPSIS}"
    assert truncate("abc", 0) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One. Two.", "One."),
        ("Wow! Really?", "Wow!"),
        ("No terminator", "No terminator"),
        ("v1.2 works. Yes.", "v1.2 works."),
    ],
)
def test_first_sentence(text: str, expected: str) -> None:
    assert first_sentence(text) == expected
