"""Guard: the acceptance gates are FROZEN (roadmap M2 -> M4 sequencing constraint).

A strategy must never be helped through by moving a threshold or redefining a gate. Changing
anything in :class:`okxq.backtest.gates.FrozenGates` fails this test. Updating the pin is a
deliberate act that belongs in its own reviewed commit stating why - never in a commit that
also adds or tunes a strategy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.backtest.gates import FROZEN, FrozenGates

pytestmark = pytest.mark.guard

#: SHA-256 of FrozenGates().canonical_json(), frozen at M2 (2026-10-03).
PINNED_SHA256 = "73cc903097ea6a44702be87481d96002c1dc36369a30e2b3d5d2088e34774ab2"


def test_gate_definitions_match_the_frozen_pin() -> None:
    assert FROZEN.sha256() == PINNED_SHA256, FROZEN.canonical_json()


def test_mandated_thresholds_are_the_mandated_values() -> None:
    assert Decimal("0.15") == FROZEN.g1_max_drawdown
    assert Decimal("1.5") == FROZEN.g2_min_profit_factor
    assert FROZEN.g3_min_trades == 100
    assert Decimal("1.2") == FROZEN.g4_min_oos_profit_factor
    assert Decimal("0.5") == FROZEN.g5_min_oos_to_is_ratio
    assert Decimal("0.20") == FROZEN.g6_perturbation
    assert (FROZEN.g7_top_n, FROZEN.g7_max_share) == (5, Decimal("0.5"))
    assert FROZEN.g8_min_dsr == 0.95


def test_gates_cannot_be_mutated_at_runtime() -> None:
    with pytest.raises(AttributeError):
        FROZEN.g1_max_drawdown = Decimal("0.5")  # type: ignore[misc]


def test_the_evaluator_defaults_to_the_frozen_instance() -> None:
    import inspect

    from okxq.backtest import gates

    for fn in (
        gates.g1,
        gates.g2,
        gates.g3,
        gates.g4,
        gates.g5,
        gates.g6,
        gates.g7,
        gates.g8,
        gates.evaluate,
        gates.evaluate_holdout,
    ):
        default = inspect.signature(fn).parameters["g"].default
        assert default is FROZEN
        assert isinstance(default, FrozenGates)
