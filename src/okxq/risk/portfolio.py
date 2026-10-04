"""Portfolio Manager state as a pure reducer (architecture §14; M5_DESIGN §7).

``apply(state, event) -> state``. The state feeds the risk snapshot: open risk and cluster
exposure (via the positions), the high-water mark (RC-11), equity at 00:00 UTC (RC-10),
consecutive losses (RC-13) and entries in the trailing hour (RC-14).

Equity is CONSERVATIVE: realised equity plus unrealised LOSSES only - unrealised gains are
excluded (checkpoint 1). The high-water mark and the UTC-day opening equity are tracked on
that same basis, so a drawdown is never measured against a gain that was never banked.

An event that does not fit the state (a close of a symbol not held, time going backwards)
is a bug upstream and raises :class:`PortfolioError`; it is never silently absorbed.
Order lifecycle (scale-outs, trailing stops, breakeven, time exits) is M6/M7; ``StopMoved``
exists now so open risk stays correct when trailing stops arrive.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from okxq.contracts import Env, Side
from okxq.errors import SafetyError

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
ZERO = Decimal(0)


class PortfolioError(SafetyError):
    """An event inconsistent with the portfolio state."""


@dataclass(frozen=True)
class Init:
    ts_ms: int
    equity: Decimal


@dataclass(frozen=True)
class Opened:
    ts_ms: int
    symbol: str
    side: Side
    qty: Decimal
    entry: Decimal
    stop: Decimal
    leverage: Decimal
    fee: Decimal


@dataclass(frozen=True)
class Closed:
    ts_ms: int
    symbol: str
    price: Decimal
    fee: Decimal


@dataclass(frozen=True)
class MarkUpdate:
    ts_ms: int
    symbol: str
    mark: Decimal


@dataclass(frozen=True)
class StopMoved:
    ts_ms: int
    symbol: str
    stop: Decimal


@dataclass(frozen=True)
class FundingAccrued:
    ts_ms: int
    symbol: str
    #: Signed cash flow to the account (negative = paid).
    amount: Decimal


Event = Init | Opened | Closed | MarkUpdate | StopMoved | FundingAccrued


@dataclass(frozen=True)
class Held:
    symbol: str
    side: Side
    qty: Decimal
    entry: Decimal
    stop: Decimal
    mark: Decimal
    mark_ts_ms: int
    leverage: Decimal
    #: Fee paid at the open. Already in ``realised``; kept here so the win/loss test for
    #: RC-13 judges the whole round trip, not the exit alone (cycle-2 advisor finding).
    entry_fee: Decimal

    @property
    def unrealised(self) -> Decimal:
        move = self.mark - self.entry if self.side == "LONG" else self.entry - self.mark
        return self.qty * move


@dataclass(frozen=True)
class PortfolioState:
    env: Env
    realised: Decimal
    positions: tuple[Held, ...]
    high_water_mark: Decimal
    day: int
    day_open_equity: Decimal
    consecutive_losses: int
    last_loss_ts_ms: int | None
    entry_times_ms: tuple[int, ...]
    last_ts_ms: int

    @property
    def equity(self) -> Decimal:
        """Conservative: realised plus unrealised losses only."""
        return self.realised + sum((min(ZERO, h.unrealised) for h in self.positions), ZERO)

    @property
    def margin_used(self) -> Decimal:
        return sum((h.qty * h.entry / h.leverage for h in self.positions), ZERO)

    def held(self, symbol: str) -> Held | None:
        return next((h for h in self.positions if h.symbol == symbol), None)


def _positive(name: str, v: Decimal) -> Decimal:
    if not (isinstance(v, Decimal) and v.is_finite() and v > 0):
        raise PortfolioError(f"{name} must be a positive finite Decimal, got {v!r}")
    return v


def _finite(name: str, v: Decimal) -> Decimal:
    if not (isinstance(v, Decimal) and v.is_finite()):
        raise PortfolioError(f"{name} must be a finite Decimal, got {v!r}")
    return v


def _update(state: PortfolioState, symbol: str, new: Callable[[Held], Held]) -> PortfolioState:
    if state.held(symbol) is None:
        raise PortfolioError(f"{symbol} is not held")
    return replace(
        state,
        positions=tuple(new(x) if x.symbol == symbol else x for x in state.positions),
    )


def initial(env: Env, event: Init) -> PortfolioState:
    eq = _positive("equity", event.equity)
    return PortfolioState(env, eq, (), eq, event.ts_ms // DAY_MS, eq, 0, None, (), event.ts_ms)


def apply(state: PortfolioState, event: Event) -> PortfolioState:
    if isinstance(event, Init):
        raise PortfolioError("Init only starts a portfolio")
    if event.ts_ms < state.last_ts_ms:
        raise PortfolioError(f"event at {event.ts_ms} precedes state at {state.last_ts_ms}")
    s = state
    day = event.ts_ms // DAY_MS
    if day > s.day:  # first event of a new UTC day: its opening equity is the equity now
        s = replace(s, day=day, day_open_equity=s.equity)
    s = replace(s, last_ts_ms=event.ts_ms)
    if isinstance(event, Opened):
        if s.held(event.symbol) is not None:
            raise PortfolioError(f"{event.symbol} already held")
        if event.side not in ("LONG", "SHORT"):
            raise PortfolioError(f"side {event.side!r}")
        h = Held(
            event.symbol,
            event.side,
            _positive("qty", event.qty),
            _positive("entry", event.entry),
            _positive("stop", event.stop),
            event.entry,
            event.ts_ms,
            _positive("leverage", event.leverage),
            _finite("fee", event.fee),
        )
        recent = tuple(t for t in s.entry_times_ms if t > event.ts_ms - HOUR_MS)
        s = replace(
            s,
            realised=s.realised - h.entry_fee,
            positions=tuple(sorted((*s.positions, h), key=lambda x: x.symbol)),
            entry_times_ms=(*recent, event.ts_ms),
        )
    elif isinstance(event, Closed):
        closing = s.held(event.symbol)
        if closing is None:
            raise PortfolioError(f"{event.symbol} is not held")
        price = _positive("price", event.price)
        pnl = replace(closing, mark=price).unrealised - _finite("fee", event.fee)
        # A loss is judged on the ROUND TRIP: the entry fee was booked at the open, so a
        # trade that only beats its exit fee is still a loss and must extend the streak.
        loss = pnl - closing.entry_fee < 0
        s = replace(
            s,
            realised=s.realised + pnl,
            positions=tuple(x for x in s.positions if x.symbol != event.symbol),
            consecutive_losses=s.consecutive_losses + 1 if loss else 0,
            last_loss_ts_ms=event.ts_ms if loss else s.last_loss_ts_ms,
        )
    elif isinstance(event, MarkUpdate):
        if s.held(event.symbol) is not None:
            mark, ts = _positive("mark", event.mark), event.ts_ms
            s = _update(s, event.symbol, lambda x: replace(x, mark=mark, mark_ts_ms=ts))
    elif isinstance(event, StopMoved):
        stop = _positive("stop", event.stop)
        s = _update(s, event.symbol, lambda x: replace(x, stop=stop))
    else:
        if s.held(event.symbol) is None:
            raise PortfolioError(f"{event.symbol} is not held")
        s = replace(s, realised=s.realised + _finite("amount", event.amount))
    return replace(s, high_water_mark=max(s.high_water_mark, s.equity))
