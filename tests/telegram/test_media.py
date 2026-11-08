import pytest

from memedexer.pipeline.worker import MAX_DOWNLOAD_BYTES
from memedexer.storage.models import NO_TOPIC, MediaType
from memedexer.telegram.media import IncomingMedia, extract_media, topic_id
from tests.telegram.updates import PHOTO_SIZES, animation, document, message, video


def test_photo_picks_largest_size_within_limit() -> None:
    media = extract_media(message(photo=PHOTO_SIZES))
    assert media == IncomingMedia("y", "uy", MediaType.PHOTO, "image/jpeg", 1280, 960, 120_000)
    assert media.reply_file_id == "y"


def test_photo_falls_back_to_smallest_when_all_too_big() -> None:
    sizes = [{**PHOTO_SIZES[3], "file_id": "huge", "width": 5000}, PHOTO_SIZES[3]]
    assert extract_media(message(photo=sizes)).file_id == "w"


@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/webp"])
def test_image_documents_are_accepted(mime_type: str) -> None:
    media = extract_media(message(document=document(mime_type)))
    assert (media.file_unique_id, media.media_type, media.mime_type, media.width) == (
        "doc",
        MediaType.IMAGE_DOCUMENT,
        mime_type,
        None,
    )
    assert media.reply_file_id is None


def test_video_keeps_duration_and_thumbnail() -> None:
    media = extract_media(message(video=video()))
    assert media == IncomingMedia(
        "file-vid", "vid", MediaType.VIDEO, "video/mp4", 1280, 720, 5_000_000, 12, thumbnail_file_id="thumb"
    )
    assert media.reply_file_id == "file-vid"


def test_oversized_video_is_still_accepted_for_its_thumbnail() -> None:
    media = extract_media(message(video=video(file_size=MAX_DOWNLOAD_BYTES + 1)))
    assert (media.media_type, media.thumbnail_file_id) == (MediaType.VIDEO, "thumb")


def test_animation_wins_over_the_document_telegram_also_sets() -> None:
    gif = animation()
    media = extract_media(message(animation=gif, document={**gif, "file_name": "meme.mp4"}))
    assert (media.media_type, media.file_unique_id, media.duration, media.reply_file_id) == (
        MediaType.ANIMATION,
        "gif",
        3,
        "file-gif",
    )


@pytest.mark.parametrize("mime_type", ["video/mp4", "video/quicktime", "video/webm"])
def test_video_documents_are_accepted_whatever_their_size(mime_type: str) -> None:
    raw = {**document(mime_type, file_size=MAX_DOWNLOAD_BYTES * 5, unique="vdoc"), "thumbnail": video()["thumbnail"]}
    media = extract_media(message(document=raw))
    assert (media.media_type, media.mime_type, media.thumbnail_file_id, media.reply_file_id) == (
        MediaType.VIDEO_DOCUMENT,
        mime_type,
        "thumb",
        None,
    )


def test_missing_video_type_defaults_to_mp4() -> None:
    raw = video()
    del raw["mime_type"]
    assert extract_media(message(video=raw)).mime_type == "video/mp4"


@pytest.mark.parametrize("mime_type", ["image/gif", "image/heic", "application/pdf", None])
def test_other_documents_are_ignored(mime_type: str | None) -> None:
    raw = document("x")
    raw["mime_type"] = mime_type
    assert extract_media(message(document=raw)) is None


def test_oversized_image_document_is_ignored() -> None:
    assert extract_media(message(document=document(file_size=MAX_DOWNLOAD_BYTES + 1))) is None


def test_text_message_has_no_media() -> None:
    assert extract_media(message()) is None


def test_topic_id() -> None:
    assert topic_id(message(thread_id=7)) == 7
    assert topic_id(message(thread_id=None)) == NO_TOPIC
    assert topic_id(message(thread_id=55, is_topic_message=False)) == NO_TOPIC
