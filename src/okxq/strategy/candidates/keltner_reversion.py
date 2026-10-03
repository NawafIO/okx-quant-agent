"""keltner_reversion - fade 1h closes outside Keltner bands back to the midline, only when
ADX is low (docs/strategies/keltner_reversion.md).

The ADX filter is a LOCALLY IMPLEMENTED regime gate (ADX below adx_max ~ "range"); the M3
design fact applies to it - ADX lags turns by weeks. It does not consume okxq.analysis.regime.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import talib

from okxq.contracts import Signal
from okxq.strategy.base import MarketContext, Params, StrategySpec, make_signal

DEFAULTS: dict[str, int | float] = {
    "kc_n": 20,  # EMA midline
    "atr_n": 10,  # band width ATR
    "kc_k": 2.0,  # band multiple
    "adx_n": 14,
    "adx_max": 20.0,  # only fade when ADX is below this
    "stop_atr": 1.5,  # stop distance beyond the entry close, in ATRs
    "time_stop_bars": 24,  # exit after this many closed bars held
    "warmup_bars": 500,  # bounded history for EMA/ATR/ADX convergence
}

#: The pre-registered grid (docs/strategies/keltner_reversion.md). Fixed before the first run.
GRID: list[dict[str, int | float]] = [
    {"kc_k": k, "adx_max": a, "time_stop_bars": t}
    for k in (2.0, 2.5)
    for a in (20.0, 25.0)
    for t in (12, 24)
]


@dataclass(frozen=True)
class KeltnerReversion:
    p: Params
    version: str
    strategy_id: str = "keltner_reversion"
    allowed_regimes: tuple[str, ...] = ()

    @property
    def warmup_bars(self) -> int:
        return int(self.p["warmup_bars"])

    def generate(self, ctx: MarketContext) -> Signal | None:
        v = ctx.view
        mid = float(talib.EMA(v.close, timeperiod=int(self.p["kc_n"]))[-1])
        atr = float(talib.ATR(v.high, v.low, v.close, timeperiod=int(self.p["atr_n"]))[-1])
        adx = float(talib.ADX(v.high, v.low, v.close, timeperiod=int(self.p["adx_n"]))[-1])
        if not all(np.isfinite((mid, atr, adx))) or atr <= 0 or adx >= self.p["adx_max"]:
            return None
        c = float(v.close[-1])
        band = self.p["kc_k"] * atr
        if c < mid - band:
            return make_signal(
                ctx, self, side="LONG", entry_ref=c, stop=c - self.p["stop_atr"] * atr, target=mid
            )
        if c > mid + band:
            return make_signal(
                ctx, self, side="SHORT", entry_ref=c, stop=c + self.p["stop_atr"] * atr, target=mid
            )
        return None

    def exit(self, ctx: MarketContext) -> bool:
        return ctx.bars_held is not None and ctx.bars_held >= int(self.p["time_stop_bars"])


SPEC = StrategySpec("keltner_reversion", DEFAULTS, lambda p, v: KeltnerReversion(p, v))
