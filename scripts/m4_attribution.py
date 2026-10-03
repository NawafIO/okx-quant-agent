"""Cost attribution of a completed M4 walk-forward (diagnostic, not a gate).

    python scripts/m4_attribution.py docs/m4_runs/trend_breakout_run1.txt

Used ONCE, for trend_breakout run 1, before the runner emitted attribution itself (Chief
Advisor M4 checkpoint 2: attribution comes from stored results; a diagnostic re-run needs a
recorded reason). Re-runs each fold's out-of-sample window with the params that run
selected (parsed from its output), through ResearchProtocol so every evaluation is LOGGED
(purpose "diagnostic"; N rises, the conservative direction), and prints the runner's
attribution: gross, fees, slippage and funding per instrument and side, and how much
funding came from the adverse funding-bound-v1.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from m4_research import (
    MODULES,
    RESEARCH_START_MS,
    ROOT,
    WARMUP_MS,
    Provider,
    print_attribution,
)

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
    print_attribution([fr.oos.result for fr in wf.folds if fr.oos])
    print(f"  logged trials now {proto.trials.stats().n_trials}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
