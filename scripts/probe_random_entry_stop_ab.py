# Evidence for the 2026-10-03 random-entry diagnosis (docs/M2_DESIGN.md). Run from repo root.
import math
import statistics as st
import sys
from collections import defaultdict
from decimal import Decimal as D  # noqa: N817
from pathlib import Path

sys.path.insert(0, "/home/user/okx-quant-agent")
from okxq.backtest import costs, sanity
from okxq.backtest.cli import _load_specs, _research_window
from okxq.backtest.engine import BacktestEngine
from okxq.backtest.reference_strategies import RandomEntry
from okxq.backtest.sizing import ProvisionalFixedNotionalSizer
from okxq.backtest.types import BacktestConfigError
from okxq.data.store import ParquetStore

store = ParquetStore(Path("/home/user/okx-quant-agent/data/paper/parquet"))
specs = _load_specs(Path("/home/user/okx-quant-agent/docs/instrument_specs.json"))
start, end, series, funding = _research_window(store, specs, 3.0)
cfg = costs.research_config(costs.FEES_LOW_SENSITIVITY)


def build(insts):
    return BacktestEngine(
        series={k: series[k] for k in insts},
        specs=specs,
        funding={k: funding[k] for k in insts},
        config=cfg,
        sizer=ProvisionalFixedNotionalSizer(D(1000)),
        trade_start_ms=start,
        end_ms=end,
    )


usable = []
for i in sorted(series):
    try:
        build([i])
        usable.append(i)
    except BacktestConfigError:
        pass
for label, stop in (("3% stop", D("0.03")), ("stop never hit (50%)", D("0.5"))):
    by = defaultdict(list)
    for seed in range(20):
        r = build(usable).run(RandomEntry(seed=seed, stop_frac=stop))
        for row, t in zip(sanity.trade_bps(r), r.trades, strict=True):
            by["all"].append(row.pre_cost)
            by[(t.exit_reason, row.side)].append(row.pre_cost)
    a = by["all"]
    mu = st.mean(a)
    se = st.stdev(a) / math.sqrt(len(a))
    print(f"{label}: n={len(a)} pre-cost {mu:+.3f} +/- {se:.3f} bps (t={mu / se:+.2f})")
    for k in sorted(k for k in by if k != "all"):
        v = by[k]
        print(f"    {k[0]:<10} {k[1]:<6} n={len(v):>6} mean {st.mean(v):+8.2f} bps")
