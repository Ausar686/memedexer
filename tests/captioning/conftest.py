import pytest

from memedexer.captioning.base import Image
from memedexer.captioning.catalog import ModelSpec, get_model_spec
from memedexer.config import Provider


@pytest.fixture
def image() -> Image:
    return Image(b"\x89PNG fake", "image/png")


@pytest.fixture
def haiku() -> ModelSpec:
    return get_model_spec(Provider.ANTHROPIC, "claude-haiku-5-5")


@pytest.fixture
def luna() -> ModelSpec:
    return get_model_spec(Provider.OPENAI, "gpt-6-luna")
