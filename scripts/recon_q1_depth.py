"""Q-1: measure actual OKX history depth per timeframe, and backfill throughput.

Writes docs/VENUE_FACTS.md. Public endpoints only; no credentials.

Method. `probe(t)` asks whether a 300-bar window starting at `t` returns any rows. Data is
contiguous from the earliest retained bar to now, so `probe` is monotonic in `t` - False
below the retention/listing boundary, True above it - which makes a binary search valid and
costs ~30 requests per (symbol, timeframe) instead of walking the whole history.

The distinction that matters for R-9: an instrument's `listTime` is when it started trading,
but OKX's history-candles endpoint has its own *retention* per bar size. The binding
constraint is whichever is later, and only measurement distinguishes them.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ccxt

TIMEFRAMES = ["1m", "5m", "1h", "1d"]
# A long-listed major, a second major, and a recently-listed alt - so the report can
# separate "listed late" from "retention cut off".
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

ex = ccxt.okx({"enableRateLimit": True})
markets = ex.load_markets()
NOW = ex.milliseconds()


def ts(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def years(ms_span: int) -> float:
    return ms_span / (365.25 * 24 * 3600 * 1000)


def probe(symbol: str, tf: str, since_ms: int) -> int:
    """Return the number of rows a window starting at ``since_ms`` yields."""
    for attempt in range(4):
        try:
            return len(ex.fetch_ohlcv(symbol, tf, since=max(since_ms, 0), limit=300))
        except ccxt.RateLimitExceeded:
            time.sleep(1.5 * (attempt + 1))
        except ccxt.NetworkError:
            time.sleep(1.0 * (attempt + 1))
    return 0


def earliest_available(symbol: str, tf: str, floor_ms: int) -> tuple[int | None, int]:
    """Binary-search the earliest timestamp with data. Returns (ts_or_None, n_requests)."""
    calls = 0
    lo, hi = floor_ms, NOW
    if probe(symbol, tf, hi - 10 * 86_400_000) == 0:
        return None, 1
    calls += 1
    if probe(symbol, tf, lo) > 0:
        return lo, calls + 1
    calls += 1
    # Invariant: probe(lo) == 0, probe(hi) > 0. Narrow to one hour.
    while hi - lo > 3_600_000:
        mid = (lo + hi) // 2
        calls += 1
        if probe(symbol, tf, mid) > 0:
            hi = mid
        else:
            lo = mid
    return hi, calls


def measure_throughput(symbol: str, tf: str, start_ms: int, pages: int = 8) -> dict[str, Any]:
    """Walk forward `pages` pages of 300 bars, measuring wall time and bars retrieved."""
    duration_ms = ex.parse_timeframe(tf) * 1000
    cursor, total = start_ms, 0
    t0 = time.time()
    for _ in range(pages):
        rows = ex.fetch_ohlcv(symbol, tf, since=cursor, limit=300)
        if not rows:
            break
        total += len(rows)
        cursor = rows[-1][0] + duration_ms
    elapsed = time.time() - t0
    return {
        "pages": pages,
        "bars": total,
        "seconds": round(elapsed, 2),
        "bars_per_sec": round(total / elapsed, 1) if elapsed else 0.0,
    }


print("=" * 78)
print("Q-1 RECONNAISSANCE - OKX history depth")
print(f"ccxt {ccxt.__version__} | now = {ts(NOW)} UTC")
print("=" * 78)

results: list[dict[str, Any]] = []

for symbol in SYMBOLS:
    info = markets[symbol]["info"]
    list_time = info.get("listTime")
    list_ms = int(list_time) if list_time else None
    print(f"\n### {symbol}   listTime = {ts(list_ms)}")
    # Search from a year before listing so a retention cut-off is distinguishable from
    # the listing date itself.
    floor_ms = (
        (list_ms - 365 * 86_400_000)
        if list_ms
        else int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)
    )

    for tf in TIMEFRAMES:
        t0 = time.time()
        earliest, calls = earliest_available(symbol, tf, floor_ms)
        took = time.time() - t0
        if earliest is None:
            print(f"  {tf:>3}: NO DATA RETURNED ({calls} reqs)")
            results.append({"symbol": symbol, "timeframe": tf, "earliest": None})
            continue

        depth_ms = NOW - earliest
        bars = depth_ms // (ex.parse_timeframe(tf) * 1000)
        limited_by = "listing" if list_ms and earliest <= list_ms + 2 * 86_400_000 else "RETENTION"
        print(
            f"  {tf:>3}: earliest={ts(earliest)}  depth={years(depth_ms):.2f}y  "
            f"~{bars:,} bars  limited_by={limited_by}  ({calls} reqs, {took:.1f}s)"
        )
        results.append(
            {
                "symbol": symbol,
                "timeframe": tf,
                "earliest": earliest,
                "earliest_iso": ts(earliest),
                "depth_years": round(years(depth_ms), 2),
                "approx_bars": int(bars),
                "limited_by": limited_by,
            }
        )

print("\n### throughput (BTC/USDT:USDT)")
throughput: dict[str, Any] = {}
for tf in ("1h", "1m"):
    row = next((r for r in results if r["symbol"] == SYMBOLS[0] and r["timeframe"] == tf), None)
    if row and row.get("earliest"):
        tp = measure_throughput(SYMBOLS[0], tf, int(row["earliest"]))
        throughput[tf] = tp
        print(f"  {tf:>3}: {tp['bars']} bars in {tp['seconds']}s = {tp['bars_per_sec']}/s")
        if row["approx_bars"] and tp["bars_per_sec"]:
            est = row["approx_bars"] / tp["bars_per_sec"]
            print(f"       full backfill of one symbol ~= {timedelta(seconds=int(est))}")

print("\n### funding rate history (BTC/USDT:USDT)")
funding: dict[str, Any] = {}
try:
    oldest = None
    cursor = NOW
    pages = 0
    # Walk backward with `until` until the venue stops returning rows.
    while pages < 40:
        batch = ex.fetch_funding_rate_history(SYMBOLS[0], limit=100, params={"until": cursor})
        if not batch:
            break
        pages += 1
        oldest = batch[0]["timestamp"]
        cursor = oldest - 1
    funding = {
        "oldest": oldest,
        "oldest_iso": ts(oldest),
        "pages_walked": pages,
        "depth_years": round(years(NOW - oldest), 2) if oldest else None,
        "interval_hours": 8,
    }
    print(f"  oldest={ts(oldest)}  depth={funding['depth_years']}y  pages={pages}")
except Exception as exc:
    funding = {"error": f"{type(exc).__name__}: {exc}"}
    print(f"  ERROR {funding['error']}")

out = Path("docs/q1_raw.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    json.dumps(
        {
            "measured_at_utc": ts(NOW),
            "ccxt_version": ccxt.__version__,
            "ohlcv": results,
            "throughput": throughput,
            "funding": funding,
        },
        indent=2,
    ),
    encoding="utf-8",
)
print(f"\nraw results -> {out}")
