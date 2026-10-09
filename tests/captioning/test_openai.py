import base64
import types

import httpx2
import openai
import pytest

from memedexer.captioning.base import CaptionError, CaptionRefused, Image, Usage
from memedexer.captioning.catalog import ModelSpec
from memedexer.captioning.prompt import SYSTEM_PROMPT
from memedexer.captioning.providers.openai import OpenAIProvider
from tests.captioning.fakes import PAYLOAD_JSON, FakeEndpoint, http_response


def completion(
    finish_reason: str = "stop", content: str | None = PAYLOAD_JSON, refusal: str | None = None, usage: bool = True
) -> openai.types.chat.ChatCompletion:
    return openai.types.chat.ChatCompletion.model_validate(
        {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-6-luna",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content, "refusal": refusal},
                }
            ],
            "usage": {"prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980} if usage else None,
        }
    )


def provider(result: object) -> tuple[OpenAIProvider, FakeEndpoint]:
    endpoint = FakeEndpoint(result)
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=endpoint))
    return OpenAIProvider(client), endpoint


async def test_request_shape(image: Image, luna: ModelSpec) -> None:
    captioner, endpoint = provider(completion())
    await captioner.caption(image, model=luna, description_language=None)

    call = endpoint.last_call
    assert call["model"] == "gpt-6-luna"
    assert call["reasoning_effort"] == "low"
    assert call["response_format"]["json_schema"]["strict"] is True
    system, user = call["messages"]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    image_part, text_part = user["content"]
    assert image_part["image_url"]["url"] == "data:image/png;base64," + base64.standard_b64encode(image.data).decode()
    assert "main language of the text" in text_part["text"]


async def test_effort_is_omitted_when_unset(image: Image, luna: ModelSpec) -> None:
    captioner, endpoint = provider(completion())
    await captioner.caption(image, model=ModelSpec(**{**vars(luna), "effort": None}), description_language=None)
    assert "reasoning_effort" not in endpoint.last_call


async def test_success(image: Image, luna: ModelSpec) -> None:
    captioner, _ = provider(completion())
    result = await captioner.caption(image, model=luna, description_language=None)
    assert result.kind == "meme"
    assert result.usage == Usage(900, 80)


async def test_missing_usage_counts_as_zero(image: Image, luna: ModelSpec) -> None:
    captioner, _ = provider(completion(usage=False))
    result = await captioner.caption(image, model=luna, description_language=None)
    assert result.usage == Usage(0, 0)


@pytest.mark.parametrize(
    "response",
    [
        completion(content=None, refusal="I can't help with that."),
        completion(finish_reason="content_filter", content=None),
    ],
)
async def test_refusals_keep_usage(image: Image, luna: ModelSpec, response: object) -> None:
    captioner, _ = provider(response)
    with pytest.raises(CaptionRefused) as excinfo:
        await captioner.caption(image, model=luna, description_language=None)
    assert excinfo.value.usage == Usage(900, 80)


async def test_empty_choices_is_permanent_error(image: Image, luna: ModelSpec) -> None:
    response = completion()
    response.choices = []
    captioner, _ = provider(response)
    with pytest.raises(CaptionError, match="no choices") as excinfo:
        await captioner.caption(image, model=luna, description_language=None)
    assert excinfo.value.usage == Usage(900, 80)


async def test_truncated_output_is_permanent_error(image: Image, luna: ModelSpec) -> None:
    captioner, _ = provider(completion(finish_reason="length", content='{"text": "WH'))
    with pytest.raises(CaptionError, match="length") as excinfo:
        await captioner.caption(image, model=luna, description_language=None)
    assert excinfo.value.retryable is False


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (openai.RateLimitError("slow down", response=http_response(429), body=None), True),
        (openai.InternalServerError("oops", response=http_response(503), body=None), True),
        (openai.BadRequestError("bad image", response=http_response(400), body=None), False),
        (openai.APIConnectionError(request=httpx2.Request("POST", "https://api.example.com")), True),
    ],
)
async def test_sdk_errors_are_classified(image: Image, luna: ModelSpec, error: Exception, retryable: bool) -> None:
    captioner, _ = provider(error)
    with pytest.raises(CaptionError) as excinfo:
        await captioner.caption(image, model=luna, description_language=None)
    assert excinfo.value.retryable is retryable
    assert excinfo.value.__cause__ is error
