"""The martingale invariant - the only test that can catch optimism in the stop/exit path.

Chief Advisor ruling (2026-10-03): *no strategy may make money through the engine on a
martingale, net of its own modelled slippage.* On a real market, a random-entry result
mixes engine behaviour with market dynamics (a 3% stop costs ~3.6 bps/trade on real hourly
data). Only on a process whose expectation is known - a price martingale - does any
positive mean prove the engine itself is generous.

Paths are price martingales with fat tails, so jumps exist: each bar is ``substeps``
multiplicative steps ``p *= 1 + s * t / sqrt(3)`` with ``t ~ Student-t(nu=3)``
(unit variance), ``E[p_next | p] = p``. The bar's OHLC is read off the path. Fees are zero,
funding is zero, slippage is the research model, so the per-trade net is exactly what the
engine gives away or takes beyond its own modelled costs.

Gate (ruling): mean net <= ``max_mean_bps`` with standard error <= ``max_stderr_bps``,
at >= ``min_substeps`` sub-steps, both with and without stops.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from okxq.backtest.engine import BacktestEngine, BacktestResult, EngineConfig
from okxq.backtest.reference_strategies import RandomEntry
from okxq.backtest.sizing import ProvisionalFixedNotionalSizer
from okxq.backtest.types import (
    BarSeries,
    FeeSchedule,
    FundingRate,
    InstrumentSpec,
    Provenance,
    SlippageModel,
)

HOUR = 3_600_000
T0 = 1_600_000_000_000 // HOUR * HOUR
INST = "MARTINGALE-USDT-SWAP"
SPEC = InstrumentSpec(
    INST,
    Decimal("0.0001"),
    Decimal("0.0001"),
    Decimal("0.0001"),
    Decimal("0.004"),
    Provenance.SYNTHETIC,
)


@dataclass(frozen=True)
class InvariantPolicy:
    max_mean_bps: float = 0.5
    max_stderr_bps: float = 0.3
    min_substeps: int = 48


POLICY = InvariantPolicy()


def martingale_bars(
    n: int,
    seed: int,
    *,
    substeps: int = 48,
    sigma_bar: float = 0.008,
    nu: float = 3.0,
    price0: float = 1000.0,
    volume: float = 1e6,
) -> BarSeries:
    """``n`` hourly bars from a fat-tailed price martingale, on a 0.0001 tick."""
    rng = np.random.default_rng(seed)
    t = rng.standard_t(nu, size=n * substeps)
    steps = 1.0 + (sigma_bar / math.sqrt(substeps)) * t / math.sqrt(nu / (nu - 2.0))
    path = price0 * np.cumprod(np.clip(steps, 1e-6, None))
    # A price martingale's log drifts down at sigma^2/2 per bar, so a long path decays
    # toward the tick, where the one-tick floor would dominate the result in bps. Refuse it
    # rather than test the floor: keep paths short enough (n * sigma_bar^2 / 2 << 1).
    lowest = float(np.min(path))
    if lowest < 1000 * 0.0001:
        raise ValueError(f"path collapsed to {lowest:.6f}; use fewer bars per path")
    grid = path.reshape(n, substeps)
    closes = grid[:, -1]
    opens = np.concatenate(([price0], closes[:-1]))
    highs = np.maximum(grid.max(axis=1), opens)
    lows = np.minimum(grid.min(axis=1), opens)

    def dec(a: np.ndarray) -> tuple[Decimal, ...]:
        return tuple(Decimal(f"{x:.4f}") for x in a)

    return BarSeries(
        inst_id=INST,
        timeframe_ms=HOUR,
        ts_open_ms=tuple(T0 + i * HOUR for i in range(n)),
        open=dec(opens),
        high=dec(highs),
        low=dec(lows),
        close=dec(closes),
        volume_base=tuple(Decimal(repr(volume)) for _ in range(n)),
    )


def run_once(
    n_bars: int,
    seed: int,
    slippage: SlippageModel,
    *,
    stop_frac: Decimal,
    substeps: int = 48,
    p_entry: float = 0.9,
    hold_bars: int = 12,
) -> BacktestResult:
    bars = martingale_bars(n_bars, seed, substeps=substeps)
    funding = [FundingRate(T0 + i * 8 * HOUR, Decimal(0)) for i in range(-1, n_bars // 8 + 3)]
    cfg = EngineConfig(
        fees=FeeSchedule(Decimal(0), Decimal(0), Provenance.SYNTHETIC),
        slippage=slippage,
        initial_equity=Decimal(10**12),
        participation_cap=Decimal(1),
        synthetic=True,
    )
    engine = BacktestEngine(
        series={INST: bars},
        specs={INST: SPEC},
        funding={INST: funding},
        config=cfg,
        sizer=ProvisionalFixedNotionalSizer(Decimal(1000)),
        trade_start_ms=T0 + 200 * HOUR,
        end_ms=T0 + n_bars * HOUR,
    )
    strategy = RandomEntry(seed=seed, p_entry=p_entry, stop_frac=stop_frac, hold_bars=hold_bars)
    return engine.run(strategy)


def net_bps(result: BacktestResult) -> list[float]:
    """Per-trade net (fees and funding are zero here) in bps of entry notional."""
    out = []
    for t in result.trades:
        notional = t.max_qty * t.avg_entry
        if notional > 0:
            out.append(float(t.net_pnl / notional) * 1e4)
    return out


@dataclass(frozen=True)
class InvariantResult:
    label: str
    n: int
    mean_bps: float
    stderr_bps: float
    passed: bool


def evaluate(
    label: str, nets: Sequence[float], policy: InvariantPolicy = POLICY
) -> InvariantResult:
    n = len(nets)
    mu = math.fsum(nets) / n
    sd = math.sqrt(math.fsum((x - mu) ** 2 for x in nets) / (n - 1))
    se = sd / math.sqrt(n)
    return InvariantResult(
        label, n, mu, se, mu <= policy.max_mean_bps and se <= policy.max_stderr_bps
    )
