"""Shared test fixtures.

Note the deliberate absence of any fixture that provisions LIVE credentials or unlocks the
phase constant globally. Tests that need an unlocked LIVE path monkeypatch it locally and
within a single test, so the lock is never relaxed suite-wide.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from okxq.obs.secrets import clear_secrets


@pytest.fixture(autouse=True)
def _no_live_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no LIVE credential is visible to any test (ZERO-LIVE-CAPITAL)."""
    for var in ("OKXQ_LIVE_API_KEY", "OKXQ_LIVE_API_SECRET", "OKXQ_LIVE_API_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clean_secret_registry() -> Iterator[None]:
    """Keep the process-wide secret registry from leaking between tests."""
    yield
    clear_secrets()
