import pytest

from memedexer.storage.models import NO_TOPIC
from memedexer.telegram.media import MAX_DOWNLOAD_BYTES, IncomingImage, extract_image, topic_id
from tests.telegram.updates import PHOTO_SIZES, document, message


def test_photo_picks_largest_size_within_limit() -> None:
    image = extract_image(message(photo=PHOTO_SIZES))
    assert image == IncomingImage("y", "uy", "image/jpeg", 1280, 960, 120_000, is_photo=True)
    assert image.photo_file_id == "y"


def test_photo_falls_back_to_smallest_when_all_too_big() -> None:
    sizes = [{**PHOTO_SIZES[3], "file_id": "huge", "width": 5000}, PHOTO_SIZES[3]]
    assert extract_image(message(photo=sizes)).file_id == "w"


@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/webp"])
def test_image_documents_are_accepted(mime_type: str) -> None:
    image = extract_image(message(document=document(mime_type)))
    assert (image.file_unique_id, image.mime_type, image.width) == ("doc", mime_type, None)
    assert image.photo_file_id is None


@pytest.mark.parametrize("mime_type", ["image/gif", "image/heic", "application/pdf", "video/mp4"])
def test_other_documents_are_ignored(mime_type: str) -> None:
    assert extract_image(message(document=document(mime_type))) is None


def test_oversized_document_is_ignored() -> None:
    assert extract_image(message(document=document(file_size=MAX_DOWNLOAD_BYTES + 1))) is None


def test_text_message_has_no_image() -> None:
    assert extract_image(message()) is None


def test_topic_id() -> None:
    assert topic_id(message(thread_id=7)) == 7
    assert topic_id(message(thread_id=None)) == NO_TOPIC
    assert topic_id(message(thread_id=55, is_topic_message=False)) == NO_TOPIC
