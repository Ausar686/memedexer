"""Builds one provider per configured API key."""

import anthropic
import openai

from memedexer.captioning.base import CaptionProvider
from memedexer.captioning.catalog import get_model_spec
from memedexer.captioning.providers.anthropic import AnthropicProvider
from memedexer.captioning.providers.openai import OpenAIProvider
from memedexer.config import Provider, Settings


def build_providers(settings: Settings) -> dict[Provider, CaptionProvider]:
    """Fails fast if the default model isn't allowlisted, so a typo surfaces at startup, not per job."""
    get_model_spec(settings.default_provider, settings.default_model)
    providers: dict[Provider, CaptionProvider] = {}
    for provider in settings.available_providers:
        key = settings.api_key(provider).get_secret_value()
        match provider:
            case Provider.ANTHROPIC:
                providers[provider] = AnthropicProvider(anthropic.AsyncAnthropic(api_key=key))
            case Provider.OPENAI:
                providers[provider] = OpenAIProvider(openai.AsyncOpenAI(api_key=key))
    return providers
