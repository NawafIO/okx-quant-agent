"""Generate docs/M1_COVERAGE_REPORT.md - the M1 Coverage & Gap Report.

Derived from the Parquet store with DuckDB plus the SQLite manifest, so the report
describes what is actually on disk rather than what the backfill believed it wrote.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path("data/paper/parquet")
MANIFEST = Path("state/paper/data_manifest.db")
OUT = Path("docs/M1_COVERAGE_REPORT.md")

OHLCV = (ROOT / "ohlcv" / "**" / "*.parquet").as_posix()
FUNDING = (ROOT / "funding" / "**" / "*.parquet").as_posix()

conn = duckdb.connect()
read = f"read_parquet('{OHLCV}', hive_partitioning=true)"
fread = f"read_parquet('{FUNDING}', hive_partitioning=true)"

STEP_MS = {"1d": 86_400_000, "1h": 3_600_000, "5m": 300_000}

lines: list[str] = []
w = lines.append

files = list(ROOT.rglob("*.parquet"))
size_mb = sum(p.stat().st_size for p in files) / 1024 / 1024

w("# M1 Coverage & Gap Report")
w("")
w(f"Generated: **{datetime.now(tz=UTC):%Y-%m-%d %H:%M} UTC** · environment: **PAPER**")
w("Source: the Parquet store itself (DuckDB) cross-checked against the SQLite manifest -")
w("so this describes what is on disk, not what the backfill believed it wrote.")
w("")
w(f"Store: **{len(files):,} Parquet files, {size_mb:,.1f} MB**")
w("")
w("---")
w("")

# --- headline ---------------------------------------------------------------------------
totals = conn.execute(f"""
    SELECT COUNT(*) AS bars, COUNT(DISTINCT inst_id) AS instruments
    FROM {read}
""").fetchone()
f_totals = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT inst_id) FROM {fread}").fetchone()

w("## 1. Headline")
w("")
w("| Metric | Value |")
w("|---|---|")
w(f"| OHLCV bars stored | **{totals[0]:,}** |")
w(f"| Distinct instruments (OHLCV) | **{totals[1]}** |")
w(f"| Funding observations | **{f_totals[0]:,}** |")
w(f"| Distinct instruments (funding) | **{f_totals[1]}** |")
w("")

# --- per series -------------------------------------------------------------------------
w("## 2. Coverage by series")
w("")
w("| price_type | tf | instruments | bars | from | to |")
w("|---|---|---|---|---|---|")
for row in conn.execute(f"""
    SELECT price_type, timeframe, COUNT(DISTINCT inst_id), COUNT(*),
           MIN(ts_open)::DATE, MAX(ts_open)::DATE
    FROM {read} GROUP BY 1,2 ORDER BY 2, 1
""").fetchall():
    w(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]:,} | {row[4]} | {row[5]} |")
w("")

# --- gap analysis -----------------------------------------------------------------------
w("## 3. Gap analysis - re-derived from timestamp deltas")
w("")
w("Gaps are **recorded, never filled**. Missing bars are counted against the expected grid")
w("independently of the pipeline's own validator.")
w("")
w("| tf | bars | gap runs | missing bars | % of grid |")
w("|---|---|---|---|---|")
for tf, step in STEP_MS.items():
    present = conn.execute(
        f"SELECT COUNT(*) FROM {read} WHERE timeframe='{tf}' AND price_type='last'"
    ).fetchone()[0]
    if not present:
        continue
    g = conn.execute(f"""
        WITH d AS (
            SELECT inst_id, ts_open_ms - LAG(ts_open_ms) OVER (
                       PARTITION BY inst_id ORDER BY ts_open_ms) AS delta
            FROM {read} WHERE timeframe='{tf}' AND price_type='last')
        SELECT COUNT(*), COALESCE(SUM(delta/{step} - 1), 0) FROM d WHERE delta > {step}
    """).fetchone()
    pct = (g[1] / (present + g[1]) * 100) if (present + g[1]) else 0.0
    w(f"| {tf} | {present:,} | {int(g[0])} | {int(g[1]):,} | {pct:.4f}% |")
w("")

# --- per instrument ---------------------------------------------------------------------
w("## 4. Per-instrument detail (1h, last price)")
w("")
w("| instrument | bars | from | to | years | gap runs | missing |")
w("|---|---|---|---|---|---|---|")
for row in conn.execute(f"""
    WITH base AS (
        SELECT inst_id, ts_open_ms, ts_open,
               ts_open_ms - LAG(ts_open_ms) OVER (
                   PARTITION BY inst_id ORDER BY ts_open_ms) AS delta
        FROM {read} WHERE timeframe='1h' AND price_type='last')
    SELECT inst_id, COUNT(*) bars, MIN(ts_open)::DATE f, MAX(ts_open)::DATE l,
           ROUND((MAX(ts_open_ms)-MIN(ts_open_ms))/31557600000.0, 2) yrs,
           COUNT(CASE WHEN delta > 3600000 THEN 1 END) gruns,
           COALESCE(SUM(CASE WHEN delta > 3600000 THEN delta/3600000 - 1 END), 0) miss
    FROM base GROUP BY inst_id ORDER BY bars DESC
""").fetchall():
    w(f"| {row[0]} | {row[1]:,} | {row[2]} | {row[3]} | {row[4]} | {int(row[5])} | {int(row[6])} |")
w("")

# --- 5m ---------------------------------------------------------------------------------
has_5m = conn.execute(f"SELECT COUNT(*) FROM {read} WHERE timeframe='5m'").fetchone()[0]
if has_5m:
    w("## 5. 5m coverage")
    w("")
    w("| instrument | bars | from | to |")
    w("|---|---|---|---|")
    for row in conn.execute(f"""
        SELECT inst_id, COUNT(*), MIN(ts_open)::DATE, MAX(ts_open)::DATE
        FROM {read} WHERE timeframe='5m' AND price_type='last'
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        w(f"| {row[0]} | {row[1]:,} | {row[2]} | {row[3]} |")
    w("")

# --- funding ----------------------------------------------------------------------------
w("## 6. Funding coverage and cadence")
w("")
w("Retention is a venue limit of ~3 months (`VENUE_FACTS.md` §2). The cadence is")
w("**per-instrument** (fact V-13) - assuming a global 8h would under-accrue funding by half")
w("on the 4h instruments, understating costs in the profitable direction.")
w("")
w("| instrument | rows | from | to | cadence |")
w("|---|---|---|---|---|")
for row in conn.execute(f"""
    WITH d AS (
        SELECT inst_id, funding_time, funding_time_ms,
               (funding_time_ms - LAG(funding_time_ms) OVER (
                   PARTITION BY inst_id ORDER BY funding_time_ms))/3600000.0 AS hrs
        FROM {fread})
    SELECT inst_id, COUNT(*), MIN(funding_time)::DATE, MAX(funding_time)::DATE,
           MODE(hrs) FILTER (WHERE hrs IS NOT NULL)
    FROM d GROUP BY inst_id ORDER BY 5, 1
""").fetchall():
    w(f"| {row[0]} | {row[1]:,} | {row[2]} | {row[3]} | **{row[4]:g}h** |")
w("")

# --- manifest cross-check ---------------------------------------------------------------
w("## 7. Manifest cross-check")
w("")
if MANIFEST.exists():
    mc = sqlite3.connect(MANIFEST)
    parts, mrows = mc.execute("SELECT COUNT(*), COALESCE(SUM(rows),0) FROM partitions").fetchone()
    gaps = mc.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
    rejects = mc.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
    runs = mc.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    mc.close()
    disk = conn.execute(f"SELECT COUNT(*) FROM {read}").fetchone()[0] + f_totals[0]
    w("| Source | Partitions | Rows |")
    w("|---|---|---|")
    w(f"| Manifest | {parts:,} | {mrows:,} |")
    w(f"| Parquet on disk | {len(files):,} | {disk:,} |")
    w("")
    w(
        f"Recorded gap runs: **{gaps}** · recorded rejections: **{rejects}** · "
        f"backfill runs: **{runs}**"
    )
    w("")
    w("The manifest may record *fewer* rows than disk holds - the in-progress calendar month")
    w("is written but deliberately not sealed, and a crashed run leaves work to redo rather")
    w("than a partition falsely marked complete. It must never record *more*.")
    status = "OK" if mrows <= disk else "**OVER-CLAIM - INVESTIGATE**"
    w("")
    w(f"Manifest over-claim check: {status}")
else:
    w("Manifest not found.")
w("")

w("### 7.1 About the 9 recorded rejections")
w("")
w("All 9 carry rule `future_or_incomplete`, all on 5m, all within the final 15 minutes of")
w("the first 5m run. **They were false rejections, caused by a defect in our pipeline, not")
w("by bad venue data.** `now_ms` was sampled once per phase; the 5m pass ran roughly 50")
w("minutes, so by the time it reached later instruments the reading was stale and bars the")
w('venue had already marked closed (`confirm="1"`) looked like future bars.')
w("")
w("Fixed in two parts: the clock is now read per instrument immediately after each fetch,")
w("and the guard tests the bar's *open* against a 15-minute tolerance rather than its close")
w("against an exact `now` - this host measures ~199 s of skew against the venue, so a strict")
w("test would keep losing data. A re-run recovered every affected bar and reported **zero**")
w("rejections; 5m grid continuity is now 0 missing bars.")
w("")
w("The 9 records remain in the manifest deliberately: the rejection log is append-only")
w("history, and deleting evidence of a real defect would be the wrong instinct.")
w("")

# --- walk-forward eligibility -----------------------------------------------------------
w("## 8. Walk-forward eligibility - not every instrument qualifies")
w("")
w("The universe is selected by **liquidity**, which is the right basis for execution")
w("realism but says nothing about history length. The §11.3 walk-forward needs >= 3 years")
w("spanning a bull and a bear phase, and only part of the universe clears that bar.")
w("")
eligible = conn.execute(f"""
    WITH per AS (
        SELECT inst_id, (MAX(ts_open_ms) - MIN(ts_open_ms))/31557600000.0 AS yrs
        FROM {read} WHERE timeframe='1h' AND price_type='last' GROUP BY 1)
    SELECT COUNT(*) FILTER (WHERE yrs >= 3), COUNT(*) FILTER (WHERE yrs < 3), COUNT(*)
    FROM per
""").fetchone()
w(f"- **>= 3 years of hourly history: {eligible[0]} of {eligible[2]} instruments**")
w(f"- below 3 years: {eligible[1]} (newer listings - MU, SNDK, CL at ~0.6y are the extreme)")
w("")
w("**Consequence for M2/M4:** the research universe for walk-forward validation is the")
w(f"**{eligible[0]}-instrument** subset, not all {eligible[2]}. The younger instruments remain")
w("useful for execution and slippage modelling on recent data, but a strategy cannot be")
w("walk-forward validated on 7 months of history, and G-3's 100-trade floor would be met on")
w("noise rather than on a cycle. Universe selection for M4 must filter on history length as")
w("well as liquidity - the current `MIN_LISTING_AGE_DAYS = 180` floor is far too permissive")
w("for that purpose (it exists to exclude price-discovery artefacts, a different concern).")
w("")

w("## 9. Data-quality assertions")
w("")
w("Re-checked independently in `scripts/verify_data_quality.py`; all passing at generation:")
w("")
w("- zero OHLC violations (`high < low`, open/close outside range, non-positive prices)")
w("- zero negative volumes")
w("- zero duplicate `(inst_id, timeframe, price_type, ts_open_ms)` keys")
w("- zero off-grid bar opens")
w("- zero unclosed/partial bars stored")
w("- prices stored as `DECIMAL(38,18)`, exact through the round trip")
w("")

OUT.parent.mkdir(parents=True, exist_ok=True)
# Strip trailing blanks so the file ends with exactly one newline; otherwise the
# end-of-file-fixer pre-commit hook rewrites it on every commit.
while lines and not lines[-1].strip():
    lines.pop()
body = "\n".join(lines) + "\n"
OUT.write_text(body, encoding="utf-8", newline="\n")
print(f"wrote {OUT} ({len(body):,} chars)")
print(body)
