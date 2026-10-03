"""Gate evaluator against synthetic curves with known MaxDD / PF (M2 acceptance)."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal as D  # noqa: N817 - fixture brevity
from statistics import NormalDist

import pytest

from okxq.backtest import gates as g
from okxq.backtest.gates import (
    FoldIS,
    GateInputs,
    Perturbation,
    RunSummary,
    Status,
    TrialStats,
    Verdict,
)

#: 60 wins of +3, 40 losses of -2: PF = 180 / 80 = 2.25; top-5 share = 15 / 100.
GOOD_PNLS = [D(3)] * 60 + [D(-2)] * 40


def curve_from_returns(rets: list[str], start: str = "10000") -> list[D]:
    eq = [D(start)]
    for r in rets:
        eq.append(eq[-1] * (1 + D(r)))
    return eq


#: Alternating +0.2% / -0.1%: per-bar Sharpe ~0.33 over 2,000 bars, MaxDD 0.1%.
GOOD_CURVE = curve_from_returns(["0.002", "-0.001"] * 1000)
GOOD = RunSummary.of(GOOD_PNLS, GOOD_CURVE)
FOLDS = (FoldIS(D(2), 50), FoldIS(D("2.5"), 50))
PERTS = (
    Perturbation("lookback", D("0.8"), D("1.9"), D("0.05")),
    Perturbation("lookback", D("1.2"), D("2.1"), D("0.06")),
)
TRIALS = TrialStats(n_trials=40, sharpe_variance=0.0004, chain_head="ab" * 32)
BASE = GateInputs(GOOD, GOOD, FOLDS, PERTS, TRIALS, GOOD, GOOD)


def status(report: g.GateReport, gate: str) -> Status:
    return {o.gate: o.status for o in report.outcomes}[gate]


def test_a_clean_strategy_is_accepted() -> None:
    r = g.evaluate(BASE)
    assert r.verdict is Verdict.ACCEPT, r.outcomes
    assert r.gates_sha256 == g.FROZEN.sha256()
    assert r.trial_chain_head == TRIALS.chain_head
    assert r.n_trials == 40


# --- G-1 ---------------------------------------------------------------------------------


def test_g1_boundary_is_exact() -> None:
    at_limit = RunSummary.of(GOOD_PNLS, [D(1000), D(1100), D(935)])  # 165/1100 = 15%
    over = RunSummary.of(GOOD_PNLS, [D(1000), D(1100), D(934)])  # 15.09%
    assert g.g1(at_limit).status is Status.PASS
    assert g.g1(over).status is Status.FAIL
    r = g.evaluate(replace(BASE, full=over))
    assert r.verdict is Verdict.DISCARD
    assert status(r, "G-1") is Status.FAIL


def test_g1_counts_open_position_drawdown_on_the_mtm_curve() -> None:
    # Every closed trade wins, but the MTM curve dipped 20% while a position was open.
    run = RunSummary.of([D(5)] * 100, [D(100), D(80), D(130)])
    assert g.g1(run).status is Status.FAIL


# --- G-2 / G-3 ---------------------------------------------------------------------------


def test_g2_boundary_and_undefined() -> None:
    exactly = RunSummary.of([D(3)] * 50 + [D(-2)] * 50, GOOD_CURVE)  # 150/100
    assert g.g2(exactly).status is Status.PASS
    below = RunSummary.of([D(3)] * 49 + [D(-2)] * 51, GOOD_CURVE)
    assert g.g2(below).status is Status.FAIL
    no_losers = RunSummary.of([D(1)] * 150, GOOD_CURVE)
    assert g.g2(no_losers).status is Status.INVALID  # never a pass (A-12)


def test_g3_too_few_trades_is_invalid_not_a_pass() -> None:
    few = RunSummary.of(GOOD_PNLS[:99], GOOD_CURVE)
    r = g.evaluate(replace(BASE, full=few))
    assert status(r, "G-3") is Status.INVALID
    assert r.verdict is Verdict.INVALID


# --- G-4 / G-5 ---------------------------------------------------------------------------


def test_g4_pooled_oos_floor() -> None:
    weak = RunSummary.of([D(3)] * 40 + [D(-2)] * 60, GOOD_CURVE)  # 120/120 = 1.0
    assert g.g4(weak).status is Status.FAIL


def test_g5_uses_trade_weighted_is_pf_of_the_selected_config() -> None:
    # IS: (2.0 x 50 + 4.0 x 150) / 200 = 3.5. OOS PF 2.25 -> ratio 0.643 -> pass.
    folds = (FoldIS(D(2), 50), FoldIS(D(4), 150))
    assert g.g5(GOOD, folds).status is Status.PASS
    # IS 5.0: ratio 2.25 / 5 = 0.45 -> overfit.
    assert g.g5(GOOD, (FoldIS(D(5), 100),)).status is Status.FAIL
    assert g.g5(GOOD, (FoldIS(D("4.5"), 100),)).status is Status.PASS  # exactly 0.5
    assert g.g5(GOOD, (FoldIS(None, 100),)).status is Status.INVALID


# --- G-6 ---------------------------------------------------------------------------------


def test_g6_any_perturbation_collapsing_fails() -> None:
    soft = (*PERTS, Perturbation("threshold", D("1.2"), D("1.19"), D("0.05")))
    assert g.g6(soft).status is Status.FAIL
    deep = (*PERTS, Perturbation("threshold", D("0.8"), D(3), D("0.151")))
    assert g.g6(deep).status is Status.FAIL


def test_g6_unperturbable_params_are_reported_not_passed() -> None:
    only = (Perturbation("n", D("0.8"), D(2), D("0.01"), unperturbable=True),)
    assert g.g6(only).status is Status.INVALID
    mixed = g.g6((*PERTS, *only))
    assert mixed.status is Status.PASS
    assert "unperturbable" in mixed.detail


# --- G-7 ---------------------------------------------------------------------------------


def test_g7_concentration() -> None:
    lucky = RunSummary.of([D(100)] + [D(1)] * 59 + [D(-1)] * 40, GOOD_CURVE)
    # top 5 = 104 of net 119 -> 0.87
    assert g.g7(lucky, GOOD).status is Status.FAIL
    assert g.g7(GOOD, lucky).status is Status.FAIL  # applied to pooled OOS too
    assert g.g7(GOOD, GOOD).status is Status.PASS


# --- G-8 ---------------------------------------------------------------------------------


def test_probabilistic_sharpe_matches_a_hand_computation() -> None:
    # N = 1 -> SR0 = 0: the DSR reduces to the PSR.
    rets = [0.002, -0.001] * 50
    sr = (sum(rets) / len(rets)) / math.sqrt(sum((x - 0.0005) ** 2 for x in rets) / (len(rets) - 1))
    # Two-point symmetric distribution: skew 0, kurtosis 1.
    z = sr * math.sqrt(len(rets) - 1) / math.sqrt(1 - 0 * sr + (1 - 1) / 4 * sr * sr)
    assert g.deflated_sharpe_ratio(rets, 1, 0.0) == pytest.approx(NormalDist().cdf(z))


def test_expected_max_sharpe_approximates_the_max_of_n_normals() -> None:
    # E[max of 1000 iid N(0,1)] = 3.2414 (tabulated order statistic).
    assert g.expected_max_sharpe(1000, 1.0) == pytest.approx(3.2414, abs=0.03)
    assert g.expected_max_sharpe(1, 1.0) == 0.0


def test_dsr_falls_as_trials_grow() -> None:
    rets = list(GOOD.returns[:200])
    few = g.deflated_sharpe_ratio(rets, 2, 0.01)
    many = g.deflated_sharpe_ratio(rets, 10_000, 0.01)
    assert few is not None and many is not None
    assert many < few


def test_g8_fails_a_lucky_search() -> None:
    # Per-bar Sharpe ~0.2 over 600 bars: clears G-8 as the only configuration tried
    # (z ~ 4.9), but not as the best of 5,000 with Sharpe s.d. 0.1 (SR0 ~ 0.37).
    meh = RunSummary.of(GOOD_PNLS, curve_from_returns(["0.0015", "-0.001"] * 300))
    searched = TrialStats(5000, 0.01, "cd" * 32)
    assert g.g8(meh, searched).status is Status.FAIL
    assert g.g8(meh, TrialStats(1, 0.0, "cd" * 32)).status is Status.PASS


# --- G-9 ---------------------------------------------------------------------------------


def test_g9_stressed_costs_must_still_clear_the_gates() -> None:
    thin = RunSummary.of([D(3)] * 50 + [D("-2.5")] * 50, GOOD_CURVE)  # PF 1.2 under stress
    r = g.evaluate(replace(BASE, stressed_full=thin))
    assert status(r, "G-9") is Status.FAIL
    assert r.verdict is Verdict.DISCARD
    assert "G-2" in {o.gate for o in r.stressed if o.status is not Status.PASS}


def test_g9_checks_the_oos_gate_under_stress() -> None:
    weak_oos = RunSummary.of([D(3)] * 40 + [D(-2)] * 60, GOOD_CURVE)
    r = g.evaluate(replace(BASE, stressed_oos=weak_oos))
    assert status(r, "G-9") is Status.FAIL


# --- holdout -----------------------------------------------------------------------------


def hold(run: RunSummary, stressed: RunSummary, prov: str = "MEASURED") -> g.GateReport:
    return g.evaluate_holdout(
        run, stressed, fee_provenance=prov, cost_config_sha="c" * 64, sizing_sha="s" * 64
    )


def test_holdout_evaluation() -> None:
    assert hold(GOOD, GOOD).verdict is Verdict.ACCEPT
    few = RunSummary.of(GOOD_PNLS[:10], GOOD_CURVE)
    assert hold(few, few).verdict is Verdict.INVALID


def test_holdout_must_survive_concentration_and_cost_stress() -> None:
    """Checkpoint-3 finding 10: the promotion decision includes G-7 and G-9."""
    lucky = RunSummary.of([D(100)] + [D(1)] * 59 + [D(-1)] * 40, GOOD_CURVE)
    r = hold(lucky, lucky)
    assert r.verdict is Verdict.DISCARD
    assert {o.gate for o in r.outcomes if o.status is Status.FAIL} >= {"G-7"}
    fragile = RunSummary.of([D(3)] * 40 + [D(-2)] * 60, GOOD_CURVE)  # PF 1.0 under stress
    r = hold(GOOD, fragile)
    assert r.verdict is Verdict.DISCARD
    assert {o.gate: o.status for o in r.outcomes}["G-9"] is Status.FAIL


def test_promotion_is_not_evaluable_on_assumed_fees() -> None:
    """Closing-audit finding M-1: a strategy that clears every holdout gate is still not
    promotable when it was priced on a fee schedule nobody measured."""
    r = hold(GOOD, GOOD, prov="UNMEASURED_ASSUMPTION")
    assert r.verdict is Verdict.INVALID
    assert status(r, "PROMOTION-FEES") is Status.INVALID
    assert r.fee_provenance == "UNMEASURED_ASSUMPTION"
    assert r.cost_config_sha == "c" * 64
    assert hold(GOOD, GOOD).verdict is Verdict.ACCEPT
    assert g.FROZEN.holdout_requires_measured_fees is True
