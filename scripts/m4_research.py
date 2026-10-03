"""Run one M4 candidate through the frozen research protocol (docs/M4_DESIGN.md).

    python scripts/m4_research.py trend_breakout

Every configuration evaluated goes to the PAPER trial log (G-8's N). Prints the GateReport,
the per-instrument out-of-sample breakdown, the BTC+ETH-only line and the survivorship
caveat. Reads only through the research door; the holdout stays sealed.
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from okxq.backtest import costs
from okxq.backtest import metrics as m
from okxq.backtest.cli import _load_specs
from okxq.backtest.engine import BacktestResult
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars, load_research_funding
from okxq.backtest.sizing import RESEARCH_SIZING
from okxq.backtest.types import BarSeries, ClosedTrade, FundingRate
from okxq.backtest.walkforward import ResearchProtocol
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs
from okxq.strategy.candidates import keltner_reversion, trend_breakout, vol_compression_breakout

ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
RESEARCH_START_MS = int(datetime(2020, 7, 1, tzinfo=UTC).timestamp() * 1000)
WARMUP_MS = 180 * DAY_MS
MODULES = {
    mod.SPEC.strategy_id: mod
    for mod in (trend_breakout, keltner_reversion, vol_compression_breakout)
}
CAVEAT = (
    "SURVIVORSHIP (V-14): the universe contains only instruments still listed in 2026; delisted "
    "perpetuals (e.g. LUNA, FTT) cannot be obtained from OKX. Results are biased UPWARD by an "
    "unknown amount, so a pass is weaker evidence than the gates imply. See the per-instrument "
    "breakdown and the BTC+ETH-only line."
)


def _slice(s: BarSeries, start: int, end: int) -> BarSeries:
    i, j = bisect_left(s.ts_open_ms, start), bisect_left(s.ts_open_ms, end)
    return BarSeries(
        s.inst_id,
        s.timeframe_ms,
        s.ts_open_ms[i:j],
        s.open[i:j],
        s.high[i:j],
        s.low[i:j],
        s.close[i:j],
        s.volume_base[i:j],
    )


class Provider:
    """All research data loaded once; each window is a slice. An instrument takes part in a
    window only if its data begins at or before the window's warm-up start (frozen rule)."""

    def __init__(self, store: ParquetStore, members: Sequence[str]) -> None:
        end = FROZEN.holdout_start_ms
        self.bars = {i: load_research_bars(store, i, "1h", 0, end) for i in members}
        self.funding = {i: load_research_funding(store, i, 0, end) for i in members}

    def __call__(
        self, start: int, end: int
    ) -> tuple[Mapping[str, BarSeries], Mapping[str, Sequence[FundingRate]]]:
        series, funding = {}, {}
        for inst, s in self.bars.items():
            if not s.ts_open_ms or s.ts_open_ms[0] > start + DAY_MS:
                continue  # data begins inside the warm-up: not yet a participant
            series[inst] = _slice(s, start, end)
            funding[inst] = [f for f in self.funding[inst] if start <= f.ts_ms < end]
        return series, funding


def _pf(trades: Sequence[ClosedTrade]) -> str:
    pf = m.profit_factor([t.net_pnl for t in trades])
    return "undef" if pf is None else f"{pf:.3f}"


def print_attribution(results: Sequence[BacktestResult]) -> None:
    """Per-instrument and per-side OOS attribution from stored fold results (no re-runs;
    Chief Advisor M4 checkpoint 2). Gross includes slippage; net = gross + fees + funding."""
    trades = [t for r in results for t in r.trades]
    agg: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for t in trades:
        for key in ((t.inst_id, str(t.side)), ("ALL", str(t.side)), ("ALL", "BOTH")):
            a = agg[key]
            a["n"] += 1
            a["gross"] += t.gross_pnl
            a["fees"] -= t.fees
            a["slip"] -= t.slippage_cost
            a["funding"] += t.funding
            a["net"] += t.net_pnl
    print("\nOOS attribution (gross includes slippage; net = gross + fees + funding):")
    print(
        f"  {'instrument':<16}{'side':<6}{'n':>6}{'gross':>11}{'fees':>10}{'slip':>10}"
        f"{'funding':>10}{'net':>11}  PF"
    )
    for (inst, side), a in sorted(agg.items(), key=lambda kv: (kv[0][0] == "ALL", kv[0])):
        ts = [
            t
            for t in trades
            if (inst == "ALL" or t.inst_id == inst) and (side == "BOTH" or str(t.side) == side)
        ]
        print(
            f"  {inst:<16}{side:<6}{int(a['n']):>6}{a['gross']:>11.0f}{a['fees']:>10.0f}"
            f"{a['slip']:>10.0f}{a['funding']:>10.0f}{a['net']:>11.0f}  {_pf(ts)}"
        )
    core = [t for t in trades if t.inst_id in ("BTC-USDT-SWAP", "ETH-USDT-SWAP")]
    core_net = sum((t.net_pnl for t in core), Decimal(0))
    print(f"  {'BTC+ETH only':<22}{len(core):>6}{'':>41}{core_net:>11.0f}  {_pf(core)}")
    refused: dict[str, int] = defaultdict(int)
    for r in results:
        for rj in r.rejections:
            refused[rj.reason] += 1
    fills = sum(len(r.fills) for r in results)
    print(
        f"  fills {fills}; closed trades {len(trades)}; refused entries "
        f"{sum(refused.values())}: "
        + (", ".join(f"{k} {v}" for k, v in sorted(refused.items())) or "none")
    )
    bound: dict[str, Decimal] = defaultdict(Decimal)
    total = Decimal(0)
    for r in results:
        for f in r.funding:
            total += f.cash_flow
            if f.bound:
                bound[f.inst_id] += f.cash_flow
    print(
        f"  funding cash flow {total:.0f}, of which funding-bound-v1 "
        f"{sum(bound.values(), Decimal(0)):.0f}: "
        + ", ".join(f"{k.split('-')[0]} {v:.0f}" for k, v in sorted(bound.items()))
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", choices=sorted(MODULES))
    a = ap.parse_args()
    mod = MODULES[a.strategy]
    spec = mod.SPEC
    profile = build_profile("PAPER")
    ensure_dirs(profile)
    members = json.loads((ROOT / "docs/universe_m4.json").read_text(encoding="utf-8"))["members"]
    specs = _load_specs(ROOT / "docs/instrument_specs.json")
    provider = Provider(ParquetStore(profile.parquet_root), members)
    proto = ResearchProtocol(
        factory=spec,
        data=provider,
        specs={k: specs[k] for k in members},
        config=costs.research_config(costs.FEES_USER_SPOT_SCHEDULE),
        profile=profile,
        warmup_ms=WARMUP_MS,
    )
    n0 = proto.trials.stats().n_trials
    grid = spec.grid(mod.GRID)
    print(
        f"{spec.strategy_id} version {spec.version[:12]}; grid {len(grid)}; "
        f"span 2020-07-01..2025-09-30; sizing {RESEARCH_SIZING.sha256()[:12]}; "
        f"trials before this run {n0}"
    )
    res = proto.run(grid, RESEARCH_START_MS, FROZEN.holdout_start_ms)
    r = res.report
    print(f"\nVERDICT {r.verdict}   (N trials now {r.n_trials}; this run added {r.n_trials - n0})")
    print(
        f"fees {r.fee_provenance}; cost {r.cost_config_sha[:12]}; sizing {r.sizing_sha[:12]}; "
        f"gates {r.gates_sha256[:12]}"
    )
    for o in r.outcomes:
        print(f"  {o.gate:<5} {o.status!s:<8} observed {o.observed}  limit {o.limit}  {o.detail}")
    for o in r.stressed:
        print(f"  G-9 under stress: {o.gate} {o.status} observed {o.observed}")
    print(f"final params (full-span selection): {res.final_params}")

    oos = [t for fr in res.walk_forward.folds if fr.oos for t in fr.oos.result.trades]
    folds = [fr for fr in res.walk_forward.folds]
    print(
        f"\nwalk-forward: {len(folds)} folds, {sum(1 for f in folds if f.oos)} with a selection; "
        f"{len(oos)} OOS trades"
    )
    for fr in folds:
        sel = fr.oos.params if fr.oos else None
        n = len(fr.oos.result.trades) if fr.oos else 0
        net = sum((t.net_pnl for t in fr.oos.result.trades), Decimal(0)) if fr.oos else 0
        t0 = datetime.fromtimestamp(fr.fold.test_start_ms / 1000, tz=UTC).date()
        print(f"  fold {fr.fold.index:>2} test {t0}: {n:>5} trades, net {net:>12.2f}, params {sel}")
    print_attribution([fr.oos.result for fr in res.walk_forward.folds if fr.oos])
    print(f"\n{CAVEAT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
