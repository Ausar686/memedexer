import logging

import pydantic
import pytest

from memedexer import redaction
from memedexer.config import Settings
from memedexer.redaction import RedactingFilter, redact


def test_redact_masks_every_secret_and_skips_empty_ones() -> None:
    assert redact("token 1:A and key sk-B", ["1:A", "", "sk-B"]) == "token <redacted> and key <redacted>"


def record(message: str, *args: object, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord("x", logging.ERROR, __file__, 1, message, args, exc_info)


def test_filter_masks_formatted_messages() -> None:
    entry = record("download of %s failed", "https://api.telegram.org/file/bot1:A/x")

    RedactingFilter(["1:A"]).filter(entry)

    assert entry.getMessage() == "download of https://api.telegram.org/file/bot<redacted>/x failed"


def test_filter_masks_tracebacks() -> None:
    try:
        raise RuntimeError("url bot1:A/x")
    except RuntimeError as exc:
        entry = record("crashed", exc_info=(type(exc), exc, exc.__traceback__))

    RedactingFilter(["1:A"]).filter(entry)

    assert "1:A" not in logging.Formatter().format(entry)
    assert "RuntimeError: url bot<redacted>/x" in entry.exc_text


def test_install_redacts_on_root_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = logging.Handler()
    seen: list[str] = []
    handler.emit = lambda entry: seen.append(entry.getMessage())
    monkeypatch.setattr(logging.getLogger(), "handlers", [handler])

    redaction.install(["1:A"])
    logging.getLogger("aiogram.dispatcher").error("token %s", "1:A")

    assert seen == ["token <redacted>"]


def test_settings_list_every_configured_secret(settings: Settings) -> None:
    settings.openai_api_key = pydantic.SecretStr("sk-oai")
    settings.database_url = pydantic.SecretStr("postgresql+asyncpg://bot:db-pass@db/memedexer")

    assert settings.secret_values == ["42:TEST", "sk-ant", "sk-oai", "db-pass"]
