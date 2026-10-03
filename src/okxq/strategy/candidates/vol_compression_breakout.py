"""vol_compression_breakout - after Bollinger bandwidth compresses into the bottom of its own
trailing range, trade the break of the compression range (docs/strategies/
vol_compression_breakout.md). 1h bars.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import talib

from okxq.contracts import Signal
from okxq.strategy.base import MarketContext, Params, StrategySpec, make_signal

DEFAULTS: dict[str, int | float] = {
    "bb_n": 20,
    "bb_k": 2.0,
    "rank_bars": 2160,  # trailing 90 days for the bandwidth percentile
    "squeeze_q": 0.10,  # previous bar's bandwidth in the bottom 10% of the trailing window
    "range_bars": 24,  # the compression range whose break is traded
    "target_r": 2.0,  # target at this multiple of the initial risk
    "time_stop_bars": 48,
    "warmup_bars": 3000,  # covers rank_bars + bb_n under +20% of either
}

#: The pre-registered grid (docs/strategies/vol_compression_breakout.md). Fixed before the
#: first run.
GRID: list[dict[str, int | float]] = [
    {"squeeze_q": q, "range_bars": r, "target_r": t}
    for q in (0.05, 0.10)
    for r in (12, 24)
    for t in (1.5, 3.0)
]


@dataclass(frozen=True)
class VolCompressionBreakout:
    p: Params
    version: str
    strategy_id: str = "vol_compression_breakout"
    allowed_regimes: tuple[str, ...] = ()

    @property
    def warmup_bars(self) -> int:
        return int(self.p["warmup_bars"])

    def generate(self, ctx: MarketContext) -> Signal | None:
        v = ctx.view
        k = float(self.p["bb_k"])
        up, mid, lo = talib.BBANDS(v.close, timeperiod=int(self.p["bb_n"]), nbdevup=k, nbdevdn=k)
        bw = (up - lo) / mid
        n_rank, n_range = int(self.p["rank_bars"]), int(self.p["range_bars"])
        prev = bw[:-1]
        past = prev[-n_rank - 1 : -1]
        past = past[np.isfinite(past)]
        if len(past) < n_rank or not np.isfinite(prev[-1]):
            return None
        if float(np.mean(past < prev[-1])) > self.p["squeeze_q"]:
            return None
        hi = float(np.max(v.high[-n_range - 1 : -1]))
        lo_r = float(np.min(v.low[-n_range - 1 : -1]))
        c = float(v.close[-1])
        if c > hi:
            risk = c - lo_r
            return make_signal(
                ctx, self, side="LONG", entry_ref=c, stop=lo_r, target=c + self.p["target_r"] * risk
            )
        if c < lo_r:
            risk = hi - c
            target = c - self.p["target_r"] * risk
            if target <= 0:
                return None
            return make_signal(ctx, self, side="SHORT", entry_ref=c, stop=hi, target=target)
        return None

    def exit(self, ctx: MarketContext) -> bool:
        return ctx.bars_held is not None and ctx.bars_held >= int(self.p["time_stop_bars"])


SPEC = StrategySpec("vol_compression_breakout", DEFAULTS, lambda p, v: VolCompressionBreakout(p, v))
