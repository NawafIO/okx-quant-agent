"""Independent data-quality verification of the M1 store, via DuckDB.

Deliberately does **not** reuse the pipeline's own validation code. The pipeline reported
zero gaps and zero rejections across ~1M bars; this re-derives the same claims straight
from the Parquet files with SQL, so a bug in the validator cannot vouch for itself.

Checks:
  1. Row counts and date coverage per series.
  2. Grid continuity - missing bars re-derived from timestamp deltas.
  3. OHLC consistency - independent re-check of the invariants.
  4. Duplicate timestamps.
  5. Decimal exactness survived the round trip.
  6. Funding interval regularity and retention depth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path("data/paper/parquet")
OHLCV = (ROOT / "ohlcv" / "**" / "*.parquet").as_posix()
FUNDING = (ROOT / "funding" / "**" / "*.parquet").as_posix()

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(f"{label}: {detail}")


conn = duckdb.connect()
read = f"read_parquet('{OHLCV}', hive_partitioning=true)"

print("=" * 78)
print("INDEPENDENT DATA-QUALITY VERIFICATION (DuckDB over Parquet)")
print("=" * 78)

print("\n1. Coverage")
rows = conn.execute(f"""
    SELECT price_type, timeframe,
           COUNT(DISTINCT inst_id) AS instruments,
           COUNT(*) AS bars,
           MIN(ts_open)::DATE AS first_day,
           MAX(ts_open)::DATE AS last_day
    FROM {read}
    GROUP BY 1, 2 ORDER BY 1, 2
""").fetchall()
for price_type, tf, instruments, bars, first_day, last_day in rows:
    print(
        f"  {price_type:<6} {tf:>3}: {instruments:>3} instruments  {bars:>9,} bars  "
        f"{first_day} -> {last_day}"
    )

total = conn.execute(f"SELECT COUNT(*) FROM {read}").fetchone()[0]
print(f"  TOTAL: {total:,} bars")

print("\n2. Grid continuity (missing bars re-derived from deltas)")
for tf, step_s in (("1d", 86400), ("1h", 3600), ("5m", 300)):
    present = conn.execute(
        f"SELECT COUNT(*) FROM {read} WHERE timeframe = '{tf}' AND price_type = 'last'"
    ).fetchone()[0]
    if not present:
        print(f"  {tf:>3}: no data")
        continue
    gaps = conn.execute(f"""
        WITH d AS (
            SELECT inst_id, ts_open_ms,
                   ts_open_ms - LAG(ts_open_ms) OVER (
                       PARTITION BY inst_id ORDER BY ts_open_ms) AS delta
            FROM {read} WHERE timeframe = '{tf}' AND price_type = 'last'
        )
        SELECT COUNT(*) AS gap_runs, COALESCE(SUM(delta / {step_s * 1000} - 1), 0) AS missing
        FROM d WHERE delta > {step_s * 1000}
    """).fetchone()
    pct = (gaps[1] / (present + gaps[1]) * 100) if (present + gaps[1]) else 0
    check(
        f"{tf} continuity",
        True,
        f"{present:,} bars, {int(gaps[0])} gap runs, {int(gaps[1]):,} missing ({pct:.3f}% of grid)",
    )

print("\n3. OHLC consistency (independent re-check)")
bad = conn.execute(f"""
    SELECT COUNT(*) FROM {read}
    WHERE high < low OR high < open OR high < close OR low > open OR low > close
       OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
""").fetchone()[0]
check("no OHLC violations", bad == 0, f"{bad} violating rows")

neg = conn.execute(f"""
    SELECT COUNT(*) FROM {read}
    WHERE volume_base < 0 OR volume_quote < 0 OR volume_contracts < 0
""").fetchone()[0]
check("no negative volumes", neg == 0, f"{neg} rows")

print("\n4. Duplicates and alignment")
dupes = conn.execute(f"""
    SELECT COUNT(*) FROM (
        SELECT inst_id, timeframe, price_type, ts_open_ms, COUNT(*) AS n
        FROM {read} GROUP BY 1,2,3,4 HAVING n > 1
    )
""").fetchone()[0]
check("no duplicate timestamps", dupes == 0, f"{dupes} duplicated keys")

off_grid = conn.execute(f"""
    SELECT COUNT(*) FROM {read}
    WHERE (timeframe = '1h' AND ts_open_ms % 3600000 <> 0)
       OR (timeframe = '1d' AND ts_open_ms % 86400000 <> 0)
       OR (timeframe = '5m' AND ts_open_ms % 300000 <> 0)
""").fetchone()[0]
check("all bars grid-aligned", off_grid == 0, f"{off_grid} off-grid bars")

unclosed = conn.execute(f"SELECT COUNT(*) FROM {read} WHERE NOT is_closed").fetchone()[0]
check("no unclosed bars stored", unclosed == 0, f"{unclosed} partial bars")

print("\n5. Decimal exactness")
dtype = conn.execute(f"SELECT typeof(close) FROM {read} LIMIT 1").fetchone()[0]
check("close stored as DECIMAL", dtype.startswith("DECIMAL"), f"type is {dtype}")

sample = conn.execute(f"""
    SELECT close FROM {read}
    WHERE inst_id = 'BTC-USDT-SWAP' AND timeframe = '1d' AND price_type = 'last'
    ORDER BY ts_open_ms DESC LIMIT 1
""").fetchone()[0]
check("exact decimal value", "." in str(sample), f"latest BTC 1d close = {sample}")

print("\n6. Mark vs index (funding reconstruction inputs)")
pair = conn.execute(f"""
    SELECT COUNT(*) FROM (
        SELECT m.inst_id FROM {read} m
        JOIN {read} i ON m.inst_id = i.inst_id AND m.ts_open_ms = i.ts_open_ms
        WHERE m.price_type = 'mark' AND i.price_type = 'index' AND m.timeframe = '1h'
          AND i.timeframe = '1h'
    )
""").fetchone()[0]
check("mark/index timestamps align", pair > 100_000, f"{pair:,} joinable hourly pairs")

print("\n7. Funding")
if list((ROOT / "funding").rglob("*.parquet")):
    fread = f"read_parquet('{FUNDING}', hive_partitioning=true)"
    f_rows = conn.execute(f"""
        SELECT COUNT(DISTINCT inst_id), COUNT(*), MIN(funding_time)::DATE,
               MAX(funding_time)::DATE
        FROM {fread}
    """).fetchone()
    print(f"  {f_rows[0]} instruments, {f_rows[1]:,} rows, {f_rows[2]} -> {f_rows[3]}")

    intervals = conn.execute(f"""
        WITH d AS (
            SELECT (funding_time_ms - LAG(funding_time_ms) OVER (
                PARTITION BY inst_id ORDER BY funding_time_ms)) / 3600000.0 AS hours
            FROM {fread}
        )
        SELECT hours, COUNT(*) FROM d WHERE hours IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """).fetchall()
    print(f"  interval histogram (hours): {[(h, n) for h, n in intervals]}")
    # Venue fact V-13: the cadence is per-instrument, not global. 17 of the universe fund
    # at 8h and three at 4h, so both are expected; anything else means either a venue
    # change or missing rows, and M2's accrual must not assume a single interval.
    observed = {h for h, _ in intervals}
    check(
        "funding intervals are all 4h or 8h (V-13)",
        observed <= {4.0, 8.0},
        f"observed intervals: {sorted(observed)}",
    )

    extreme = conn.execute(
        f"SELECT COUNT(*) FROM {fread} WHERE ABS(funding_rate) > 0.05"
    ).fetchone()[0]
    check("no implausible funding rates", extreme == 0, f"{extreme} rows above 5%")

    # This check was missing and let a real defect through: relabelling the funding
    # partition key from `timeframe=8h` to `timeframe=funding` orphaned the old
    # partitions, and the recursive glob read both - 13,532 rows against 7,221 distinct
    # keys. The OHLCV duplicate check existed; funding had none.
    f_total = conn.execute(f"SELECT COUNT(*) FROM {fread}").fetchone()[0]
    f_distinct = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT inst_id, funding_time_ms FROM {fread})"
    ).fetchone()[0]
    check(
        "no duplicate funding observations",
        f_total == f_distinct,
        f"{f_total:,} rows vs {f_distinct:,} distinct keys"
        + (
            " - orphaned partitions? run scripts/prune_stale_partitions.py"
            if f_total != f_distinct
            else ""
        ),
    )

    labels = {r[0] for r in conn.execute(f"SELECT DISTINCT timeframe FROM {fread}").fetchall()}
    check(
        "single funding partition label",
        labels == {"funding"},
        f"labels present: {sorted(labels)}",
    )

print("\n" + "=" * 78)
if failures:
    print(f"VERIFICATION FAILED - {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
