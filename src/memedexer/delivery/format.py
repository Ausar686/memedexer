"""Render a stored caption as a Telegram photo caption within its length limit."""

import html
import re

# Telegram measures the parsed caption text (markup excluded) in UTF-16 code units.
CAPTION_LIMIT = 1024
ELLIPSIS = "…"

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def tg_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text.strip(), maxsplit=1)[0]


def truncate(text: str, limit: int) -> str:
    """Cut to at most `limit` UTF-16 units, marking the cut with an ellipsis."""
    if tg_len(text) <= limit:
        return text
    budget, used = limit - tg_len(ELLIPSIS), 0
    for index, char in enumerate(text):
        used += tg_len(char)
        if used > budget:
            return text[:index].rstrip() + ELLIPSIS if limit > 0 else ""
    return text


def format_caption(text: str, description: str, tags: list[str], limit: int = CAPTION_LIMIT) -> str:
    """HTML caption: OCR text in an expandable quote, then the description, then hashtags.

    Over the limit, tags are kept whole, the description shrinks to its first sentence, then the OCR text is
    truncated, then the description.
    """
    text, description, tag_line = text.strip(), description.strip(), " ".join(tags)
    if _visible_len(text, description, tag_line) > limit:
        description = first_sentence(description)
    overflow = _visible_len(text, description, tag_line) - limit
    if overflow > 0 and text:
        text = truncate(text, max(tg_len(text) - overflow, 0))
        if text == ELLIPSIS:
            text = ""
    overflow = _visible_len(text, description, tag_line) - limit
    if overflow > 0:
        description = truncate(description, max(tg_len(description) - overflow, 0))
    parts = []
    if text:
        parts.append(f"<blockquote expandable>{html.escape(text)}</blockquote>")
    parts.extend(html.escape(part) for part in (description, tag_line) if part)
    return "\n".join(parts)


def _visible_len(*parts: str) -> int:
    present = [part for part in parts if part]
    return sum(tg_len(part) for part in present) + max(len(present) - 1, 0)
