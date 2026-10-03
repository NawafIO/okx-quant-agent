"""Deterministic market-regime classifier (architecture §8.3) - authoritative, LLM-free.

Labels each CLOSED daily bar ``TREND_UP | TREND_DOWN | RANGE | HIGH_VOL | CRISIS | UNDEFINED``.
The LLM path (M8) may only ever *reduce* risk appetite on disagreement; it cannot change a label.

**Thresholds are frozen BEFORE any real-data run** (Chief Advisor, M3 checkpoint 1): this file
is committed, with :data:`PINNED_REGIME_SHA256`, ahead of the answer keys and ahead of the
first run. They come from first principles stated next to each one, not from looking at how
2021/2022 scored. One documented revision is allowed if validation fails; it is recorded as
a trial, and the revised thresholds must then pass the SEALED second answer key.

Causality (checkpoint-1 findings M-1/M-2):
* every input at day ``t`` uses bars ``<= t`` only; the volatility percentile ranks today's
  value against the TRAILING window strictly before today - never a full-sample percentile;
* an input that is not yet defined yields ``UNDEFINED`` - never a default ``RANGE``;
* a daily label becomes known at that bar's CLOSE, so an hourly consumer may apply it only to
  hours opening at or after the close (:func:`align_to_hourly`). Applying day t's label to the
  hours of day t is look-ahead - the most likely leak in M4.

Deviations from §8.3 inputs (recorded, not omitted - finding M-6): the Hurst exponent, the
ATR percentile and the cross-universe correlation-collapse crisis flag are NOT used. Hurst on
a daily window is too noisy to gate a label; the ATR percentile duplicates the realised-vol
percentile; correlation collapse needs a universe-wide causal correlation feature that does
not exist yet (quant.py is batch-only). Crisis is detected from price speed instead.

**VALIDATION STATUS: FAILED** (docs/M3_DESIGN.md §2). These thresholds failed the
pre-registered BTC key and the SOL/XRP CRISIS/HIGH_VOL transfer keys. Every label is
DESCRIPTIVE. A strategy may consume a label only as a declared parameter set, subject to G-6
perturbation, and must state the label's 2-4 week lag at turns. The one revision is unspent
and the ETH key is still sealed.

Live use must warm up with at least ``LIVE_MIN_HISTORY_DAYS`` of daily history: EMA-50 and
ADX have infinite memory (TA registry ``min_warmup``), so a shorter buffer would label days
differently from research.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt
import talib

from okxq.analysis.ta import REGISTRY, Bars
from okxq.errors import SafetyError

Arr = npt.NDArray[np.float64]
DAY_MS = 86_400_000


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    CRISIS = "CRISIS"
    UNDEFINED = "UNDEFINED"


@dataclass(frozen=True)
class RegimeParams:
    """Threshold values. Unpinned, for SENSITIVITY ANALYSIS ONLY (:func:`classify_with`);
    strategies get labels only through :func:`classify`, which uses the pinned set."""

    # CRISIS - price speed, the one input defined from the first week (so March 2020 is
    # detectable before any volatility history exists). A one-day fall of 10% or a five-day
    # fall of 20% in a major crypto asset is a disorderly market by any reading; both are
    # far outside a normal day (BTC daily sd is roughly 3-4%).
    crisis_1d_return: float = -0.10
    crisis_5d_return: float = -0.20
    # HIGH_VOL - realised vol (20d) in the top 5% of its own trailing year. Relative to the
    # instrument's own history, so it is not a fixed crypto-vs-gold number; top 5% rather than
    # 10% so it marks the exceptional, not one day in ten by construction.
    vol_window: int = 20
    vol_pctl_high: float = 0.95
    vol_rank_lookback: int = 365
    vol_rank_min_history: int = 180
    # TREND - EMA(20) vs EMA(50) for direction, the 10-day slope of EMA(50) for persistence,
    # and ADX(14) >= 20 for strength (Wilder's conventional "trending" floor).
    ema_fast: int = 20
    ema_slow: int = 50
    slope_days: int = 10
    adx_period: int = 14
    adx_trend: float = 20.0
    # Bars before EMA-50 + slope and ADX exist at all (labels before this are UNDEFINED unless
    # CRISIS fires).
    min_bars: int = 60
    # A daily label is known at its bar's CLOSE; it applies to hourly bars opening at or after.
    alignment: str = "daily label applies from its bar's close (ts_open + 1d) onward"

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class FrozenRegime(RegimeParams):
    """The pinned thresholds. Any other values refuse to construct."""

    def __post_init__(self) -> None:
        if self.sha256() != PINNED_REGIME_SHA256:
            raise RegimeTamperError(
                f"regime thresholds differ from the frozen pin (got {self.sha256()})"
            )


#: SHA-256 of FrozenRegime().canonical_json(), frozen BEFORE the first real-data run.
PINNED_REGIME_SHA256 = "260beabd593e575f2c7ca08d34f0abb602910b0a42d438386874e08c9a221938"


class RegimeTamperError(SafetyError):
    """Regime thresholds were changed without re-pinning."""


LIVE_MIN_HISTORY_DAYS = max(REGISTRY["ema_50"].min_warmup, REGISTRY["adx_14"].min_warmup)


@dataclass(frozen=True)
class RegimeInputs:
    r1: Arr
    r5: Arr
    vol_rank: Arr
    ema_fast: Arr
    ema_slow: Arr
    slope: Arr
    adx: Arr


def inputs(b: Bars, f: RegimeParams) -> RegimeInputs:
    """Every series is causal: element t uses bars 0..t only."""
    c = b.close
    n = len(c)
    r1 = np.full(n, np.nan)
    r1[1:] = c[1:] / c[:-1] - 1
    r5 = np.full(n, np.nan)
    r5[5:] = c[5:] / c[:-5] - 1
    logret = np.full(n, np.nan)
    logret[1:] = np.log(c[1:] / c[:-1])
    vol = np.full(n, np.nan)
    for t in range(f.vol_window, n):
        vol[t] = np.std(logret[t - f.vol_window + 1 : t + 1], ddof=1)
    rank = np.full(n, np.nan)
    for t in range(n):
        if np.isnan(vol[t]):
            continue
        past = vol[max(0, t - f.vol_rank_lookback) : t]  # STRICTLY before t
        past = past[~np.isnan(past)]
        if len(past) >= f.vol_rank_min_history:
            rank[t] = float(np.mean(past < vol[t]))
    ef: Arr = talib.EMA(c, timeperiod=f.ema_fast)
    es: Arr = talib.EMA(c, timeperiod=f.ema_slow)
    slope = np.full(n, np.nan)
    slope[f.slope_days :] = es[f.slope_days :] / es[: -f.slope_days] - 1
    adx: Arr = talib.ADX(b.high, b.low, b.close, timeperiod=f.adx_period)
    return RegimeInputs(r1, r5, rank, ef, es, slope, adx)


def classify(b: Bars) -> list[Regime]:
    """One label per daily bar with the PINNED thresholds - the only entry point a strategy
    may use. Priority CRISIS > HIGH_VOL > TREND > RANGE; UNDEFINED when a needed input does
    not exist yet (CRISIS can fire from day 5, before anything else)."""
    return classify_with(b, FROZEN_REGIME)


def classify_with(b: Bars, f: RegimeParams) -> list[Regime]:
    """Same rules under arbitrary thresholds - sensitivity analysis only (finding M-3).
    Strategy and research code may not import this (isolation guard)."""
    x = inputs(b, f)
    out: list[Regime] = []
    for t in range(len(b.close)):
        if (not np.isnan(x.r1[t]) and x.r1[t] <= f.crisis_1d_return) or (
            not np.isnan(x.r5[t]) and x.r5[t] <= f.crisis_5d_return
        ):
            out.append(Regime.CRISIS)
            continue
        needed = (x.vol_rank[t], x.ema_fast[t], x.ema_slow[t], x.slope[t], x.adx[t])
        if t < f.min_bars or any(np.isnan(v) for v in needed):
            out.append(Regime.UNDEFINED)
            continue
        if x.vol_rank[t] >= f.vol_pctl_high:
            out.append(Regime.HIGH_VOL)
        elif x.adx[t] >= f.adx_trend and x.ema_fast[t] > x.ema_slow[t] and x.slope[t] > 0:
            out.append(Regime.TREND_UP)
        elif x.adx[t] >= f.adx_trend and x.ema_fast[t] < x.ema_slow[t] and x.slope[t] < 0:
            out.append(Regime.TREND_DOWN)
        else:
            out.append(Regime.RANGE)
    return out


def align_to_hourly(
    daily_ts_open_ms: list[int], labels: list[Regime], hourly_ts_open_ms: list[int]
) -> list[Regime]:
    """Label each hourly bar with the latest daily label whose bar has CLOSED by the hour's
    open (daily close = ts_open + 1 day). Hours before the first daily close are UNDEFINED.
    Never the label of the day the hour belongs to - that day has not closed yet."""
    out: list[Regime] = []
    j = -1
    for h in hourly_ts_open_ms:
        while j + 1 < len(daily_ts_open_ms) and daily_ts_open_ms[j + 1] + DAY_MS <= h:
            j += 1
        out.append(labels[j] if j >= 0 else Regime.UNDEFINED)
    return out


FROZEN_REGIME = FrozenRegime()
