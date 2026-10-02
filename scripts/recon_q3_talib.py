"""Q-3: does TA-Lib actually work on this host, or is a fallback required?

Installation succeeding is not the question - importing the C extension and producing
correct numbers is. This computes two indicators against a hand-checkable series.
"""

from __future__ import annotations

import numpy as np

print("--- import ---")
try:
    import talib

    print(f"  talib OK, version {talib.__version__}")
    print(f"  functions available: {len(talib.get_functions())}")
except Exception as exc:
    print(f"  talib IMPORT FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(1) from exc

print("\n--- correctness: SMA(5) on a linear ramp ---")
series = np.arange(1.0, 11.0)  # 1..10
sma = talib.SMA(series, timeperiod=5)
# SMA(5) of 1..5 = 3; of 6..10 = 8. First 4 values are NaN.
print(f"  input  : {series.tolist()}")
print(f"  SMA(5) : {[None if np.isnan(v) else round(v, 4) for v in sma]}")
assert np.isnan(sma[:4]).all(), "first 4 values must be NaN"
assert abs(sma[4] - 3.0) < 1e-12, f"SMA(5)[4] should be 3.0, got {sma[4]}"
assert abs(sma[9] - 8.0) < 1e-12, f"SMA(5)[9] should be 8.0, got {sma[9]}"
print("  SMA matches hand calculation")

print("\n--- correctness: RSI bounds on random walk ---")
rng = np.random.default_rng(42)
walk = 100 + np.cumsum(rng.normal(0, 1, 500))
rsi = talib.RSI(walk, timeperiod=14)
valid = rsi[~np.isnan(rsi)]
print(f"  RSI range: [{valid.min():.2f}, {valid.max():.2f}] over {len(valid)} values")
assert valid.min() >= 0 and valid.max() <= 100, "RSI must stay within [0, 100]"
print("  RSI within [0, 100]")

print("\n--- ATR (needs high/low/close) ---")
high = walk + 1.0
low = walk - 1.0
atr = talib.ATR(high, low, walk, timeperiod=14)
print(f"  ATR last value: {atr[-1]:.4f}")
assert not np.isnan(atr[-1]) and atr[-1] > 0

print("\nQ-3 RESULT: TA-Lib works on this host from a prebuilt wheel.")
