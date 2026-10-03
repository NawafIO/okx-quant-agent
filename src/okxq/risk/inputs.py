"""Frozen inputs for one decision cycle (M5_DESIGN §1, §11).

No field has a default: a caller cannot forget the kill-switch state or a halt basis and get
a permissive value. The engine validates every value itself (default deny) - these records
do not, because the engine must reject garbage, not crash on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from okxq.contracts import Env, RegimeLabel, SentimentScore, Side


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: Side
    qty_base: Decimal
    stop: Decimal
    mark: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    env: Env
    #: Decision time; proposal expiry and every age are measured from it, never the clock.
    cycle_ts_ms: int
    #: Read fresh by snapshot construction each cycle (RC-01).
    kill_switch_engaged: bool
    #: Conservative equity frozen for the cycle: realised + unrealised LOSSES only.
    equity: Decimal
    free_margin: Decimal
    high_water_mark: Decimal | None
    #: Equity at the most recent 00:00 UTC.
    day_open_equity: Decimal | None
    positions: tuple[OpenPosition, ...]
    entries_last_hour: int
    consecutive_losses: int
    last_loss_ts_ms: int | None
    #: Symbols that already produced a signal on the current bar (RC-09).
    signals_this_bar: tuple[str, ...]
    #: Venue API error-rate breaker; None = unknown = REJECT (RC-15).
    api_error_breach: bool | None


@dataclass(frozen=True)
class MarketFacts:
    env: Env
    symbol: str
    tick_size: Decimal
    lot_size_base: Decimal
    min_size_base: Decimal
    #: Unmeasured until M6 -> None -> REJECT (RC-12e).
    max_size_base: Decimal | None
    mmr: Decimal
    #: Wilder ATR(14) on closed 1h bars (policy.atr_definition).
    atr_1h: Decimal | None
    last_bar_close_ts_ms: int | None
    timeframe_ms: int
    #: Listed, not in maintenance, not flagged for delisting (RC-04); None = unknown.
    tradable: bool | None
    #: Hash of the instrument-spec snapshot these values came from.
    spec_sha: str


@dataclass(frozen=True)
class QualInputs:
    #: The deterministic daily label; missing or stale = REJECT.
    regime: RegimeLabel | None
    #: LLM sentiment; missing = neutral (§13.4).
    sentiment: SentimentScore | None
