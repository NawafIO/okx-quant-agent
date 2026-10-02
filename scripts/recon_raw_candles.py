"""Can we ingest OHLCV as exact strings rather than CCXT-parsed floats (rule D-1)?

ccxt.fetch_ohlcv returns float prices, so exactness is lost at the unified-API boundary.
OKX's REST response carries strings. This probes the implicit endpoint and confirms the
before/after cursor semantics for candles (verified separately for funding: `after` walks
backward).
"""

from __future__ import annotations

from datetime import UTC, datetime

import ccxt

ex = ccxt.okx({"enableRateLimit": True})
ex.load_markets()
INST = ex.market("BTC/USDT:USDT")["id"]
print("instId:", INST)


def ts(ms: int | str) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


print("\n--- raw history-candles response shape ---")
resp = ex.publicGetMarketHistoryCandles({"instId": INST, "bar": "1H", "limit": 3})
print("code:", resp.get("code"))
rows = resp.get("data", [])
print("rows:", len(rows))
for r in rows:
    print("  ", r)
print("\ntypes in first row:", [type(x).__name__ for x in rows[0]])
print("price field is str:", isinstance(rows[0][1], str))

print("\n--- ordering (raw) ---")
print("first ts:", ts(rows[0][0]), "last ts:", ts(rows[-1][0]))
print("descending:", int(rows[0][0]) > int(rows[-1][0]))

print("\n--- cursor semantics: `after` should walk BACKWARD ---")
page1 = ex.publicGetMarketHistoryCandles({"instId": INST, "bar": "1H", "limit": 100})["data"]
oldest1 = min(int(r[0]) for r in page1)
page2 = ex.publicGetMarketHistoryCandles(
    {"instId": INST, "bar": "1H", "limit": 100, "after": str(oldest1)}
)["data"]
oldest2 = min(int(r[0]) for r in page2)
print(f"  page1 oldest = {ts(oldest1)}")
print(f"  page2 oldest = {ts(oldest2)}  moved_back={oldest2 < oldest1}")

print("\n--- `confirm` flag marks closed bars ---")
recent = ex.publicGetMarketCandles({"instId": INST, "bar": "1H", "limit": 3})["data"]
for r in recent:
    print(f"   ts={ts(r[0])} confirm={r[-1]!r} (1 = closed)")
