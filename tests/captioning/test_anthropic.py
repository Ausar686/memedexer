import base64
import json
import types

import anthropic
import httpx2
import pytest

from memedexer.captioning.base import CaptionError, CaptionRefused, Image, Usage
from memedexer.captioning.catalog import ModelSpec
from memedexer.captioning.prompt import SYSTEM_PROMPT
from memedexer.captioning.providers.anthropic import AnthropicProvider
from tests.captioning.fakes import PAYLOAD_JSON, FakeEndpoint, http_response


def message(stop_reason: str = "end_turn", text: str | None = PAYLOAD_JSON, **extra: object) -> anthropic.types.Message:
    content = [] if text is None else [{"type": "text", "text": text}]
    return anthropic.types.Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1500, "output_tokens": 120},
            **extra,
        }
    )


def provider(result: object) -> tuple[AnthropicProvider, FakeEndpoint]:
    endpoint = FakeEndpoint(result)
    client = types.SimpleNamespace(messages=endpoint)
    return AnthropicProvider(client), endpoint


async def test_request_shape(image: Image, haiku: ModelSpec) -> None:
    captioner, endpoint = provider(message())
    await captioner.caption(image, model=haiku, description_language="Russian")

    call = endpoint.last_call
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"] == SYSTEM_PROMPT
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in call
    image_block, text_block = call["messages"][0]["content"]
    assert image_block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(image.data).decode(),
    }
    assert "in Russian" in text_block["text"]


async def test_effort_is_omitted_when_unset(image: Image, haiku: ModelSpec) -> None:
    captioner, endpoint = provider(message())
    await captioner.caption(image, model=ModelSpec(**{**vars(haiku), "effort": None}), description_language=None)
    assert "effort" not in endpoint.last_call["output_config"]


async def test_success(image: Image, haiku: ModelSpec) -> None:
    captioner, _ = provider(message())
    result = await captioner.caption(image, model=haiku, description_language=None)
    assert result.text == json.loads(PAYLOAD_JSON)["text"]
    assert result.tags == ["#testing", "#first_try"]
    assert result.usage == Usage(1500, 120)


async def test_refusal_keeps_usage(image: Image, haiku: ModelSpec) -> None:
    refused = message("refusal", text=None, stop_details={"type": "refusal", "category": "cyber", "explanation": None})
    captioner, _ = provider(refused)
    with pytest.raises(CaptionRefused, match="cyber") as excinfo:
        await captioner.caption(image, model=haiku, description_language=None)
    assert excinfo.value.usage == Usage(1500, 120)
    assert excinfo.value.retryable is False


async def test_truncated_output_is_permanent_error(image: Image, haiku: ModelSpec) -> None:
    captioner, _ = provider(message("max_tokens", text='{"text": "WHEN'))
    with pytest.raises(CaptionError, match="max_tokens") as excinfo:
        await captioner.caption(image, model=haiku, description_language=None)
    assert excinfo.value.retryable is False
    assert excinfo.value.usage == Usage(1500, 120)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (anthropic.RateLimitError("slow down", response=http_response(429), body=None), True),
        (anthropic.InternalServerError("oops", response=http_response(529), body=None), True),
        (anthropic.BadRequestError("bad image", response=http_response(400), body=None), False),
        (anthropic.AuthenticationError("bad key", response=http_response(401), body=None), False),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.example.com")), True),
    ],
)
async def test_sdk_errors_are_classified(image: Image, haiku: ModelSpec, error: Exception, retryable: bool) -> None:
    captioner, _ = provider(error)
    with pytest.raises(CaptionError) as excinfo:
        await captioner.caption(image, model=haiku, description_language=None)
    assert excinfo.value.retryable is retryable
    assert excinfo.value.usage is None
    assert excinfo.value.__cause__ is error
