"""Technical-analysis engine (architecture §9.2) - pure functions over CLOSED bars.

Every indicator is ``f(Bars) -> float64 array`` aligned with the input: value ``i`` uses bars
``0..i`` only. ``REGISTRY`` lists them all, and the T-1 property test iterates the registry,
so no indicator can be added without being tested for look-ahead
(``f(x[:n])[-1] == f(x)[n-1]``). An indicator that fails is removed, not patched (roadmap M3).

TA-Lib is the primary implementation (Q-3, verified at M1). Where TA-Lib has no equivalent
(Keltner, rolling VWAP, Supertrend), the implementation here is validated against an
independent re-computation in the tests (T-2).

Deliberately absent:
* swing highs/lows and pivots - a pivot needs bars to its RIGHT, so it is look-ahead by
  definition. A pivot confirmed k bars later is a different (causal) indicator, not added yet;
* CVD - needs trade-side (aggressor) data, which the store does not hold;
* volume profile - no consumer yet;
* OBV - its level is a cumulative sum from the first bar, so it depends on where the series
  starts; it failed the warm-up test (see REGISTRY) and was removed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import talib

Arr = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Bars:
    """Closed OHLCV bars as contiguous float64 arrays."""

    open: Arr
    high: Arr
    low: Arr
    close: Arr
    volume: Arr

    def head(self, n: int) -> Bars:
        return Bars(self.open[:n], self.high[:n], self.low[:n], self.close[:n], self.volume[:n])

    @classmethod
    def of(cls, o: object, h: object, low: object, c: object, v: object) -> Bars:
        def arr(x: object) -> Arr:
            return np.ascontiguousarray(np.asarray(x, dtype=np.float64))

        return cls(arr(o), arr(h), arr(low), arr(c), arr(v))


# --- own implementations (validated against independent re-computation, T-2) ------------


def rolling_vwap(b: Bars, n: int = 24) -> Arr:
    """Volume-weighted typical price over the last ``n`` bars; NaN where volume sums to 0."""
    tp = (b.high + b.low + b.close) / 3.0
    pv = np.cumsum(np.concatenate(([0.0], tp * b.volume)))
    vv = np.cumsum(np.concatenate(([0.0], b.volume)))
    out = np.full(len(b.close), np.nan)
    for i in range(n - 1, len(b.close)):
        vol = vv[i + 1] - vv[i + 1 - n]
        if vol > 0:
            out[i] = (pv[i + 1] - pv[i + 1 - n]) / vol
    return out


def keltner(b: Bars, n: int = 20, atr_n: int = 10, k: float = 2.0) -> tuple[Arr, Arr, Arr]:
    mid: Arr = talib.EMA(b.close, timeperiod=n)
    atr: Arr = talib.ATR(b.high, b.low, b.close, timeperiod=atr_n)
    return mid - k * atr, mid, mid + k * atr


def supertrend(b: Bars, n: int = 10, k: float = 3.0) -> tuple[Arr, Arr]:
    """Classic Supertrend: (line, direction +1/-1). Recursive on closed bars only."""
    atr: Arr = talib.ATR(b.high, b.low, b.close, timeperiod=n)
    hl2 = (b.high + b.low) / 2.0
    size = len(b.close)
    line = np.full(size, np.nan)
    direction = np.full(size, np.nan)
    upper = lower = np.nan
    for i in range(size):
        if np.isnan(atr[i]):
            continue
        bu, bl = hl2[i] + k * atr[i], hl2[i] - k * atr[i]
        prev_close = b.close[i - 1] if i > 0 else b.close[i]
        upper = bu if np.isnan(upper) or bu < upper or prev_close > upper else upper
        lower = bl if np.isnan(lower) or bl > lower or prev_close < lower else lower
        prev_dir = direction[i - 1] if i > 0 and not np.isnan(direction[i - 1]) else 1.0
        if prev_dir > 0:
            d = -1.0 if b.close[i] < lower else 1.0
        else:
            d = 1.0 if b.close[i] > upper else -1.0
        direction[i] = d
        line[i] = lower if d > 0 else upper
    return line, direction


# --- registry: every entry is T-1 property-tested -----------------------------------------


def _first(f: Callable[[Bars], tuple[Arr, ...]], i: int) -> Callable[[Bars], Arr]:
    return lambda b: f(b)[i]


def _macd(b: Bars) -> tuple[Arr, Arr, Arr]:
    m, s, h = talib.MACD(b.close, fastperiod=12, slowperiod=26, signalperiod=9)
    return m, s, h


def _bbands(b: Bars) -> tuple[Arr, Arr, Arr]:
    u, m, lo = talib.BBANDS(b.close, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    return u, m, lo


def _stoch(b: Bars) -> tuple[Arr, Arr]:
    k, d = talib.STOCH(b.high, b.low, b.close, fastk_period=14, slowk_period=3, slowd_period=3)
    return k, d


@dataclass(frozen=True)
class Indicator:
    fn: Callable[[Bars], Arr]
    #: Bars of history after which a value computed from a BOUNDED buffer agrees with one
    #: computed from the full history (within 1e-6 relative). Wilder/EMA smoothing has
    #: infinite memory, so research (full history) and a live buffer agree only past this
    #: point. Live use (M8) must warm up with at least this much history. Declared with a
    #: margin over the measured convergence and verified by test (Chief Advisor, M3 cp-1).
    min_warmup: int
    #: "causal": a per-bar series a strategy may consume. (Batch statistics live in quant.)
    kind: str = "causal"

    def __call__(self, b: Bars) -> Arr:
        _assert_talib_state()
        return self.fn(b)


#: TA-Lib's unstable period is GLOBAL MUTABLE STATE (TA_SetUnstablePeriod). Pin it, and
#: refuse to compute if anything changed it (Chief Advisor, M3 checkpoint 1).
TALIB_UNSTABLE_PERIOD = 0
# MACD has no entry of its own: its internal EMAs follow the "EMA" setting.
_PINNED = ("EMA", "RSI", "ATR", "ADX", "PLUS_DI", "MINUS_DI")
talib.set_unstable_period("ALL", TALIB_UNSTABLE_PERIOD)  # type: ignore[attr-defined]


def _assert_talib_state() -> None:
    for name in _PINNED:
        if talib.get_unstable_period(name) != TALIB_UNSTABLE_PERIOD:  # type: ignore[attr-defined]
            raise RuntimeError(f"TA-Lib unstable period for {name} was changed at runtime")


def _ind(fn: Callable[[Bars], Arr], warmup: int) -> Indicator:
    return Indicator(fn, warmup)


#: Removed: "obv". Its LEVEL depends on where the series starts (a cumulative sum from bar 0),
#: so a bounded buffer never agrees with the full history - it failed the warm-up test and is
#: removed, not patched (roadmap M3). A windowed OBV change would be a different indicator.
REGISTRY: dict[str, Indicator] = {
    # trend
    "sma_20": _ind(lambda b: talib.SMA(b.close, timeperiod=20), 60),
    "ema_20": _ind(lambda b: talib.EMA(b.close, timeperiod=20), 250),
    "ema_50": _ind(lambda b: talib.EMA(b.close, timeperiod=50), 450),
    "macd": _ind(_first(_macd, 0), 350),
    "macd_signal": _ind(_first(_macd, 1), 350),
    "macd_hist": _ind(_first(_macd, 2), 350),
    "adx_14": _ind(lambda b: talib.ADX(b.high, b.low, b.close, timeperiod=14), 450),
    "plus_di_14": _ind(lambda b: talib.PLUS_DI(b.high, b.low, b.close, timeperiod=14), 350),
    "minus_di_14": _ind(lambda b: talib.MINUS_DI(b.high, b.low, b.close, timeperiod=14), 350),
    "donchian_high_20": _ind(lambda b: talib.MAX(b.high, timeperiod=20), 60),
    "donchian_low_20": _ind(lambda b: talib.MIN(b.low, timeperiod=20), 60),
    "supertrend": _ind(_first(supertrend, 0), 300),
    "supertrend_dir": _ind(_first(supertrend, 1), 300),
    # momentum
    "rsi_14": _ind(lambda b: talib.RSI(b.close, timeperiod=14), 350),
    "stoch_k": _ind(_first(_stoch, 0), 60),
    "stoch_d": _ind(_first(_stoch, 1), 60),
    "cci_20": _ind(lambda b: talib.CCI(b.high, b.low, b.close, timeperiod=20), 60),
    "roc_10": _ind(lambda b: talib.ROC(b.close, timeperiod=10), 60),
    # volatility
    "atr_14": _ind(lambda b: talib.ATR(b.high, b.low, b.close, timeperiod=14), 350),
    "bb_upper": _ind(_first(_bbands, 0), 60),
    "bb_mid": _ind(_first(_bbands, 1), 60),
    "bb_lower": _ind(_first(_bbands, 2), 60),
    "keltner_lower": _ind(_first(keltner, 0), 300),
    "keltner_mid": _ind(_first(keltner, 1), 300),
    "keltner_upper": _ind(_first(keltner, 2), 300),
    # volume
    "vwap_24": _ind(rolling_vwap, 60),
}
