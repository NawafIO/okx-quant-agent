"""Guard: the acceptance gates are FROZEN (roadmap M2 -> M4 sequencing constraint).

A strategy must never be helped through by moving a threshold or redefining a gate. Changing
anything in :class:`okxq.backtest.gates.FrozenGates` fails this test. Updating the pin is a
deliberate act that belongs in its own reviewed commit stating why - never in a commit that
also adds or tunes a strategy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.backtest.gates import FROZEN, PINNED_GATES_SHA256, FrozenGates, GateTamperError

pytestmark = pytest.mark.guard

#: SHA-256 of FrozenGates().canonical_json(), frozen at M2 (2026-10-03).
PINNED_SHA256 = "9066dca9f14b4003f8b45658c89e6761b40700b5d04cd5b23c2e816290d52b79"


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


def test_a_lenient_copy_of_the_gates_refuses_to_exist() -> None:
    """Checkpoint-3 finding 2: constructing different thresholds must fail, not just
    mutating the frozen instance."""
    with pytest.raises(GateTamperError):
        FrozenGates(g2_min_profit_factor=Decimal("1.0"))
    with pytest.raises(GateTamperError):
        FrozenGates(holdout_start_utc="2099-01-01T00:00:00+00:00")
    assert FrozenGates() == FROZEN


def test_the_in_code_pin_matches_this_pin() -> None:
    assert PINNED_GATES_SHA256 == PINNED_SHA256


@pytest.mark.parametrize("module", ["gates", "holdout", "walkforward"])
def test_no_public_function_accepts_replacement_thresholds(module: str) -> None:
    """Nothing a researcher calls may take the gates (or the holdout boundary) as an
    argument - the frozen instance is bound inside, never passed in."""
    import importlib
    import inspect

    mod = importlib.import_module(f"okxq.backtest.{module}")
    for name, obj in vars(mod).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != mod.__name__:
            continue
        callables = [obj]
        if inspect.isclass(obj):
            callables = [getattr(obj, "__init__")] + [  # noqa: B009
                f for n, f in vars(obj).items() if callable(f) and not n.startswith("__")
            ]
        for fn in callables:
            if not callable(fn) or (inspect.isclass(fn) and fn is not obj):
                continue
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                continue
            for p in params.values():
                assert "FrozenGates" not in str(p.annotation), f"{module}.{name}: {p.name}"
                assert p.name not in {"g", "gates", "thresholds"}, f"{module}.{name}: {p.name}"


# --- research sizing (Chief Advisor, M4 checkpoint 1): sizing is a G-1 lever ---------------

#: SHA-256 of ResearchSizing().canonical_json(), frozen before the first M4 run (2026-10-03).
PINNED_SIZING_SHA256 = "e32046ed9bbc3b33e7a40d1ada9b0d6751d2b7b7ba132594f4f86e8f68908353"


def test_research_sizing_matches_the_frozen_pin() -> None:
    from okxq.backtest.sizing import PINNED_RESEARCH_SIZING_SHA256, RESEARCH_SIZING

    assert RESEARCH_SIZING.sha256() == PINNED_SIZING_SHA256 == PINNED_RESEARCH_SIZING_SHA256
    assert Decimal("0.005") == RESEARCH_SIZING.risk_fraction  # RC-05
    assert Decimal(3) == RESEARCH_SIZING.leverage  # RC-12


def test_research_sizing_refuses_other_values() -> None:
    from okxq.backtest.sizing import ResearchSizing, SizingTamperError

    with pytest.raises(SizingTamperError):
        ResearchSizing(risk_fraction=Decimal("0.0025"))


def test_research_protocol_does_not_accept_a_sizer() -> None:
    import inspect

    from okxq.backtest.walkforward import ResearchProtocol

    assert "sizer" not in inspect.signature(ResearchProtocol.__init__).parameters
