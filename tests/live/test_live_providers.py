"""Calls the real provider APIs; run with `uv run pytest -m live` and keys in `.env`."""

import pathlib

import pytest

from memedexer.captioning.base import Image
from memedexer.captioning.catalog import cost_microusd, get_model_spec
from memedexer.captioning.registry import build_providers
from memedexer.config import Provider, Settings

pytestmark = pytest.mark.live

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "meme.png"
LIVE_MODELS = [(Provider.ANTHROPIC, "claude-haiku-5-5"), (Provider.OPENAI, "gpt-6-luna")]


@pytest.mark.parametrize(("provider", "model_id"), LIVE_MODELS)
async def test_captions_fixture_meme(provider: Provider, model_id: str) -> None:
    settings = Settings()
    if provider not in settings.available_providers:
        pytest.skip(f"no API key for {provider!s}")
    spec = get_model_spec(provider, model_id)

    result = await build_providers(settings)[provider].caption(
        Image(FIXTURE.read_bytes(), "image/png"), model=spec, description_language=None
    )

    normalized = " ".join(result.text.upper().split())
    assert "WHEN ALL TESTS PASS" in normalized
    assert "ON THE FIRST TRY" in normalized
    assert result.languages == ["en"]
    assert len(result.tags) <= 4
    assert result.usage.input_tokens > 0
    cost = cost_microusd(spec, result.usage.input_tokens, result.usage.output_tokens)
    print(f"\n{provider}/{model_id}: {result} cost={cost}µ$")
