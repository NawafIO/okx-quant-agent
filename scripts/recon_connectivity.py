"""Throwaway connectivity probe: can we reach OKX public endpoints at all?

Public endpoints only. No credentials are constructed or read.
"""

import time

import ccxt

ex = ccxt.okx({"enableRateLimit": True})
print("ccxt version:", ccxt.__version__)

t0 = time.time()
markets = ex.load_markets()
print(f"load_markets OK in {time.time() - t0:.2f}s - {len(markets)} markets")

swaps = [
    m for m in markets.values() if m.get("swap") and m.get("quote") == "USDT" and m.get("active")
]
print("active USDT-margined swaps:", len(swaps))
print("examples:", [m["symbol"] for m in swaps[:5]])

t0 = time.time()
server_ms = ex.fetch_time()
local_ms = ex.milliseconds()
print(f"fetch_time OK in {time.time() - t0:.2f}s; clock skew = {local_ms - server_ms} ms")

bars = ex.fetch_ohlcv("BTC/USDT:USDT", timeframe="1h", limit=5)
print("fetch_ohlcv sample rows:", len(bars))
print("last bar:", bars[-1] if bars else None)
