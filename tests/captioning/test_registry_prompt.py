import pytest

from memedexer.captioning.catalog import UnknownModelError
from memedexer.captioning.prompt import user_instruction
from memedexer.captioning.providers.anthropic import AnthropicProvider
from memedexer.captioning.providers.openai import OpenAIProvider
from memedexer.captioning.registry import build_providers
from memedexer.config import Provider, Settings


def settings(**overrides: object) -> Settings:
    values = {"telegram_bot_token": "1:a", "owner_user_id": 1, "anthropic_api_key": "sk-ant"} | overrides
    return Settings(_env_file=None, **values)


def test_builds_only_providers_with_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    providers = build_providers(settings())
    assert set(providers) == {Provider.ANTHROPIC}
    assert isinstance(providers[Provider.ANTHROPIC], AnthropicProvider)

    providers = build_providers(settings(openai_api_key="sk-oai"))
    assert isinstance(providers[Provider.OPENAI], OpenAIProvider)


def test_unknown_default_model_fails_fast() -> None:
    with pytest.raises(UnknownModelError):
        build_providers(settings(default_model="claude-nonexistent"))


def test_user_instruction_language() -> None:
    assert user_instruction("Russian").endswith("in Russian.")
    assert "English if it has no text" in user_instruction(None)
