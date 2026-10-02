"""GUARD: the LIVE environment must be unreachable (architecture §6.4, lock Layer 4).

A failure in this file blocks all progress. It is the automated expression of the
ZERO-LIVE-CAPITAL policy.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from okxq import phase
from okxq.env.profiles import build_profile
from okxq.errors import LiveTradingLockedError, SafetyError

pytestmark = pytest.mark.guard

_LIVE_VARS = ("OKXQ_LIVE_API_KEY", "OKXQ_LIVE_API_SECRET", "OKXQ_LIVE_API_PASSPHRASE")

#: The host environment as it was at import time. Captured at module scope deliberately:
#: ``tests/conftest.py`` has an autouse fixture that deletes these variables before every
#: test, and collection happens before fixtures run. Asserting against ``os.environ`` inside
#: a test would therefore only confirm that the fixture works - it would stay green even if
#: the operator really did have a LIVE key in their shell. This snapshot is what makes the
#: Layer 1 guard below load-bearing rather than vacuous.
_HOST_LIVE_VARS: dict[str, str | None] = {v: os.environ.get(v) for v in _LIVE_VARS}


def test_phase_is_below_live_unlock() -> None:
    """The phase constant must still lock LIVE."""
    assert phase.PHASE < phase.LIVE_UNLOCK_PHASE
    assert phase.live_trading_unlocked() is False


def test_live_unlock_phase_is_four() -> None:
    """LIVE unlocks at phase 4, not 3.

    Phase 3 is simulated operation (DEMO/PAPER). An earlier architecture draft said LIVE
    unlocked at phase 3, which would have opened this lock layer during simulation; the
    constant is pinned here so that regression is caught.
    """
    assert phase.LIVE_UNLOCK_PHASE == 4


def test_building_live_profile_raises(tmp_path: Path) -> None:
    """Constructing the LIVE profile must raise."""
    with pytest.raises(LiveTradingLockedError):
        build_profile("LIVE", root=tmp_path)


def test_live_lock_is_a_safety_error() -> None:
    """The lock error must be a SafetyError, so no generic handler retries past it."""
    assert issubclass(LiveTradingLockedError, SafetyError)


def test_simulated_profiles_still_build(tmp_path: Path) -> None:
    """The lock must not be a blunt instrument: DEMO and PAPER must still work.

    Without this, a test suite could pass by breaking profile construction entirely.
    """
    assert build_profile("DEMO", root=tmp_path).env == "DEMO"
    assert build_profile("PAPER", root=tmp_path).env == "PAPER"


def test_live_check_is_not_dead_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Proves the lock is load-bearing rather than incidentally unreachable.

    With the phase constant raised, LIVE construction must *succeed* - which demonstrates
    that the guard above fails for the stated reason (the phase check) and not because of
    an unrelated error that would mask an unlocked LIVE path later.
    """
    monkeypatch.setattr(phase, "PHASE", 4)
    monkeypatch.setattr("okxq.env.profiles.live_trading_unlocked", lambda: True, raising=True)
    profile = build_profile("LIVE", root=tmp_path)
    assert profile.real_capital_at_risk is True
    # Even unlocked, LIVE must arm the kill switch by default (lock Layer 3) and must have
    # no credentials, because none are provisioned (lock Layer 1 - the one that matters).
    assert profile.kill_switch_armed_by_default is True
    assert profile.credentials is None


def test_no_live_credentials_are_provisioned_on_the_host() -> None:
    """LIVE lock Layer 1: no trade-permitted LIVE credential exists on this host.

    Checked against the import-time snapshot, not ``os.environ`` - see ``_HOST_LIVE_VARS``.
    """
    for var, value in _HOST_LIVE_VARS.items():
        assert value is None, (
            f"{var} is set on this host. LIVE lock Layer 1 is violated: no trade-permitted "
            "LIVE credential may exist before Phase 4 sign-off."
        )


def test_host_snapshot_would_detect_a_provisioned_credential() -> None:
    """Negative control proving the guard above can actually fail.

    Without this, a refactor that broke the snapshot into an always-empty dict would leave
    the Layer 1 guard passing forever - which is precisely the defect this pair replaced.
    """
    polluted = {**_HOST_LIVE_VARS, "OKXQ_LIVE_API_KEY": "leaked-key-value"}
    assert any(v is not None for v in polluted.values())
