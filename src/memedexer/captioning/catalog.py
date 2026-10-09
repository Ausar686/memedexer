"""Allowlisted captioning models with their prices; the only models settings may select."""

import dataclasses
import decimal
import math

from memedexer.config import Provider


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    provider: Provider
    id: str
    input_usd_per_mtok: decimal.Decimal
    output_usd_per_mtok: decimal.Decimal
    effort: str | None = "low"


def _spec(provider: Provider, model_id: str, input_price: str, output_price: str) -> ModelSpec:
    return ModelSpec(provider, model_id, decimal.Decimal(input_price), decimal.Decimal(output_price))


MODELS: dict[tuple[Provider, str], ModelSpec] = {
    (spec.provider, spec.id): spec
    for spec in (
        _spec(Provider.ANTHROPIC, "claude-haiku-5-5", "0.10", "0.50"),
        _spec(Provider.ANTHROPIC, "claude-sonnet-5-5", "2", "10"),
        _spec(Provider.ANTHROPIC, "claude-opus-5-5", "4", "20"),
        _spec(Provider.OPENAI, "gpt-6-luna", "0.10", "0.50"),
        _spec(Provider.OPENAI, "gpt-6-sol", "2", "10"),
    )
}


class UnknownModelError(LookupError):
    pass


def get_model_spec(provider: Provider, model_id: str) -> ModelSpec:
    try:
        return MODELS[(provider, model_id)]
    except KeyError:
        raise UnknownModelError(f"{provider!s}/{model_id} is not in the model allowlist") from None


def models_for(provider: Provider) -> list[ModelSpec]:
    return [spec for (spec_provider, _), spec in MODELS.items() if spec_provider == provider]


def cost_microusd(spec: ModelSpec, input_tokens: int, output_tokens: int) -> int:
    """USD per million tokens equals micro-USD per token; rounded up so budgets never under-count."""
    cost = input_tokens * spec.input_usd_per_mtok + output_tokens * spec.output_usd_per_mtok
    return math.ceil(cost)
