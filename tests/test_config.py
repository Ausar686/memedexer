import decimal

import pydantic
import pytest

from memedexer.config import DEFAULT_DATABASE_URL, Provider, Settings

REQUIRED = {"TELEGRAM_BOT_TOKEN": "123:abc", "OWNER_USER_ID": "42", "ANTHROPIC_API_KEY": "sk-ant"}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def load() -> Settings:
    return Settings(_env_file=None)


def test_defaults(env: pytest.MonkeyPatch) -> None:
    settings = load()
    assert settings.owner_user_id == 42
    assert settings.default_provider is Provider.ANTHROPIC
    assert settings.default_model == "claude-haiku-5-5"
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert (settings.cost_limit_daily_usd, settings.cost_limit_weekly_usd, settings.cost_limit_monthly_usd) == (
        decimal.Decimal(20),
        decimal.Decimal(100),
        decimal.Decimal(200),
    )


def test_empty_values_fall_back_to_defaults(env: pytest.MonkeyPatch) -> None:
    env.setenv("COST_LIMIT_DAILY_USD", "")
    env.setenv("OPENAI_API_KEY", "")
    settings = load()
    assert settings.cost_limit_daily_usd == decimal.Decimal(20)
    assert settings.openai_api_key is None


def test_overrides(env: pytest.MonkeyPatch) -> None:
    env.setenv("COST_LIMIT_MONTHLY_USD", "12.5")
    env.setenv("OPENAI_API_KEY", "sk-oai")
    env.setenv("DEFAULT_PROVIDER", "openai")
    settings = load()
    assert settings.cost_limit_monthly_usd == decimal.Decimal("12.5")
    assert settings.default_provider is Provider.OPENAI
    assert settings.available_providers == {Provider.ANTHROPIC, Provider.OPENAI}


def test_available_providers_require_key(env: pytest.MonkeyPatch) -> None:
    assert load().available_providers == {Provider.ANTHROPIC}


def test_default_provider_without_key_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("DEFAULT_PROVIDER", "openai")
    with pytest.raises(pydantic.ValidationError, match="default provider openai has no API key"):
        load()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_limit_is_rejected(env: pytest.MonkeyPatch, value: str) -> None:
    env.setenv("COST_LIMIT_WEEKLY_USD", value)
    with pytest.raises(pydantic.ValidationError):
        load()


def test_missing_required_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(pydantic.ValidationError, match="telegram_bot_token"):
        load()
