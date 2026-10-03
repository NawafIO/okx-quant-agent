"""trend_breakout - daily Donchian breakout with an ATR stop (docs/strategies/trend_breakout.md).

Runs on 1h bars (the cost model is calibrated on 1h bars) and aggregates completed UTC days
itself; it decides only on the bar that closes a UTC day, so entries fill at the next day's
first open. Every numeric constant is in DEFAULTS.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import talib

from okxq.contracts import Signal
from okxq.strategy.base import (
    DAY_MS,
    HOUR_MS,
    FloatArray,
    MarketContext,
    Params,
    StrategySpec,
    make_signal,
)

DEFAULTS: dict[str, int | float] = {
    "entry_days": 55,  # breakout channel
    "exit_days": 20,  # opposite channel that closes the trade
    "atr_days": 20,  # Wilder ATR on daily bars
    "stop_atr": 2.0,  # initial stop distance in daily ATRs
    "target_atr": 10.0,  # far target (the Signal contract requires one)
    "warmup_bars": 3600,  # 150 days of 1h bars; +20% = 180 days, the project ceiling
}

#: The pre-registered grid (docs/strategies/trend_breakout.md). Fixed before the first run.
GRID: list[dict[str, int | float]] = [
    {"entry_days": e, "exit_days": x, "stop_atr": s}
    for e in (20, 55)
    for x in (10, 20)
    for s in (2.0, 3.0)
]


def daily(ctx: MarketContext) -> tuple[FloatArray, FloatArray, FloatArray] | None:
    """Completed UTC days as (high, low, close), or None unless this bar closes a day."""
    ts = ctx.view.ts_open_ms
    if (int(ts[-1]) + HOUR_MS) % DAY_MS:
        return None
    day = ts // DAY_MS
    edges = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate(([0], edges))
    # The first group may be a partial day cut by the bounded view: drop it.
    if len(starts) > 1 and int(ts[0]) % DAY_MS:
        starts = starts[1:]
    hi = np.maximum.reduceat(ctx.view.high[starts[0] :], starts - starts[0])
    lo = np.minimum.reduceat(ctx.view.low[starts[0] :], starts - starts[0])
    ends = np.concatenate((starts[1:], [len(ts)])) - 1
    return hi, lo, ctx.view.close[ends]


@dataclass(frozen=True)
class TrendBreakout:
    p: Params
    version: str
    strategy_id: str = "trend_breakout"
    allowed_regimes: tuple[str, ...] = ()

    @property
    def warmup_bars(self) -> int:
        return int(self.p["warmup_bars"])

    def generate(self, ctx: MarketContext) -> Signal | None:
        d = daily(ctx)
        if d is None:
            return None
        hi, lo, cl = d
        n = int(self.p["entry_days"])
        if len(cl) <= max(n, int(self.p["atr_days"])):
            return None
        atr = float(talib.ATR(hi, lo, cl, timeperiod=int(self.p["atr_days"]))[-1])
        if not np.isfinite(atr) or atr <= 0:
            return None
        c = float(cl[-1])
        if c > float(np.max(hi[-n - 1 : -1])):
            return make_signal(
                ctx,
                self,
                side="LONG",
                entry_ref=c,
                stop=c - self.p["stop_atr"] * atr,
                target=c + self.p["target_atr"] * atr,
            )
        if c < float(np.min(lo[-n - 1 : -1])):
            stop = c + self.p["stop_atr"] * atr
            target = c - self.p["target_atr"] * atr
            if target <= 0:
                return None
            return make_signal(ctx, self, side="SHORT", entry_ref=c, stop=stop, target=target)
        return None

    def exit(self, ctx: MarketContext) -> bool:
        d = daily(ctx)
        if d is None or ctx.position is None:
            return False
        hi, lo, cl = d
        m = int(self.p["exit_days"])
        if len(cl) <= m:
            return False
        c = float(cl[-1])
        if ctx.position.side.value == "LONG":
            return c < float(np.min(lo[-m - 1 : -1]))
        return c > float(np.max(hi[-m - 1 : -1]))


SPEC = StrategySpec("trend_breakout", DEFAULTS, lambda p, v: TrendBreakout(p, v))
