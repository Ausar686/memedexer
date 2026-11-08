"""Builds one provider per configured API key."""

import anthropic
import openai

from memedexer.captioning.base import CaptionProvider
from memedexer.captioning.catalog import get_model_spec
from memedexer.captioning.providers.anthropic import AnthropicProvider
from memedexer.captioning.providers.openai import OpenAIProvider
from memedexer.config import Provider, Settings

# A caption takes seconds; the SDKs' 10-minute default would let one stalled call hold a worker that long.
REQUEST_TIMEOUT_SECONDS = 90.0
# The worker retries on its own schedule and counts each attempt; SDK retries would multiply calls unseen.
SDK_MAX_RETRIES = 0


def build_providers(settings: Settings) -> dict[Provider, CaptionProvider]:
    """Fails fast if the default model isn't allowlisted, so a typo surfaces at startup, not per job."""
    get_model_spec(settings.default_provider, settings.default_model)
    providers: dict[Provider, CaptionProvider] = {}
    for provider in settings.available_providers:
        key = settings.api_key(provider).get_secret_value()
        match provider:
            case Provider.ANTHROPIC:
                client = anthropic.AsyncAnthropic(
                    api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES
                )
                providers[provider] = AnthropicProvider(client)
            case Provider.OPENAI:
                client = openai.AsyncOpenAI(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES)
                providers[provider] = OpenAIProvider(client)
    return providers
