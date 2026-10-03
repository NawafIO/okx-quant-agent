"""Guard: the risk policy is FROZEN (roadmap M5, architecture §13.1 "no bypass").

Changing any threshold, the cluster map or the required-check set fails this test. Moving
the pin is a reviewed commit stating why - never part of a strategy or research change.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.risk.policy import (
    FROZEN_RISK_POLICY,
    PINNED_RISK_POLICY_SHA256,
    RiskPolicy,
    RiskPolicyTamperError,
)

pytestmark = pytest.mark.guard

#: SHA-256 of RiskPolicy().canonical_json(), frozen at M5 (2026-10-03).
PINNED_SHA256 = "b7bcb28b382b82d2f24d39a4871ede355d759907ab0a079729ff83f739a5a376"


def test_risk_policy_matches_the_frozen_pin() -> None:
    assert FROZEN_RISK_POLICY.sha256() == PINNED_SHA256 == PINNED_RISK_POLICY_SHA256


def test_architecture_defaults_are_the_specified_values() -> None:
    p = FROZEN_RISK_POLICY
    assert Decimal("0.005") == p.max_risk_per_trade  # RC-05
    assert Decimal("0.03") == p.max_portfolio_heat  # RC-06
    assert Decimal("0.015") == p.cluster_cap  # RC-07
    assert p.max_positions == 5  # RC-08
    assert Decimal("0.02") == p.daily_loss_limit  # RC-10
    assert Decimal("0.10") == p.max_dd_halt  # RC-11
    assert p.max_leverage == 3  # RC-12


def test_risk_policy_refuses_other_values() -> None:
    with pytest.raises(RiskPolicyTamperError):
        RiskPolicy(max_risk_per_trade=Decimal("0.01"))
    with pytest.raises(RiskPolicyTamperError):
        RiskPolicy(required_checks=("RC-01",))
    with pytest.raises(RiskPolicyTamperError):
        RiskPolicy(clusters=(("BTC-USDT-SWAP", "OTHER"),))


def test_risk_policy_cannot_be_mutated() -> None:
    with pytest.raises(AttributeError):
        FROZEN_RISK_POLICY.max_leverage = 10  # type: ignore[misc]


def test_research_sizing_risk_equals_the_policy_risk() -> None:
    from okxq.backtest.sizing import RESEARCH_SIZING

    assert RESEARCH_SIZING.risk_fraction == FROZEN_RISK_POLICY.max_risk_per_trade
    assert RESEARCH_SIZING.leverage == FROZEN_RISK_POLICY.max_leverage
