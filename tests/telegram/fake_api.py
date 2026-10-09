import collections.abc as cabc
import itertools
import typing as t

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetChat, GetChatMember, GetMe, SendMessage, SendPhoto, TelegramMethod
from aiogram.types import ChatFullInfo, ChatMemberMember, ChatMemberOwner, Message, User


class FakeTelegram(BaseSession):
    """Bot API stand-in: records every call and answers with realistic objects instead of hitting the network."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.admins: set[int] = set()
        # A list of errors is consumed one per call, so a call can fail N times and then succeed.
        self.errors: dict[type[TelegramMethod], Exception | list[Exception]] = {}
        self._message_ids = itertools.count(1000)

    def calls(self, method: type[TelegramMethod]) -> list[t.Any]:
        return [request for request in self.requests if isinstance(request, method)]

    def sent_texts(self, chat_id: int | None = None) -> list[str]:
        return [m.text for m in self.calls(SendMessage) if chat_id is None or m.chat_id == chat_id]

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> t.Any:
        self.requests.append(method)
        error = self.errors.get(type(method))
        if isinstance(error, list):
            error = error.pop(0) if error else None
        if error is not None:
            raise error
        if isinstance(method, SendMessage):
            return Message.model_validate(
                {
                    "message_id": next(self._message_ids),
                    "date": 0,
                    "chat": {"id": method.chat_id, "type": "private" if method.chat_id > 0 else "supergroup"},
                    "text": method.text,
                }
            )
        if isinstance(method, SendPhoto):
            message_id = next(self._message_ids)
            return Message.model_validate(
                {
                    "message_id": message_id,
                    "date": 0,
                    "chat": {"id": method.chat_id, "type": "supergroup"},
                    "caption": method.caption,
                    "photo": [
                        {"file_id": f"small-{message_id}", "file_unique_id": "s", "width": 90, "height": 90},
                        {"file_id": f"photo-{message_id}", "file_unique_id": "p", "width": 1280, "height": 960},
                    ],
                }
            )
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="U")
            if method.user_id in self.admins:
                return ChatMemberOwner(user=user, is_anonymous=False)
            return ChatMemberMember(user=user)
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="memedexer", username="memedexer_bot")
        if isinstance(method, GetChat):
            return ChatFullInfo.model_construct(id=method.chat_id, type="private")
        return True

    async def stream_content(self, *args: t.Any, **kwargs: t.Any) -> cabc.AsyncGenerator[bytes]:
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        return None
