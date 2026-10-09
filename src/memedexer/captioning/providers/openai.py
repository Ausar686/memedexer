"""OpenAI adapter over the official `openai` SDK; Chat Completions keeps it usable for OpenAI-compatible servers."""

import base64

import openai

from memedexer.captioning.base import (
    CaptionError,
    CaptionRefused,
    CaptionResult,
    Image,
    Usage,
    caption_json_schema,
    is_retryable_status,
    parse_payload,
)
from memedexer.captioning.catalog import ModelSpec
from memedexer.captioning.prompt import SYSTEM_PROMPT, user_instruction

MAX_OUTPUT_TOKENS = 8192


class OpenAIProvider:
    def __init__(self, client: openai.AsyncOpenAI) -> None:
        self._client = client

    async def caption(self, image: Image, *, model: ModelSpec, description_language: str | None) -> CaptionResult:
        data_url = f"data:{image.media_type};base64,{base64.standard_b64encode(image.data).decode('ascii')}"
        extra: dict = {} if model.effort is None else {"reasoning_effort": model.effort}
        try:
            response = await self._client.chat.completions.create(
                model=model.id,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": user_instruction(description_language)},
                        ],
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "caption", "strict": True, "schema": caption_json_schema()},
                },
                **extra,
            )
        except openai.APIStatusError as exc:
            raise CaptionError(str(exc), retryable=is_retryable_status(exc.status_code)) from exc
        except openai.APIConnectionError as exc:
            raise CaptionError(str(exc), retryable=True) from exc

        usage = Usage()
        if response.usage is not None:
            usage = Usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        if not response.choices:
            raise CaptionError("response has no choices", retryable=False, usage=usage)
        choice = response.choices[0]
        if choice.message.refusal:
            raise CaptionRefused(f"refused: {choice.message.refusal}", usage=usage)
        if choice.finish_reason == "content_filter":
            raise CaptionRefused("refused by content filter", usage=usage)
        if choice.finish_reason != "stop":
            raise CaptionError(f"unexpected finish reason {choice.finish_reason}", retryable=False, usage=usage)
        return parse_payload(choice.message.content, usage)
