"""Walk-forward protocol and the full gate pipeline (architecture §11.3).

Every degree of freedom a researcher could tune lives in :data:`okxq.backtest.gates.FROZEN`:
window lengths, the in-sample selection objective, the minimum in-sample trade count for a
configuration to be selectable, flat-at-fold-boundary. Every configuration evaluated - in
selection, the final fit, perturbation and stress - is appended to the trial log, which is
what G-8 deflates by.

Final configuration (for G-1/G-2/G-3/G-6/G-7 on the full research period): chosen on the
whole research span by the same frozen objective. Perturbations (G-6) and stress (G-9) are
evaluated with parameters held FIXED - no re-selection - so neither can be passed by
re-optimising around the perturbed or stressed costs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from okxq.backtest import metrics as m
from okxq.backtest.engine import BacktestEngine, BacktestResult, EngineConfig, Strategy
from okxq.backtest.gates import (
    FROZEN,
    FoldIS,
    FrozenGates,
    GateInputs,
    GateReport,
    Perturbation,
    RunSummary,
    evaluate,
)
from okxq.backtest.holdout import HoldoutSealedError
from okxq.backtest.sizing import Sizer
from okxq.backtest.trials import Trial, TrialLog
from okxq.backtest.types import G9_STRESS, BarSeries, FundingRate, InstrumentSpec

Params = dict[str, Any]
#: (start_ms, end_ms) -> bars and funding covering that window plus any warm-up before it.
DataProvider = Callable[
    [int, int], tuple[Mapping[str, BarSeries], Mapping[str, Sequence[FundingRate]]]
]


class StrategyFactory(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def build(self, params: Params) -> Strategy: ...


@dataclass(frozen=True)
class Fold:
    index: int
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int


def _add_months(ms: int, months: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, tz=UTC)
    y, mo = divmod(d.month - 1 + months, 12)
    return int(d.replace(year=d.year + y, month=mo + 1).timestamp()) * 1000


def make_folds(start_ms: int, end_ms: int, gates: FrozenGates = FROZEN) -> list[Fold]:
    """Rolling windows: train ``wf_train_months``, test the next ``wf_test_months``, step
    ``wf_step_months``. Refuses any span reaching the sealed holdout."""
    if end_ms > gates.holdout_start_ms:
        raise HoldoutSealedError("walk-forward span reaches the sealed holdout")
    folds: list[Fold] = []
    k = 0
    while True:
        train_start = _add_months(start_ms, k * gates.wf_step_months)
        train_end = _add_months(train_start, gates.wf_train_months)
        test_end = _add_months(train_end, gates.wf_test_months)
        if test_end > end_ms:
            return folds
        folds.append(Fold(k, train_start, train_end, train_end, test_end))
        k += 1


def periods_per_year(series: Mapping[str, BarSeries]) -> int:
    tf = min(s.timeframe_ms for s in series.values())
    return int(365 * 24 * 3_600_000 // tf)


@dataclass(frozen=True)
class Evaluated:
    params: Params
    result: BacktestResult
    summary: RunSummary
    annual_sharpe: float | None


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    selected: Evaluated | None
    oos: Evaluated | None


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[FoldResult, ...]
    oos: RunSummary
    is_folds: tuple[FoldIS, ...]


@dataclass(frozen=True)
class ProtocolResult:
    report: GateReport
    final_params: Params | None
    walk_forward: WalkForwardResult


class ResearchProtocol:
    """Runs strategies through the frozen protocol. One instance = one research context."""

    def __init__(
        self,
        *,
        factory: StrategyFactory,
        data: DataProvider,
        specs: Mapping[str, InstrumentSpec],
        config: EngineConfig,
        sizer: Sizer,
        trials: TrialLog,
        warmup_ms: int,
        gates: FrozenGates = FROZEN,
    ) -> None:
        self._factory = factory
        self._data = data
        self._specs = specs
        self._config = config
        self._sizer = sizer
        self._trials = trials
        self._warmup = warmup_ms
        self._g = gates

    # -- single evaluation, always logged -------------------------------------------------

    def evaluate_params(
        self,
        params: Params,
        start_ms: int,
        end_ms: int,
        *,
        purpose: str,
        config: EngineConfig | None = None,
    ) -> Evaluated:
        if end_ms > self._g.holdout_start_ms:
            raise HoldoutSealedError("research evaluation reaches the sealed holdout")
        series, funding = self._data(start_ms - self._warmup, end_ms)
        engine = BacktestEngine(
            series=series,
            specs=self._specs,
            funding=funding,
            config=config or self._config,
            sizer=self._sizer,
            trade_start_ms=start_ms,
            end_ms=end_ms,
        )
        result = engine.run(self._factory.build(params))
        summary = RunSummary.of(result.net_pnls, result.equity_values)
        rets = list(summary.returns)
        annual = m.sharpe_ratio(rets, periods_per_year(series))
        pf = m.profit_factor(list(summary.pnls))
        self._trials.record(
            Trial(
                strategy_id=self._factory.strategy_id,
                strategy_version=self._factory.version,
                params=params,
                window_start_ms=start_ms,
                window_end_ms=end_ms,
                purpose=purpose,
                n_trades=len(summary.pnls),
                per_period_sharpe=m.per_period_sharpe(rets),
                profit_factor=None if pf is None else str(pf),
                result_digest=result.digest(),
            )
        )
        return Evaluated(params, result, summary, annual)

    def select(self, grid: Sequence[Params], start_ms: int, end_ms: int) -> Evaluated | None:
        """Frozen objective: max annualised Sharpe among configs with enough IS trades.
        Ties keep grid order, so selection is deterministic."""
        best: Evaluated | None = None
        for params in grid:
            ev = self.evaluate_params(params, start_ms, end_ms, purpose="selection")
            if len(ev.summary.pnls) < self._g.wf_min_is_trades_for_selection:
                continue
            if ev.annual_sharpe is None:
                continue
            if best is None or ev.annual_sharpe > (best.annual_sharpe or -math.inf):
                best = ev
        return best

    # -- walk-forward ---------------------------------------------------------------------

    def walk_forward(
        self,
        grid: Sequence[Params],
        start_ms: int,
        end_ms: int,
        *,
        config: EngineConfig | None = None,
        fixed: Mapping[int, Params] | None = None,
        purpose: str = "oos",
    ) -> WalkForwardResult:
        """``fixed`` re-runs given per-fold params without re-selection (used for G-9)."""
        cfg = config or self._config
        equity = cfg.initial_equity
        out: list[FoldResult] = []
        pnls: list[Decimal] = []
        curve: list[Decimal] = []
        is_folds: list[FoldIS] = []
        for fold in make_folds(start_ms, end_ms, self._g):
            if fixed is not None:
                params = fixed.get(fold.index)
                selected = None
            else:
                selected = self.select(grid, fold.train_start_ms, fold.train_end_ms)
                params = selected.params if selected else None
                if selected is not None:
                    is_folds.append(
                        FoldIS(
                            m.profit_factor(list(selected.summary.pnls)), len(selected.summary.pnls)
                        )
                    )
            if params is None:
                out.append(FoldResult(fold, selected, None))
                continue
            oos = self.evaluate_params(
                params,
                fold.test_start_ms,
                fold.test_end_ms,
                purpose=purpose,
                config=replace(cfg, initial_equity=equity),
            )
            equity = oos.summary.equity[-1] if oos.summary.equity else equity
            pnls.extend(oos.summary.pnls)
            # Stitch: each fold's curve starts at the previous fold's closing equity.
            curve.extend(oos.summary.equity[1:] if curve else oos.summary.equity)
            out.append(FoldResult(fold, selected, oos))
        return WalkForwardResult(tuple(out), RunSummary.of(pnls, curve), tuple(is_folds))

    # -- G-6 ------------------------------------------------------------------------------

    def perturb(self, params: Params, start_ms: int, end_ms: int) -> list[Perturbation]:
        out = []
        step = self._g.g6_perturbation
        for name, value in sorted(params.items()):
            if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
                continue
            for factor in (1 - step, 1 + step):
                moved = _scale(value, factor)
                if moved == value:
                    out.append(Perturbation(name, factor, None, Decimal(0), unperturbable=True))
                    continue
                ev = self.evaluate_params(
                    params | {name: moved}, start_ms, end_ms, purpose="perturbation"
                )
                out.append(
                    Perturbation(
                        name,
                        factor,
                        m.profit_factor(list(ev.summary.pnls)),
                        m.max_drawdown(list(ev.summary.equity)),
                    )
                )
        return out

    # -- the whole protocol ---------------------------------------------------------------

    def run(self, grid: Sequence[Params], start_ms: int, end_ms: int) -> ProtocolResult:
        wf = self.walk_forward(grid, start_ms, end_ms)
        final = self.select(grid, start_ms, end_ms)
        oos_span = (
            (wf.folds[0].fold.test_start_ms, wf.folds[-1].fold.test_end_ms)
            if wf.folds
            else (start_ms, end_ms)
        )
        if final is None:
            empty = RunSummary.of([], [])
            full = stressed_full = empty
            perts: list[Perturbation] = []
        else:
            full = final.summary
            perts = self.perturb(final.params, *oos_span)
            stressed_full = self.evaluate_params(
                final.params,
                start_ms,
                end_ms,
                purpose="stress",
                config=replace(self._config, stress=G9_STRESS),
            ).summary
        fixed = {fr.fold.index: fr.oos.params for fr in wf.folds if fr.oos is not None}
        stressed_wf = self.walk_forward(
            grid,
            start_ms,
            end_ms,
            config=replace(self._config, stress=G9_STRESS),
            fixed=fixed,
            purpose="stress",
        )
        inputs = GateInputs(
            full=full,
            oos=wf.oos,
            is_folds=wf.is_folds,
            perturbations=tuple(perts),
            # Read LAST, so N includes every trial this run itself just made.
            trials=self._trials.stats(),
            stressed_full=stressed_full,
            stressed_oos=stressed_wf.oos,
        )
        return ProtocolResult(evaluate(inputs, self._g), final.params if final else None, wf)


def _scale(value: int | float | Decimal, factor: Decimal) -> int | float | Decimal:
    if isinstance(value, int):
        return int((Decimal(value) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if isinstance(value, float):
        return float(Decimal(repr(value)) * factor)
    return value * factor
