"""Process-wide settings loaded from the environment and `.env`."""

import decimal
import enum

import pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/memedexer.db"


class Provider(enum.StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class DatabaseSettings(BaseSettings):
    """The subset migrations need, so `alembic` runs without bot credentials."""

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL


class Settings(DatabaseSettings):
    telegram_bot_token: pydantic.SecretStr
    owner_user_id: int

    anthropic_api_key: pydantic.SecretStr | None = None
    openai_api_key: pydantic.SecretStr | None = None

    default_provider: Provider = Provider.ANTHROPIC
    default_model: str = "claude-haiku-5-5"

    cost_limit_daily_usd: decimal.Decimal = pydantic.Field(default=decimal.Decimal(20), gt=0)
    cost_limit_weekly_usd: decimal.Decimal = pydantic.Field(default=decimal.Decimal(100), gt=0)
    cost_limit_monthly_usd: decimal.Decimal = pydantic.Field(default=decimal.Decimal(200), gt=0)

    worker_concurrency: int = pydantic.Field(default=4, gt=0)
    log_level: str = "INFO"

    def api_key(self, provider: Provider) -> pydantic.SecretStr | None:
        keys = {Provider.ANTHROPIC: self.anthropic_api_key, Provider.OPENAI: self.openai_api_key}
        return keys[provider]

    @property
    def available_providers(self) -> frozenset[Provider]:
        return frozenset(p for p in Provider if self.api_key(p) is not None)

    @pydantic.model_validator(mode="after")
    def _default_provider_has_key(self) -> "Settings":
        if self.default_provider not in self.available_providers:
            raise ValueError(f"default provider {self.default_provider!s} has no API key configured")
        return self
