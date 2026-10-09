"""Effective captioning settings for a topic: topic → chat → env default."""

import dataclasses
import logging

from memedexer.captioning.catalog import ModelSpec, UnknownModelError, get_model_spec
from memedexer.config import Provider, Settings
from memedexer.storage.models import Chat, Topic

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CaptionSettings:
    model: ModelSpec
    description_language: str | None


def _override(level: Chat | Topic | None) -> ModelSpec | None:
    if level is None or level.provider is None or level.model is None:
        return None
    try:
        return get_model_spec(Provider(level.provider), level.model)
    except (ValueError, UnknownModelError):
        # A model dropped from the allowlist after being selected; fall through rather than fail every job.
        log.warning("ignoring non-allowlisted override %s/%s", level.provider, level.model)
        return None


def resolve_caption_settings(chat: Chat, topic: Topic | None, settings: Settings) -> CaptionSettings:
    model = _override(topic) or _override(chat) or get_model_spec(settings.default_provider, settings.default_model)
    return CaptionSettings(model=model, description_language=chat.description_language)
