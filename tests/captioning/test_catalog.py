import pytest

from memedexer.captioning.catalog import (
    MODELS,
    ModelSpec,
    UnknownModelError,
    cost_microusd,
    get_model_spec,
    models_for,
)
from memedexer.config import Provider, Settings


def test_cost_is_micro_usd(haiku: ModelSpec) -> None:
    assert cost_microusd(haiku, 1500, 120) == 150 + 60


def test_cost_rounds_up(haiku: ModelSpec) -> None:
    assert cost_microusd(haiku, 1, 0) == 1
    assert cost_microusd(haiku, 0, 0) == 0


def test_unknown_model() -> None:
    with pytest.raises(UnknownModelError, match="openai/claude-haiku-5-5"):
        get_model_spec(Provider.OPENAI, "claude-haiku-5-5")


def test_every_provider_has_models() -> None:
    for provider in Provider:
        assert models_for(provider)
        assert all(spec.provider is provider for spec in models_for(provider))


def test_settings_default_is_allowlisted() -> None:
    fields = Settings.model_fields
    assert (fields["default_provider"].default, fields["default_model"].default) in MODELS
