"""Claude adapter over the official `anthropic` SDK."""

import base64

import anthropic

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

# Adaptive thinking stays on (it can't be disabled on every current model); headroom covers it plus dense OCR text.
MAX_OUTPUT_TOKENS = 8192


class AnthropicProvider:
    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def caption(self, image: Image, *, model: ModelSpec, description_language: str | None) -> CaptionResult:
        output_config: dict = {"format": {"type": "json_schema", "schema": caption_json_schema()}}
        if model.effort is not None:
            output_config["effort"] = model.effort
        try:
            response = await self._client.messages.create(
                model=model.id,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image.media_type,
                                    "data": base64.standard_b64encode(image.data).decode("ascii"),
                                },
                            },
                            {"type": "text", "text": user_instruction(description_language)},
                        ],
                    }
                ],
                output_config=output_config,
            )
        except anthropic.APIStatusError as exc:
            raise CaptionError(str(exc), retryable=is_retryable_status(exc.status_code)) from exc
        except anthropic.APIConnectionError as exc:
            raise CaptionError(str(exc), retryable=True) from exc

        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise CaptionRefused(f"refused (category: {category})", usage=usage)
        if response.stop_reason != "end_turn":
            raise CaptionError(f"unexpected stop reason {response.stop_reason}", retryable=False, usage=usage)
        text = next((block.text for block in response.content if block.type == "text"), None)
        return parse_payload(text, usage)
