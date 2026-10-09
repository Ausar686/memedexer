import pytest

from memedexer.config import Settings
from memedexer.pipeline.resolve import resolve_caption_settings
from memedexer.storage.models import Chat, Topic


def chat(**overrides: object) -> Chat:
    return Chat(id=-1, **overrides)


def topic(**overrides: object) -> Topic:
    return Topic(chat_id=-1, thread_id=3, **overrides)


def test_defaults(settings: Settings) -> None:
    resolved = resolve_caption_settings(chat(), None, settings)
    assert (resolved.model.provider, resolved.model.id) == ("anthropic", "claude-haiku-5-5")
    assert resolved.description_language is None


def test_topic_beats_chat(settings: Settings) -> None:
    resolved = resolve_caption_settings(
        chat(provider="anthropic", model="claude-sonnet-5-5", description_language="Russian"),
        topic(provider="openai", model="gpt-6-luna"),
        settings,
    )
    assert resolved.model.id == "gpt-6-luna"
    assert resolved.description_language == "Russian"


def test_chat_applies_without_topic_override(settings: Settings) -> None:
    resolved = resolve_caption_settings(chat(provider="anthropic", model="claude-sonnet-5-5"), topic(), settings)
    assert resolved.model.id == "claude-sonnet-5-5"


def test_half_set_override_is_ignored(settings: Settings) -> None:
    resolved = resolve_caption_settings(chat(), topic(provider="openai"), settings)
    assert resolved.model.id == "claude-haiku-5-5"


@pytest.mark.parametrize(("provider", "model"), [("openai", "gpt-1"), ("gemini", "gemini-pro")])
def test_stale_override_falls_through(settings: Settings, provider: str, model: str) -> None:
    resolved = resolve_caption_settings(
        chat(provider="anthropic", model="claude-opus-5-5"), topic(provider=provider, model=model), settings
    )
    assert resolved.model.id == "claude-opus-5-5"
