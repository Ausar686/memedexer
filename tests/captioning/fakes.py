import json
import typing as t

import httpx2

PAYLOAD = {
    "text": "WHEN ALL TESTS PASS\nON THE FIRST TRY",
    "description": "A smiley face between two lines of text.",
    "tags": ["testing", "#Testing", "first try", "123"],
    "languages": ["EN"],
    "kind": "meme",
}
PAYLOAD_JSON = json.dumps(PAYLOAD)


class FakeEndpoint:
    """Stands in for an SDK resource method: records kwargs, returns or raises a canned result."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, t.Any]] = []

    async def create(self, **kwargs: t.Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    @property
    def last_call(self) -> dict[str, t.Any]:
        return self.calls[-1]


def http_response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.example.com/v1"))
