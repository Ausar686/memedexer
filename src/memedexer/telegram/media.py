"""Which messages carry a captionable image, and which file to take from them."""

import dataclasses

from aiogram.types import Message

from memedexer.pipeline.images import MAX_SIDE
from memedexer.storage.models import NO_TOPIC

DOCUMENT_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
# Bot API `getFile` refuses anything larger.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class IncomingImage:
    file_id: str
    file_unique_id: str
    mime_type: str
    width: int | None
    height: int | None
    file_size: int | None


def topic_id(message: Message) -> int:
    """Only forum topic messages count; reply threads in ordinary groups also carry a thread id."""
    if message.is_topic_message and message.message_thread_id is not None:
        return message.message_thread_id
    return NO_TOPIC


def extract_image(message: Message) -> IncomingImage | None:
    if message.photo:
        # The largest size within MAX_SIDE avoids paying for pixels the model would downscale anyway.
        fitting = [p for p in message.photo if max(p.width, p.height) <= MAX_SIDE]
        size = max(fitting, key=lambda p: p.width * p.height) if fitting else min(
            message.photo, key=lambda p: p.width * p.height
        )
        return IncomingImage(size.file_id, size.file_unique_id, "image/jpeg", size.width, size.height, size.file_size)
    document = message.document
    if document is None or document.mime_type not in DOCUMENT_MEDIA_TYPES:
        return None
    if document.file_size is not None and document.file_size > MAX_DOWNLOAD_BYTES:
        return None
    return IncomingImage(
        document.file_id, document.file_unique_id, document.mime_type, None, None, document.file_size
    )
