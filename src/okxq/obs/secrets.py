"""Secret registry and redaction (architecture §20.3).

Secrets must never reach a log file or the audit trail. Rather than trusting every call
site to remember that, secret *values* are registered once at startup and redacted
centrally at the logging and audit boundaries.

This is defence in depth, not the primary control: the primary control is that credentials
are never passed into an LLM context and never written to the project tree.
"""

from __future__ import annotations

import threading

REDACTED = "***REDACTED***"

#: Shortest value we will register. Redacting a 1-3 character string would corrupt
#: unrelated log output for no security benefit.
_MIN_SECRET_LEN = 4

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a secret value for redaction. Short or empty values are ignored."""
    if not value or len(value) < _MIN_SECRET_LEN:
        return
    with _lock:
        _secrets.add(value)


def clear_secrets() -> None:
    """Forget all registered secrets. For test isolation only."""
    with _lock:
        _secrets.clear()


def registered_count() -> int:
    """Number of registered secrets. Never returns the values themselves."""
    with _lock:
        return len(_secrets)


def redact(text: str) -> str:
    """Replace every registered secret occurrence in ``text``.

    Longest-first so that a secret which is a substring of another is not partially
    replaced, leaving a fragment of the longer one exposed.
    """
    with _lock:
        ordered = sorted(_secrets, key=len, reverse=True)
    for secret in ordered:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text
