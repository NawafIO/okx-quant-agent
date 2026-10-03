"""Model acceptance gates G-1..G-9 (architecture §11.3) - FROZEN before any strategy exists.

Thresholds AND the exact definitions behind them are fixed here, at M2, so that no strategy
can later be helped through by moving a threshold or quietly redefining what a gate measures
(roadmap: "frozen before M4 begins"). The canonical form of :data:`FROZEN` is hashed and the
hash is pinned by a guard test: changing anything below fails the build until the pin is
deliberately updated in a reviewed commit.

Definitions were ruled by the Chief Advisor at M2 checkpoint 1 (2026-10-03), recorded in
``docs/M2_DESIGN.md``. The evaluator is a pure function of already-computed artefacts; it
runs no backtests and reads no data.

Statuses: ``PASS``; ``FAIL`` (the strategy is DISCARDED); ``INVALID`` (not evaluable - e.g.
too few trades, or a statistic that is undefined; never treated as a pass).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from statistics import NormalDist

from okxq.backtest import metrics as m
from okxq.backtest.types import G9_STRESS

EULER_GAMMA = 0.5772156649015329


@dataclass(frozen=True)
class FrozenGates:
    # G-1 MaxDD on the MTM equity curve of every bar close, open positions included (A-8).
    g1_max_drawdown: Decimal = Decimal("0.15")  # observed > limit -> FAIL
    # G-2 profit factor of closed position lifecycles (A-2). Undefined PF -> INVALID (A-12).
    g2_min_profit_factor: Decimal = Decimal("1.5")  # observed < limit -> FAIL
    # G-3 closed position lifecycles, not fills.
    g3_min_trades: int = 100  # observed < limit -> INVALID
    # G-4 PF of trades POOLED across every walk-forward OOS fold.
    g4_min_oos_profit_factor: Decimal = Decimal("1.2")
    # G-5 pooled OOS PF / trade-count-weighted mean of per-fold IS PF of the SELECTED config.
    # (Not pooled IS: 12m windows stepped 3m overlap, so pooling double-counts trades.)
    g5_min_oos_to_is_ratio: Decimal = Decimal("0.5")
    # G-6 every numeric parameter x(1 -/+ 0.20), one at a time; each perturbed config run
    # fixed (no re-selection) over the OOS span must keep PF >= floor AND G-1's MaxDD.
    g6_perturbation: Decimal = Decimal("0.20")
    g6_min_profit_factor: Decimal = Decimal("1.2")
    g6_max_drawdown: Decimal = Decimal("0.15")
    # G-7 share of net profit from the top-N lifecycles, on full-period AND pooled OOS trades.
    g7_top_n: int = 5
    g7_max_share: Decimal = Decimal("0.5")
    # G-8 Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) of the pooled OOS per-bar
    # returns, deflated by N = every trial in the trial log and V = their Sharpe variance.
    g8_min_dsr: float = 0.95
    # G-9 the whole set re-evaluated under G9_STRESS must still pass G-1, G-2, G-3, G-4, G-7.
    g9_stress: str = (
        f"fees x{G9_STRESS.fees}, slippage x{G9_STRESS.slippage}, "
        f"funding paid x{G9_STRESS.funding_paid}, funding received x{G9_STRESS.funding_received}"
    )
    g9_must_pass: tuple[str, ...] = ("G-1", "G-2", "G-3", "G-4", "G-7")
    # Walk-forward protocol (researcher degrees of freedom, frozen with the gates).
    wf_train_months: int = 12
    wf_test_months: int = 3
    wf_step_months: int = 3
    wf_selection_objective: str = "max in-sample annualised Sharpe of the MTM curve"
    wf_min_is_trades_for_selection: int = 30
    wf_flat_at_fold_boundary: bool = True
    # Sealed holdout: open-ended, so archives appended later stay sealed too (A-3).
    holdout_start_utc: str = "2025-10-01T00:00:00+00:00"
    holdout_must_pass: tuple[str, ...] = ("G-1", "G-3", "G-4")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def holdout_start_ms(self) -> int:
        return (
            int(datetime.fromisoformat(self.holdout_start_utc).astimezone(UTC).timestamp()) * 1000
        )


FROZEN = FrozenGates()


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105 - a gate status, not a credential
    FAIL = "FAIL"
    INVALID = "INVALID"


class Verdict(StrEnum):
    ACCEPT = "ACCEPT"
    DISCARD = "DISCARD"
    INVALID = "INVALID"


@dataclass(frozen=True)
class GateOutcome:
    gate: str
    status: Status
    observed: str
    limit: str
    detail: str = ""


# --- inputs ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    """The two things a gate reads from a run: closed-lifecycle P&Ls and the MTM curve."""

    pnls: tuple[Decimal, ...]
    equity: tuple[Decimal, ...]
    #: Per-bar simple returns of the MTM curve (for Sharpe / DSR).
    returns: tuple[float, ...] = ()

    @classmethod
    def of(cls, pnls: Sequence[Decimal], equity: Sequence[Decimal]) -> RunSummary:
        return cls(tuple(pnls), tuple(equity), tuple(m.simple_returns(list(equity))))


@dataclass(frozen=True)
class FoldIS:
    """In-sample result of the configuration SELECTED in one walk-forward fold."""

    profit_factor: Decimal | None
    n_trades: int


@dataclass(frozen=True)
class Perturbation:
    param: str
    factor: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    #: True when x(1 +/- 0.2) rounds back to the same value (small integers): reported, and
    #: excluded from the gate rather than counted as a pass.
    unperturbable: bool = False


@dataclass(frozen=True)
class TrialStats:
    """Read from the trial log (see :mod:`okxq.backtest.trials`)."""

    n_trials: int
    sharpe_variance: float
    chain_head: str


@dataclass(frozen=True)
class GateInputs:
    full: RunSummary
    oos: RunSummary
    is_folds: tuple[FoldIS, ...]
    perturbations: tuple[Perturbation, ...]
    trials: TrialStats
    stressed_full: RunSummary
    stressed_oos: RunSummary


@dataclass(frozen=True)
class GateReport:
    outcomes: tuple[GateOutcome, ...]
    verdict: Verdict
    gates_sha256: str
    trial_chain_head: str
    n_trials: int
    stressed: tuple[GateOutcome, ...] = field(default=())

    def failed(self) -> list[str]:
        return [o.gate for o in self.outcomes if o.status is not Status.PASS]


# --- statistics --------------------------------------------------------------------------


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """SR0: the Sharpe the best of ``n_trials`` skill-less trials is expected to reach.

    ``sqrt(V) * ((1 - g) * Z^-1(1 - 1/N) + g * Z^-1(1 - 1/(N e)))``, g = Euler-Mascheroni.
    Zero for fewer than two trials (the DSR then reduces to the probabilistic Sharpe ratio).
    """
    if n_trials < 2 or sharpe_variance <= 0:
        return 0.0
    z = NormalDist().inv_cdf
    return math.sqrt(sharpe_variance) * (
        (1 - EULER_GAMMA) * z(1 - 1 / n_trials) + EULER_GAMMA * z(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe_ratio(
    returns: Sequence[float], n_trials: int, sharpe_variance: float
) -> float | None:
    """Probability that the true per-period Sharpe exceeds SR0, corrected for skew, fat
    tails and track-record length. ``None`` when any input moment is undefined."""
    sr = m.per_period_sharpe(returns)
    skew = m.skewness(returns)
    kurt = m.kurtosis(returns)
    if sr is None or skew is None or kurt is None or len(returns) < 2:
        return None
    var_term = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if var_term <= 0:
        return None
    sr0 = expected_max_sharpe(n_trials, sharpe_variance)
    return NormalDist().cdf((sr - sr0) * math.sqrt(len(returns) - 1) / math.sqrt(var_term))


# --- individual gates --------------------------------------------------------------------


def _fmt(x: object) -> str:
    return "undefined" if x is None else str(x)


def g1(run: RunSummary, g: FrozenGates = FROZEN) -> GateOutcome:
    if len(run.equity) < 2:
        return GateOutcome("G-1", Status.INVALID, "no equity curve", f"<= {g.g1_max_drawdown}")
    dd = m.max_drawdown(list(run.equity))
    ok = dd <= g.g1_max_drawdown
    return GateOutcome(
        "G-1", Status.PASS if ok else Status.FAIL, str(dd), f"<= {g.g1_max_drawdown}"
    )


def g2(run: RunSummary, g: FrozenGates = FROZEN) -> GateOutcome:
    pf = m.profit_factor(list(run.pnls))
    if pf is None:
        return GateOutcome(
            "G-2",
            Status.INVALID,
            "undefined",
            f">= {g.g2_min_profit_factor}",
            "no losing trades or no trades: PF is unbounded, not passing",
        )
    ok = pf >= g.g2_min_profit_factor
    return GateOutcome(
        "G-2", Status.PASS if ok else Status.FAIL, str(pf), f">= {g.g2_min_profit_factor}"
    )


def g3(run: RunSummary, g: FrozenGates = FROZEN) -> GateOutcome:
    n = len(run.pnls)
    ok = n >= g.g3_min_trades
    return GateOutcome(
        "G-3", Status.PASS if ok else Status.INVALID, str(n), f">= {g.g3_min_trades}"
    )


def g4(oos: RunSummary, g: FrozenGates = FROZEN) -> GateOutcome:
    pf = m.profit_factor(list(oos.pnls))
    if pf is None:
        return GateOutcome("G-4", Status.INVALID, "undefined", f">= {g.g4_min_oos_profit_factor}")
    ok = pf >= g.g4_min_oos_profit_factor
    return GateOutcome(
        "G-4", Status.PASS if ok else Status.FAIL, str(pf), f">= {g.g4_min_oos_profit_factor}"
    )


def g5(oos: RunSummary, folds: Sequence[FoldIS], g: FrozenGates = FROZEN) -> GateOutcome:
    limit = f">= {g.g5_min_oos_to_is_ratio} x IS"
    oos_pf = m.profit_factor(list(oos.pnls))
    weighted = [(f.profit_factor, f.n_trades) for f in folds if f.n_trades > 0]
    if oos_pf is None or not weighted or any(pf is None for pf, _ in weighted):
        return GateOutcome(
            "G-5", Status.INVALID, "undefined", limit, "an IS or OOS profit factor is undefined"
        )
    total = sum(n for _, n in weighted)
    is_pf = sum((pf * n for pf, n in weighted if pf is not None), Decimal(0)) / total
    ratio = oos_pf / is_pf if is_pf > 0 else Decimal(0)
    ok = ratio >= g.g5_min_oos_to_is_ratio
    return GateOutcome(
        "G-5", Status.PASS if ok else Status.FAIL, f"OOS {oos_pf} / IS {is_pf} = {ratio}", limit
    )


def g6(perts: Sequence[Perturbation], g: FrozenGates = FROZEN) -> GateOutcome:
    limit = (
        f"PF >= {g.g6_min_profit_factor} and MaxDD <= {g.g6_max_drawdown} "
        f"for every +/-{g.g6_perturbation}"
    )
    live = [p for p in perts if not p.unperturbable]
    if not live:
        return GateOutcome("G-6", Status.INVALID, "no perturbable parameter", limit)
    bad = [
        p
        for p in live
        if p.profit_factor is None
        or p.profit_factor < g.g6_min_profit_factor
        or p.max_drawdown > g.g6_max_drawdown
    ]
    detail = "; ".join(
        f"{p.param} x{p.factor}: PF {_fmt(p.profit_factor)} DD {p.max_drawdown}" for p in bad
    )
    skipped = [p.param for p in perts if p.unperturbable]
    if skipped:
        detail = (detail + "; " if detail else "") + f"unperturbable: {sorted(set(skipped))}"
    return GateOutcome(
        "G-6",
        Status.FAIL if bad else Status.PASS,
        f"{len(live) - len(bad)}/{len(live)} held",
        limit,
        detail,
    )


def g7(full: RunSummary, oos: RunSummary, g: FrozenGates = FROZEN) -> GateOutcome:
    limit = f"top {g.g7_top_n} <= {g.g7_max_share} of net profit"
    shares = [m.top_n_share(list(r.pnls), g.g7_top_n) for r in (full, oos)]
    if any(s is None for s in shares):
        return GateOutcome("G-7", Status.INVALID, "net profit not positive", limit)
    worst = max(s for s in shares if s is not None)
    return GateOutcome(
        "G-7",
        Status.PASS if worst <= g.g7_max_share else Status.FAIL,
        f"full {shares[0]}, OOS {shares[1]}",
        limit,
    )


def g8(oos: RunSummary, trials: TrialStats, g: FrozenGates = FROZEN) -> GateOutcome:
    dsr = deflated_sharpe_ratio(list(oos.returns), trials.n_trials, trials.sharpe_variance)
    limit = f">= {g.g8_min_dsr} (N={trials.n_trials})"
    if dsr is None:
        return GateOutcome("G-8", Status.INVALID, "undefined", limit)
    return GateOutcome(
        "G-8", Status.PASS if dsr >= g.g8_min_dsr else Status.FAIL, f"{dsr:.6f}", limit
    )


def _verdict(outcomes: Sequence[GateOutcome]) -> Verdict:
    if any(o.status is Status.FAIL for o in outcomes):
        return Verdict.DISCARD
    if any(o.status is Status.INVALID for o in outcomes):
        return Verdict.INVALID
    return Verdict.ACCEPT


def evaluate(inputs: GateInputs, g: FrozenGates = FROZEN) -> GateReport:
    """Evaluate G-1..G-9. Any FAIL discards; any INVALID makes the strategy not evaluable."""
    stressed = tuple(
        o
        for o in (
            g1(inputs.stressed_full, g),
            g2(inputs.stressed_full, g),
            g3(inputs.stressed_full, g),
            g4(inputs.stressed_oos, g),
            g7(inputs.stressed_full, inputs.stressed_oos, g),
        )
        if o.gate in g.g9_must_pass
    )
    bad = [o for o in stressed if o.status is not Status.PASS]
    g9 = GateOutcome(
        "G-9",
        _verdict_status(bad),
        "; ".join(f"{o.gate} {o.status}: {o.observed}" for o in bad) or "all held",
        g.g9_stress,
    )
    outcomes = (
        g1(inputs.full, g),
        g2(inputs.full, g),
        g3(inputs.full, g),
        g4(inputs.oos, g),
        g5(inputs.oos, inputs.is_folds, g),
        g6(inputs.perturbations, g),
        g7(inputs.full, inputs.oos, g),
        g8(inputs.oos, inputs.trials, g),
        g9,
    )
    return GateReport(
        outcomes=outcomes,
        verdict=_verdict(outcomes),
        gates_sha256=g.sha256(),
        trial_chain_head=inputs.trials.chain_head,
        n_trials=inputs.trials.n_trials,
        stressed=stressed,
    )


def _verdict_status(bad: Sequence[GateOutcome]) -> Status:
    if any(o.status is Status.FAIL for o in bad):
        return Status.FAIL
    if bad:
        return Status.INVALID
    return Status.PASS


def evaluate_holdout(run: RunSummary, g: FrozenGates = FROZEN) -> GateReport:
    """The single holdout read: must pass G-1, G-3 (a 10-trade "pass" means nothing) and the
    OOS profit-factor floor G-4. If it fails, the strategy is dead - never re-tuned."""
    outcomes = tuple(
        o for o in (g1(run, g), g3(run, g), g4(run, g)) if o.gate in g.holdout_must_pass
    )
    return GateReport(outcomes, _verdict(outcomes), g.sha256(), "", 0)
