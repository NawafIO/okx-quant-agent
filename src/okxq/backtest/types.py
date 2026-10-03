"""Value types shared by the backtest engine, gates and research harness.

Venue parameters (fee rates, tick and lot sizes, maintenance margin) are **injected, never
asserted**: ``docs/VENUE_FACTS.md`` measures none of them, the fee tier sits behind a private
endpoint, and the rest need network access. Every such object carries a ``provenance`` so a
backtest on unmeasured numbers is visible as such - and refused outside synthetic tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from okxq.errors import OkxqError


class BacktestConfigError(OkxqError):
    """A backtest was asked to run on inputs it cannot honestly evaluate."""


class Provenance(StrEnum):
    """Where a venue parameter came from."""

    MEASURED = "MEASURED"  # read from the venue, dated, recorded in VENUE_FACTS
    SYNTHETIC = "SYNTHETIC"  # test fixture; refused unless the engine runs in synthetic mode
    UNMEASURED = "UNMEASURED"  # a placeholder; always refused


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True)
class InstrumentSpec:
    """Static contract facts for one perpetual swap. Sizes are in BASE units (finding A-10):
    contract counts would need ``ctVal``, which is unmeasured, so nothing here depends on it."""

    inst_id: str
    tick_size: Decimal
    lot_size_base: Decimal
    min_size_base: Decimal
    #: Maintenance margin ratio applied to position notional (isolated margin).
    mmr: Decimal
    provenance: Provenance


@dataclass(frozen=True)
class FeeSchedule:
    """Maker/taker rates as fractions of notional, charged in USDT (the settlement currency
    of USDT-margined swaps)."""

    maker: Decimal
    taker: Decimal
    provenance: Provenance


@dataclass(frozen=True)
class SlippageModel:
    """Volatility- and size-scaled slippage with a one-tick floor (architecture §11.2).

    ``slip = max(1 tick, price * (k_vol * sigma + k_impact * sqrt(participation)))`` where
    ``sigma`` is the sample std of the last ``vol_lookback`` close-to-close log returns and
    ``participation`` is order size over the PREVIOUS bar's base volume - both known before
    the fill, so the engine itself holds no look-ahead. The square-root impact form is an
    assumption, recorded under ``assumption_id`` for reconciliation against PAPER fills (M7).
    """

    k_vol: Decimal
    k_impact: Decimal
    vol_lookback: int
    assumption_id: str


@dataclass(frozen=True)
class CostStress:
    """Cost multipliers. 1x is the modelled baseline; G-9 runs 2x.

    Funding is stressed ADVERSELY on both legs (advisor finding A-4): payments are multiplied
    by ``funding_paid``, receipts by ``funding_received`` (< 1 under stress), so a strategy
    whose income is funding cannot be flattered by the stress test.
    """

    fees: Decimal = Decimal(1)
    slippage: Decimal = Decimal(1)
    funding_paid: Decimal = Decimal(1)
    funding_received: Decimal = Decimal(1)


#: The G-9 stress: 2x fees, 2x slippage, 2x funding paid, 0.5x funding received.
G9_STRESS = CostStress(
    fees=Decimal(2),
    slippage=Decimal(2),
    funding_paid=Decimal(2),
    funding_received=Decimal("0.5"),
)


@dataclass(frozen=True)
class FundingRate:
    """One funding settlement. ``modelled`` marks a reconstruction rather than a realised
    venue rate, so results can report how much of their funding was modelled."""

    ts_ms: int
    rate: Decimal
    modelled: bool = False


@dataclass(frozen=True)
class BarSeries:
    """One instrument's closed bars, chronological, gaps allowed (never repaired).

    Parallel tuples rather than a DataFrame: the engine releases bars one at a time into a
    strategy's view, and tuples cannot be mutated behind its back.
    """

    inst_id: str
    timeframe_ms: int
    ts_open_ms: tuple[int, ...]
    open: tuple[Decimal, ...]
    high: tuple[Decimal, ...]
    low: tuple[Decimal, ...]
    close: tuple[Decimal, ...]
    volume_base: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        n = len(self.ts_open_ms)
        lengths = {len(self.open), len(self.high), len(self.low), len(self.close)}
        if lengths != {n} or len(self.volume_base) != n:
            raise BacktestConfigError(f"{self.inst_id}: ragged bar columns")
        for a, b in zip(self.ts_open_ms, self.ts_open_ms[1:], strict=False):
            if b <= a:
                raise BacktestConfigError(f"{self.inst_id}: bars not strictly increasing")
            if (b - a) % self.timeframe_ms:
                raise BacktestConfigError(f"{self.inst_id}: bar off the {self.timeframe_ms}ms grid")

    def __len__(self) -> int:
        return len(self.ts_open_ms)


class Action(StrEnum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"


@dataclass(frozen=True)
class OrderIntent:
    """What a strategy may ask for. It names direction and protective levels; it does NOT
    name a quantity - sizing is the Sizer's job, never the strategy's.

    Entries without a stop are rejected by the engine (no signal without a stop-loss).
    """

    inst_id: str
    action: Action
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None
    tag: str = ""


@dataclass(frozen=True)
class FillRecord:
    ts_ms: int
    inst_id: str
    side: Side
    #: +qty adds to the position, -qty reduces it (base units).
    qty_delta: Decimal
    price: Decimal
    fee: Decimal
    slippage_cost: Decimal
    liquidity: str  # "taker" | "maker" | "liquidation"
    reason: str


@dataclass(frozen=True)
class FundingEvent:
    ts_ms: int
    inst_id: str
    rate: Decimal
    modelled: bool
    #: Signed cash flow to the account after stress (negative = paid).
    cash_flow: Decimal


@dataclass(frozen=True)
class ClosedTrade:
    """One position lifecycle, open to flat, aggregating every fill (advisor finding A-2).

    Partial fills therefore never fragment a trade: G-3 counts lifecycles and G-7 sees a big
    win as one trade however many fills it took.
    """

    inst_id: str
    side: Side
    entry_ts_ms: int
    exit_ts_ms: int
    max_qty: Decimal
    avg_entry: Decimal
    avg_exit: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    slippage_cost: Decimal
    exit_reason: str

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees + self.funding


@dataclass(frozen=True)
class Rejection:
    ts_ms: int
    inst_id: str
    reason: str


@dataclass
class _TradeAccumulator:
    """Mutable per-lifecycle totals; frozen into a ClosedTrade when the position goes flat."""

    side: Side
    entry_ts_ms: int
    max_qty: Decimal = Decimal(0)
    entry_qty: Decimal = Decimal(0)
    entry_value: Decimal = Decimal(0)
    exit_qty: Decimal = Decimal(0)
    exit_value: Decimal = Decimal(0)
    gross_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    slippage_cost: Decimal = Decimal(0)
    reasons: list[str] = field(default_factory=list)
