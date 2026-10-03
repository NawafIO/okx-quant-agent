"""Cost attribution of a completed M4 walk-forward (diagnostic, not a gate).

    python scripts/m4_attribution.py docs/m4_runs/trend_breakout_run1.txt

Re-runs each fold's out-of-sample window with the params that run selected (parsed from the
run's output), through ResearchProtocol so every evaluation is LOGGED (purpose
"diagnostic"; N rises, the conservative direction), and splits P&L per instrument and side
into gross, fees, slippage and funding - including how much funding came from the adverse
funding-bound-v1.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from m4_research import MODULES, RESEARCH_START_MS, ROOT, WARMUP_MS, Provider

from okxq.backtest import costs
from okxq.backtest.cli import _load_specs
from okxq.backtest.gates import FROZEN
from okxq.backtest.walkforward import ResearchProtocol
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile


def main() -> int:
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    sid = text.split()[0]
    fixed = {
        int(m.group(1)): ast.literal_eval(m.group(2))
        for m in re.finditer(r"fold\s+(\d+) test .*params (\{.*\})", text)
    }
    profile = build_profile("PAPER")
    members = json.loads((ROOT / "docs/universe_m4.json").read_text(encoding="utf-8"))["members"]
    specs = _load_specs(ROOT / "docs/instrument_specs.json")
    proto = ResearchProtocol(
        factory=MODULES[sid].SPEC,
        data=Provider(ParquetStore(profile.parquet_root), members),
        specs={k: specs[k] for k in members},
        config=costs.research_config(costs.FEES_USER_SPOT_SCHEDULE),
        profile=profile,
        warmup_ms=WARMUP_MS,
    )
    wf = proto.walk_forward(
        [], RESEARCH_START_MS, FROZEN.holdout_start_ms, fixed=fixed, purpose="diagnostic"
    )
    agg: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for fr in wf.folds:
        if fr.oos is None:
            continue
        r = fr.oos.result
        for t in r.trades:
            a = agg[(t.inst_id, str(t.side))]
            a["n"] += 1
            a["gross"] += t.gross_pnl
            a["fees"] -= t.fees
            a["slip"] -= t.slippage_cost
            a["funding"] += t.funding
            a["net"] += t.net_pnl
    print(
        f"{sid}: OOS attribution over {len(fixed)} folds (gross includes slippage; "
        "fees and funding shown separately; net = gross + fees + funding)"
    )
    print(
        f"  {'instrument':<16}{'side':<6}{'n':>5}{'gross':>12}{'fees':>11}{'slip':>11}"
        f"{'funding':>11}{'net':>12}"
    )
    tot: dict[str, Decimal] = defaultdict(Decimal)
    for (inst, side), a in sorted(agg.items()):
        print(
            f"  {inst:<16}{side:<6}{int(a['n']):>5}{a['gross']:>12.0f}{a['fees']:>11.0f}"
            f"{a['slip']:>11.0f}{a['funding']:>11.0f}{a['net']:>12.0f}"
        )
        for k in ("gross", "fees", "slip", "funding", "net"):
            tot[k] += a[k]
    print(
        f"  {'TOTAL':<22}{'':>5}{tot['gross']:>12.0f}{tot['fees']:>11.0f}{tot['slip']:>11.0f}"
        f"{tot['funding']:>11.0f}{tot['net']:>12.0f}"
    )
    b = sum(
        (f.cash_flow for fr in wf.folds if fr.oos for f in fr.oos.result.funding if f.bound),
        Decimal(0),
    )
    allf = sum(
        (f.cash_flow for fr in wf.folds if fr.oos for f in fr.oos.result.funding), Decimal(0)
    )
    by_inst: dict[str, Decimal] = defaultdict(Decimal)
    for fr in wf.folds:
        if fr.oos:
            for f in fr.oos.result.funding:
                if f.bound:
                    by_inst[f.inst_id] += f.cash_flow
    print(f"\n  funding cash flow total {allf:.0f}, of which from funding-bound-v1 {b:.0f}")
    for inst, v in sorted(by_inst.items()):
        print(f"    bound funding {inst:<16} {v:>10.0f}")
    print(f"  logged trials now {proto.trials.stats().n_trials}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
