"""Can funding be reconstructed from data that *does* have multi-year depth?

OKX funding is driven by the perpetual's premium over the index. Mark-price and index-price
candles are available through fetch_ohlcv with params price='mark'/'index'. If those carry
years of history, a modelled funding series becomes possible as a fallback - a *model*, not
the realised rate, so it carries estimation error that must be stress-tested.

This script only establishes availability and depth. It does not fit or validate a model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import ccxt

ex = ccxt.okx({"enableRateLimit": True})
ex.load_markets()
SYM = "BTC/USDT:USDT"
NOW = ex.milliseconds()


def ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def years(span: int) -> float:
    return span / (365.25 * 24 * 3600 * 1000)


def earliest(price_type: str | None, tf: str = "1h") -> int | None:
    """Binary-search earliest available bar for a given price series."""
    params = {"price": price_type} if price_type else {}

    def probe(t: int) -> bool:
        try:
            return len(ex.fetch_ohlcv(SYM, tf, since=max(t, 0), limit=100, params=params)) > 0
        except Exception:
            return False

    lo = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)
    hi = NOW
    if not probe(hi - 10 * 86_400_000):
        return None
    if probe(lo):
        return lo
    while hi - lo > 3_600_000:
        mid = (lo + hi) // 2
        if probe(mid):
            hi = mid
        else:
            lo = mid
    return hi


out = {}
for label, ptype in (("last", None), ("mark", "mark"), ("index", "index")):
    e = earliest(ptype)
    if e:
        print(f"  {label:>5} 1h candles: earliest={ts(e)}  depth={years(NOW - e):.2f}y")
        out[label] = {"earliest_iso": ts(e), "depth_years": round(years(NOW - e), 2)}
    else:
        print(f"  {label:>5} 1h candles: UNAVAILABLE")
        out[label] = None

Path("docs/q1_proxy_raw.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("\nraw -> docs/q1_proxy_raw.json")
