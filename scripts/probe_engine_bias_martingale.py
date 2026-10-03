# Evidence for the 2026-10-03 random-entry diagnosis (docs/M2_DESIGN.md). Run from repo root.
"""Is the -3.6 bps pre-cost bias the engine or the market? Martingale test + real decomposition."""

import math
import random
import statistics as st
import sys
from collections import defaultdict
from decimal import Decimal as D  # noqa: N817

sys.path.insert(0, "/home/user/okx-quant-agent")
from okxq.backtest import sanity
from okxq.backtest.engine import BacktestEngine, EngineConfig
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

H = 3_600_000
T0 = 1_600_000_000_000 // H * H


def martingale(n, seed, vol=0.008):
    """Bars whose path is a price martingale: 12 sub-steps per bar, E[step]=0 in PRICE."""
    rng = random.Random(seed)  # noqa: S311 - simulation
    p = 1000.0
    rows = []
    for _ in range(n):
        o = p
        hi = lo = p
        for _ in range(12):
            p = max(1.0, p * (1 + rng.gauss(0, vol / math.sqrt(12))))
            hi, lo = max(hi, p), min(lo, p)
        rows.append((o, hi, lo, p))

    def q(x: float) -> D:
        return D(repr(round(x, 4)))

    return BarSeries(
        "M-USDT-SWAP",
        H,
        tuple(T0 + i * H for i in range(n)),
        tuple(q(r[0]) for r in rows),
        tuple(q(r[1]) for r in rows),
        tuple(q(r[2]) for r in rows),
        tuple(q(r[3]) for r in rows),
        tuple(D(10**9) for _ in rows),
    )


spec = InstrumentSpec(
    "M-USDT-SWAP", D("0.0001"), D("0.0001"), D("0.0001"), D("0.004"), Provenance.SYNTHETIC
)
NB = 100000
fund = [FundingRate(T0 + i * 8 * H, D(0)) for i in range(-1, NB // 8 + 3)]
cfg = EngineConfig(
    fees=FeeSchedule(D(0), D(0), Provenance.SYNTHETIC),
    slippage=SlippageModel(D(0), 24, 0, "probe"),
    initial_equity=D(10**9),
    participation_cap=D(1),
    synthetic=True,
)
rows = []
for seed in range(20):
    bars = martingale(NB, seed)
    eng = BacktestEngine(
        series={"M-USDT-SWAP": bars},
        specs={"M-USDT-SWAP": spec},
        funding={"M-USDT-SWAP": fund},
        config=cfg,
        sizer=ProvisionalFixedNotionalSizer(D(1000)),
        trade_start_ms=T0 + 30 * H,
        end_ms=T0 + NB * H,
    )
    rows += sanity.trade_bps(eng.run(RandomEntry(seed=seed, p_entry=0.9)))
pre = [r.pre_cost for r in rows]
mu = st.mean(pre)
se = st.stdev(pre) / math.sqrt(len(pre))
print(f"MARTINGALE: n={len(pre)} pre-cost mean {mu:+.3f} bps, stderr {se:.3f}, t={mu / se:+.2f}")
by = defaultdict(list)
for r in rows:
    by[r.side].append(r.pre_cost)
for k, v in by.items():
    print(f"  {k}: n={len(v)} mean {st.mean(v):+.3f} bps")
