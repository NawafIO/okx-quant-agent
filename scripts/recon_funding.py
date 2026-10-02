"""Q-1 (funding): measure true funding-rate history depth.

CCXT's okx.fetch_funding_rate_history maps only `since` -> OKX's `before` and ignores
`until`, so passing `until` paginates nowhere - the venue returns the same recent window
every call. OKX's backward cursor is `after` (records *earlier* than the given fundingTime),
which must be passed through `params`.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import ccxt

ex = ccxt.okx({"enableRateLimit": True})
ex.load_markets()
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
NOW = ex.milliseconds()


def ts(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def years(span_ms: int) -> float:
    return span_ms / (365.25 * 24 * 3600 * 1000)


print("--- control: does `after` actually paginate backward? ---")
first = ex.fetch_funding_rate_history("BTC/USDT:USDT", limit=100)
print(f"  page 1 (no cursor): {len(first)} rows, oldest={ts(first[0]['timestamp'])}")
second = ex.fetch_funding_rate_history(
    "BTC/USDT:USDT", limit=100, params={"after": first[0]["timestamp"]}
)
print(f"  page 2 (after=...): {len(second)} rows, oldest={ts(second[0]['timestamp'])}")
moved = second and second[0]["timestamp"] < first[0]["timestamp"]
print(f"  cursor moved backward: {moved}")

results = {}
for symbol in SYMBOLS:
    print(f"\n--- {symbol} ---")
    oldest = None
    cursor: int | None = None
    pages = 0
    total = 0
    t0 = time.time()
    while pages < 600:  # 600 * 100 * 8h would be ~55 years; a safety bound, not a target
        params = {"after": cursor} if cursor is not None else {}
        for attempt in range(4):
            try:
                batch = ex.fetch_funding_rate_history(symbol, limit=100, params=params)
                break
            except (ccxt.RateLimitExceeded, ccxt.NetworkError):
                time.sleep(1.5 * (attempt + 1))
        else:
            break
        if not batch:
            break
        pages += 1
        total += len(batch)
        oldest = batch[0]["timestamp"]
        cursor = oldest
        if len(batch) < 100:
            break
    elapsed = time.time() - t0
    depth = years(NOW - oldest) if oldest else None
    print(
        f"  oldest={ts(oldest)}  depth={depth:.2f}y  rows={total}  pages={pages}  ({elapsed:.1f}s)"
    )
    results[symbol] = {
        "oldest": oldest,
        "oldest_iso": ts(oldest),
        "depth_years": round(depth, 2) if depth else None,
        "rows": total,
        "pages": pages,
        "seconds": round(elapsed, 1),
        "interval_hours": 8,
    }

out = Path("docs/q1_funding_raw.json")
out.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"\nraw -> {out}")
