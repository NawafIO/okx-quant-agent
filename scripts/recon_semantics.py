"""Throwaway: establish OKX/CCXT pagination semantics before measuring depth (Q-1).

Questions this answers empirically, rather than from assumption:
  1. Is the host clock skew real, or a measurement artefact?
  2. What limit does OKX actually honour per fetch_ohlcv call?
  3. With `since` set far before listing, does OKX return the earliest available bars
     (one request gives the answer) or an empty list (requiring a search)?
  4. Are returned bars ascending, and does `since` mean >= since?
  5. Does funding-rate history work, and at what interval?
"""

import time
from datetime import UTC, datetime

import ccxt

ex = ccxt.okx({"enableRateLimit": True})
ex.load_markets()
SYM = "BTC/USDT:USDT"


def ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


print("--- 1. clock skew, measured three times ---")
for _ in range(3):
    t0 = ex.milliseconds()
    server = ex.fetch_time()
    t1 = ex.milliseconds()
    # Mid-point of the local interval removes round-trip latency from the estimate.
    print(f"  skew ~= {((t0 + t1) // 2) - server} ms   (rtt {t1 - t0} ms)")

print("\n--- 2. honoured limit (requested -> returned) ---")
for want in (100, 300, 500, 1000):
    try:
        rows = ex.fetch_ohlcv(SYM, "1h", limit=want)
        print(f"  limit={want:>5} -> {len(rows)} rows")
    except Exception as exc:
        print(f"  limit={want:>5} -> ERROR {type(exc).__name__}: {exc}")

print("\n--- 3+4. `since` far in the past (2017-01-01) ---")
since_2017 = int(datetime(2017, 1, 1, tzinfo=UTC).timestamp() * 1000)
for tf in ("1d", "1h", "5m", "1m"):
    try:
        rows = ex.fetch_ohlcv(SYM, tf, since=since_2017, limit=100)
        if rows:
            ascending = all(rows[i][0] < rows[i + 1][0] for i in range(len(rows) - 1))
            print(
                f"  {tf:>3}: {len(rows):>3} rows, first={ts(rows[0][0])}, "
                f"last={ts(rows[-1][0])}, ascending={ascending}"
            )
        else:
            print(f"  {tf:>3}: EMPTY - a search will be needed")
    except Exception as exc:
        print(f"  {tf:>3}: ERROR {type(exc).__name__}: {exc}")

print("\n--- 5. funding rate history ---")
try:
    fr = ex.fetch_funding_rate_history(SYM, limit=100)
    print(f"  {len(fr)} rows; first={ts(fr[0]['timestamp'])}, last={ts(fr[-1]['timestamp'])}")
    if len(fr) > 1:
        deltas = {fr[i + 1]["timestamp"] - fr[i]["timestamp"] for i in range(len(fr) - 1)}
        print(f"  intervals (hours): {sorted(d / 3_600_000 for d in deltas)}")
except Exception as exc:
    print(f"  ERROR {type(exc).__name__}: {exc}")

print("\n--- 5b. funding history with early `since` ---")
try:
    fr = ex.fetch_funding_rate_history(SYM, since=since_2017, limit=100)
    print(f"  {len(fr)} rows; first={ts(fr[0]['timestamp'])}" if fr else "  EMPTY")
except Exception as exc:
    print(f"  ERROR {type(exc).__name__}: {exc}")

print("\n--- 6. single-request latency, 5 samples (1h, limit=100) ---")
for _ in range(5):
    t0 = time.time()
    ex.fetch_ohlcv(SYM, "1h", limit=100)
    print(f"  {(time.time() - t0) * 1000:.0f} ms")
