"""GUARD: a registered secret must never appear in a log line or an audit record.

Architecture §20.2/§20.3. This is defence in depth - the primary control is that
credentials are never written to the project tree and never enter an LLM context.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from okxq.audit.chain import AuditChain, verify_chain
from okxq.obs.logging import configure_logging, get_logger
from okxq.obs.secrets import REDACTED, clear_secrets, redact, register_secret

pytestmark = pytest.mark.guard

DUMMY_SECRET = "sk-test-DO-NOT-LOG-8f3a9c21"


@pytest.fixture(autouse=True)
def _isolate_secrets() -> Iterator[None]:
    clear_secrets()
    yield
    clear_secrets()


def test_secret_absent_from_log_message() -> None:
    register_secret(DUMMY_SECRET)
    stream = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=stream)
    get_logger("t").info("authenticating with %s", DUMMY_SECRET)

    output = stream.getvalue()
    assert DUMMY_SECRET not in output
    assert REDACTED in output


def test_secret_absent_from_structured_extra_field() -> None:
    """A secret leaks just as readily through a structured field as a format string."""
    register_secret(DUMMY_SECRET)
    stream = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=stream)
    get_logger("t").info("request", extra={"api_key": DUMMY_SECRET, "venue": "okx"})

    output = stream.getvalue()
    assert DUMMY_SECRET not in output
    assert "okx" in output  # non-secret context is preserved


def test_secret_absent_from_exception_traceback() -> None:
    register_secret(DUMMY_SECRET)
    stream = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=stream)
    try:
        raise ValueError(f"auth failed for {DUMMY_SECRET}")
    except ValueError:
        get_logger("t").exception("boom")

    assert DUMMY_SECRET not in stream.getvalue()


def test_secret_absent_from_audit_chain(tmp_path: Path) -> None:
    """The audit trail is permanent, so a secret reaching it is unrecoverable."""
    register_secret(DUMMY_SECRET)
    path = tmp_path / "audit.jsonl"
    chain = AuditChain(path)
    chain.append("auth", {"api_key": DUMMY_SECRET, "env": "DEMO"})

    raw = path.read_text(encoding="utf-8")
    assert DUMMY_SECRET not in raw
    assert REDACTED in raw


def test_redacted_record_still_verifies(tmp_path: Path) -> None:
    """Redaction must not be mistakable for tampering.

    The hash is computed over the redacted payload, so a redacted chain verifies cleanly.
    """
    register_secret(DUMMY_SECRET)
    path = tmp_path / "audit.jsonl"
    chain = AuditChain(path)
    chain.append("auth", {"api_key": DUMMY_SECRET})
    chain.append("boot", {"env": "DEMO"})

    assert verify_chain(path) == 2


def test_overlapping_secrets_fully_redacted() -> None:
    """A secret that is a substring of another must not leave a fragment exposed."""
    register_secret("abc123")
    register_secret("abc123456789")
    assert "abc123" not in redact("token=abc123456789")


def test_short_values_are_not_registered() -> None:
    """Redacting a 1-3 char string would corrupt unrelated output for no benefit."""
    register_secret("ab")
    register_secret("")
    register_secret(None)
    assert redact("a tab in a cab") == "a tab in a cab"


def test_unregistered_secret_is_not_redacted() -> None:
    """Negative control: redaction works by registration, so the test above is meaningful."""
    assert DUMMY_SECRET in redact(f"key={DUMMY_SECRET}")
