"""M1 acceptance: a DuckDB query over the full store returns within a stated budget.

The budget is stated here rather than discovered after the fact, so it can fail:

    B-1  full-store row count                      <  5 s
    B-2  single-symbol single-timeframe full scan  <  2 s   (the research access pattern)
    B-3  multi-year aggregate across all symbols   < 10 s
    B-4  mark/index join for funding reconstruction < 10 s

If a budget is missed the number is reported rather than quietly relaxed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

ROOT = Path("data/paper/parquet")
OHLCV = (ROOT / "ohlcv" / "**" / "*.parquet").as_posix()

BUDGETS = {"B-1": 5.0, "B-2": 2.0, "B-3": 10.0, "B-4": 10.0}
failures: list[str] = []

conn = duckdb.connect()
read = f"read_parquet('{OHLCV}', hive_partitioning=true)"


#: Runs per measurement. A single sample is too fragile to assert on: B-2 measured 0.068s,
#: then 2.736s when it happened to run straight after a full-store scan had evicted the OS
#: file cache, then 0.028s steady-state. The median of several runs reflects the access
#: pattern; one cold sample reflects whatever else touched the disk. The budgets themselves
#: are unchanged - this fixes the measurement, not the target.
SAMPLES = 3


def timed(tag: str, label: str, sql: str) -> None:
    timings: list[float] = []
    rows = 0
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        result = conn.execute(sql).fetchall()
        timings.append(time.perf_counter() - t0)
        rows = len(result)
    timings.sort()
    median = timings[len(timings) // 2]
    budget = BUDGETS[tag]
    ok = median < budget
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag} {label}")
    print(
        f"         median {median:.3f}s of {SAMPLES} (budget {budget:.1f}s) "
        f"[min {timings[0]:.3f} max {timings[-1]:.3f}], {rows} row(s) out"
    )
    if not ok:
        failures.append(f"{tag}: median {median:.3f}s exceeded {budget}s")


print("=" * 78)
print("M1 ACCEPTANCE: DuckDB query budgets over the full store")
print("=" * 78)

size_mb = sum(p.stat().st_size for p in ROOT.rglob("*.parquet")) / 1024 / 1024
files = len(list(ROOT.rglob("*.parquet")))
print(f"\nstore: {files:,} parquet files, {size_mb:,.1f} MB\n")

timed("B-1", "full-store row count", f"SELECT COUNT(*) FROM {read}")

BTC_1H = (
    ROOT
    / "ohlcv"
    / "inst_id=BTC-USDT-SWAP"
    / "timeframe=1h"
    / "price_type=last"
    / "**"
    / "*.parquet"
).as_posix()

timed(
    "B-2",
    "BTC 1h full scan via targeted series path (research access pattern)",
    f"""SELECT MIN(ts_open), MAX(ts_open), COUNT(*), AVG(close)
        FROM read_parquet('{BTC_1H}', hive_partitioning=true)""",
)

# Reported for contrast, not budgeted. Same rows, read through the whole-dataset glob with
# equivalent filters. The gap is file listing and footer metadata, not data volume - which
# is why ParquetStore.series_glob exists and why M2 must use it.
t0 = time.perf_counter()
conn.execute(
    f"""SELECT MIN(ts_open), MAX(ts_open), COUNT(*), AVG(close) FROM {read}
        WHERE inst_id = 'BTC-USDT-SWAP' AND timeframe = '1h' AND price_type = 'last'"""
).fetchall()
print(f"         (same rows via whole-dataset glob: {time.perf_counter() - t0:.3f}s)")

timed(
    "B-3",
    "monthly OHLC aggregate, all symbols, all years",
    f"""SELECT inst_id, date_trunc('month', ts_open) AS m,
               COUNT(*) bars, MIN(low) lo, MAX(high) hi
        FROM {read}
        WHERE timeframe = '1h' AND price_type = 'last'
        GROUP BY 1, 2 ORDER BY 1, 2""",
)

timed(
    "B-4",
    "mark/index hourly join (funding reconstruction input)",
    f"""SELECT m.inst_id, COUNT(*) pairs,
               AVG((m.close - i.close) / i.close) AS avg_premium
        FROM {read} m
        JOIN {read} i
          ON m.inst_id = i.inst_id AND m.ts_open_ms = i.ts_open_ms
        WHERE m.price_type = 'mark' AND i.price_type = 'index'
          AND m.timeframe = '1h' AND i.timeframe = '1h'
        GROUP BY 1 ORDER BY 1""",
)

print("\n--- partition pruning sanity (why B-2 is fast) ---")
t0 = time.perf_counter()
pruned = conn.execute(
    f"""SELECT COUNT(*) FROM {read}
        WHERE inst_id = 'BTC-USDT-SWAP' AND timeframe = '1h' AND price_type = 'last'
          AND year = 2024"""
).fetchone()[0]
print(
    f"  single-month-partition-pruned scan: {time.perf_counter() - t0:.3f}s "
    f"({pruned:,} bars in 2024)"
)

print("\n" + "=" * 78)
if failures:
    print("BUDGET FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL BUDGETS MET")
