import typing as t

from aiogram.types import ChatMemberAdministrator, Message, Update

from tests.factories import CHAT_ID, THREAD_ID

PHOTO_SIZES = [
    {"file_id": "s", "file_unique_id": "us", "width": 90, "height": 67, "file_size": 1_000},
    {"file_id": "m", "file_unique_id": "um", "width": 320, "height": 240, "file_size": 10_000},
    {"file_id": "y", "file_unique_id": "uy", "width": 1280, "height": 960, "file_size": 120_000},
    {"file_id": "w", "file_unique_id": "uw", "width": 2560, "height": 1920, "file_size": 400_000},
]


def message_data(
    *,
    message_id: int = 100,
    chat_id: int = CHAT_ID,
    chat_type: str = "supergroup",
    thread_id: int | None = THREAD_ID,
    is_topic_message: bool = True,
    photo: list[dict] | None = None,
    document: dict | None = None,
    edit_date: int | None = None,
    text: str | None = None,
    user_id: int = 5,
    reply_to: dict[str, t.Any] | None = None,
    **extra: t.Any,
) -> dict[str, t.Any]:
    data: dict[str, t.Any] = {
        "message_id": message_id,
        "date": 1_760_000_000,
        "chat": {"id": chat_id, "type": chat_type, "title": "memes"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
        **extra,
    }
    if text is not None:
        data["text"] = text
    if reply_to is not None:
        data["reply_to_message"] = reply_to
    if thread_id is not None:
        data["message_thread_id"] = thread_id
        data["is_topic_message"] = is_topic_message
    if photo is not None:
        data["photo"] = photo
    if document is not None:
        data["document"] = document
    if edit_date is not None:
        data["edit_date"] = edit_date
    return data


def message(**kwargs: t.Any) -> Message:
    return Message.model_validate(message_data(**kwargs))


def update(update_id: int = 1, *, edited: bool = False, **kwargs: t.Any) -> Update:
    key = "edited_message" if edited else "message"
    return Update.model_validate({"update_id": update_id, key: message_data(**kwargs)})


def document(mime_type: str = "image/png", file_size: int = 50_000, unique: str = "doc") -> dict[str, t.Any]:
    return {"file_id": f"file-{unique}", "file_unique_id": unique, "mime_type": mime_type, "file_size": file_size}


def administrator() -> dict[str, t.Any]:
    fields = ChatMemberAdministrator.model_fields
    flags = {name: False for name, field in fields.items() if field.is_required() and field.annotation is bool}
    return {"status": "administrator", **flags}


def member_update(
    update_id: int = 1,
    *,
    old: str | dict[str, t.Any],
    new: str | dict[str, t.Any],
    chat_id: int = CHAT_ID,
    title: str = "memes",
    is_forum: bool = True,
    by: int = 5,
) -> Update:
    bot_user = {"id": 42, "is_bot": True, "first_name": "memedexer"}
    return Update.model_validate(
        {
            "update_id": update_id,
            "my_chat_member": {
                "chat": {"id": chat_id, "type": "supergroup", "title": title, "is_forum": is_forum},
                "from": {"id": by, "is_bot": False, "first_name": "Bob", "last_name": "<Admin>"},
                "date": 0,
                "old_chat_member": _member(old, bot_user),
                "new_chat_member": _member(new, bot_user),
            },
        }
    )


def _member(status: str | dict[str, t.Any], user: dict[str, t.Any]) -> dict[str, t.Any]:
    data = {"status": status} if isinstance(status, str) else status
    return {**data, "user": user}


def callback_update(data: str, *, user_id: int, update_id: int = 1) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": "cb1",
                "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
                "chat_instance": "ci",
                "data": data,
                "message": message_data(message_id=77, chat_id=user_id, chat_type="private", thread_id=None, text="x"),
            },
        }
    )
