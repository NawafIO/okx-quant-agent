"""Apply the frozen M4 dual-filter universe rule (docs/M4_DESIGN.md §2) and write
docs/universe_m4.json. Reads only through the research door.

    python scripts/universe_m4.py
"""

import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars
from okxq.data.store import ParquetStore
from okxq.data.universe import MIN_24H_QUOTE_VOLUME
from okxq.env.profiles import build_profile

ROOT = Path(__file__).resolve().parents[1]
HISTORY_CUTOFF = datetime(2022, 9, 30, tzinfo=UTC)  # >= 3 years before research end


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def main() -> int:
    profile = build_profile("PAPER")
    store = ParquetStore(profile.parquet_root)
    rows, members = [], []
    for d in sorted((profile.parquet_root / "ohlcv").iterdir()):
        inst = d.name.removeprefix("inst_id=")
        try:
            h = load_research_bars(store, inst, "1h", 0, FROZEN.holdout_start_ms)
            day = load_research_bars(store, inst, "1d", 0, FROZEN.holdout_start_ms)
        except Exception as e:  # no research-window data: excluded, reason recorded
            rows.append({"inst_id": inst, "included": False, "why": type(e).__name__})
            continue
        if not h.ts_open_ms or not day.ts_open_ms:
            rows.append({"inst_id": inst, "included": False, "why": "no research-window bars"})
            continue
        first = int(h.ts_open_ms[0])
        turnover = statistics.median(
            float(c) * float(v) for c, v in zip(day.close, day.volume_base, strict=True)
        )
        hist_ok = first <= int(HISTORY_CUTOFF.timestamp() * 1000)
        liq_ok = turnover >= MIN_24H_QUOTE_VOLUME
        ok = hist_ok and liq_ok
        rows.append(
            {
                "inst_id": inst,
                "first_1h": iso(first),
                "median_daily_quote_turnover_usd": round(turnover),
                "history_ok": hist_ok,
                "liquidity_ok": liq_ok,
                "included": ok,
            }
        )
        if ok:
            members.append(inst)
    out = {
        "rule": "docs/M4_DESIGN.md section 2: first 1h bar <= 2022-09-30 AND median daily quote "
        f"turnover (close x base volume, research window) >= {MIN_24H_QUOTE_VOLUME}",
        "computed": "2026-10-03",
        "members": members,
        "instruments": rows,
    }
    (ROOT / "docs/universe_m4.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for r in rows:
        print(r)
    print("MEMBERS:", members)
    return 0


if __name__ == "__main__":
    sys.exit(main())
