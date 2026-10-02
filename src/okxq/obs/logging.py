"""Structured JSON logging with secret redaction (architecture §20.2).

Operational logs are for debuggability and are distinct from the audit trail
(:mod:`okxq.audit.chain`), which is append-only and hash-chained. Do not conflate them: a
log line can be rotated away, an audit record cannot.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from okxq.obs.secrets import redact


class RedactingJsonFormatter(logging.Formatter):
    """Render records as single-line JSON with every registered secret redacted.

    Redaction is applied to the rendered message *and* to every extra field, because a
    secret leaks just as readily through a structured field as through a format string.
    """

    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        rendered = json.dumps(payload, default=str, ensure_ascii=False)
        return redact(rendered)


def configure_logging(level: int = logging.INFO, *, stream: Any = None) -> None:
    """Install the redacting JSON formatter on the root logger.

    Idempotent: replaces existing handlers rather than stacking duplicates, so calling it
    twice does not double every log line.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(RedactingJsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
