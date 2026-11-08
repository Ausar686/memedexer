"""Keep secret values out of text that leaves the process: logs, stored errors and owner DMs."""

import collections.abc as cabc
import logging

MASK = "<redacted>"


def redact(text: str, secrets: cabc.Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    return text


class RedactingFilter(logging.Filter):
    """Masks secrets in a record's message and traceback; attach it to handlers, which see every logger's records."""

    def __init__(self, secrets: cabc.Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self._secrets)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, self._secrets)
        return True


def install(secrets: cabc.Iterable[str]) -> None:
    """Redact on every handler the root logger has; call after `logging.basicConfig`."""
    log_filter = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(log_filter)
    return None
