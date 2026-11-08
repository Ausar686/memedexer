"""Which messages carry captionable media, and which file to take from them."""

import dataclasses

from aiogram.types import Document, Message, PhotoSize
from sqlalchemy.ext.asyncio import AsyncSession

from memedexer.pipeline.images import MAX_SIDE
from memedexer.pipeline.worker import over_download_limit
from memedexer.storage import repo
from memedexer.storage.models import NO_TOPIC, Media, MediaType

IMAGE_DOCUMENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
# Videos whose type a document doesn't state are most likely MP4, Telegram's own video format.
DEFAULT_VIDEO_TYPE = "video/mp4"


@dataclasses.dataclass(frozen=True)
class IncomingMedia:
    file_id: str
    file_unique_id: str
    media_type: MediaType
    mime_type: str
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    duration: int | None = None
    thumbnail_file_id: str | None = None

    @property
    def reply_file_id(self) -> str | None:
        """Documents can't be re-sent as photos or videos by their own id; delivery uploads them once."""
        if self.media_type in (MediaType.IMAGE_DOCUMENT, MediaType.VIDEO_DOCUMENT):
            return None
        return self.file_id


def topic_id(message: Message) -> int:
    """Only forum topic messages count; reply threads in ordinary groups also carry a thread id."""
    if message.is_topic_message and message.message_thread_id is not None:
        return message.message_thread_id
    return NO_TOPIC


def extract_media(message: Message) -> IncomingMedia | None:
    """A message's photo, GIF, video, or image or video document; `None` when it has nothing to caption."""
    if message.photo:
        return _photo(message.photo)
    # Telegram also sets `document` on GIFs for older clients, so the animation must win.
    if (animation := message.animation) is not None:
        return IncomingMedia(
            animation.file_id,
            animation.file_unique_id,
            MediaType.ANIMATION,
            animation.mime_type or DEFAULT_VIDEO_TYPE,
            animation.width,
            animation.height,
            animation.file_size,
            animation.duration,
            _file_id(animation.thumbnail),
        )
    if (video := message.video) is not None:
        return IncomingMedia(
            video.file_id,
            video.file_unique_id,
            MediaType.VIDEO,
            video.mime_type or DEFAULT_VIDEO_TYPE,
            video.width,
            video.height,
            video.file_size,
            video.duration,
            _file_id(video.thumbnail),
        )
    if message.document is not None:
        return _document(message.document)
    return None


async def save_media(session: AsyncSession, media: IncomingMedia) -> Media:
    return await repo.upsert_media(
        session,
        file_unique_id=media.file_unique_id,
        file_id=media.file_id,
        media_type=media.media_type,
        mime_type=media.mime_type,
        width=media.width,
        height=media.height,
        file_size=media.file_size,
        duration=media.duration,
        thumbnail_file_id=media.thumbnail_file_id,
        reply_file_id=media.reply_file_id,
    )


def _photo(sizes: list[PhotoSize]) -> IncomingMedia:
    # The largest size within MAX_SIDE avoids paying for pixels the model would downscale anyway.
    fitting = [p for p in sizes if max(p.width, p.height) <= MAX_SIDE]
    size = max(fitting, key=lambda p: p.width * p.height) if fitting else min(sizes, key=lambda p: p.width * p.height)
    return IncomingMedia(
        size.file_id, size.file_unique_id, MediaType.PHOTO, "image/jpeg", size.width, size.height, size.file_size
    )


def _document(document: Document) -> IncomingMedia | None:
    mime_type = document.mime_type or ""
    if mime_type in IMAGE_DOCUMENT_TYPES:
        # An image document can't fall back to a thumbnail, so one over the download limit is skipped.
        if over_download_limit(document.file_size):
            return None
        return IncomingMedia(
            document.file_id, document.file_unique_id, MediaType.IMAGE_DOCUMENT, mime_type, file_size=document.file_size
        )
    if mime_type.startswith("video/"):
        return IncomingMedia(
            document.file_id,
            document.file_unique_id,
            MediaType.VIDEO_DOCUMENT,
            mime_type,
            file_size=document.file_size,
            thumbnail_file_id=_file_id(document.thumbnail),
        )
    return None


def _file_id(thumbnail: PhotoSize | None) -> str | None:
    return None if thumbnail is None else thumbnail.file_id
