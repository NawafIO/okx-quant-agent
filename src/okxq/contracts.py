"""Data contracts - the typed records that cross every module boundary (architecture §4).

Two rules are enforced here mechanically rather than by convention:

**D-1 - no float for money.** ``float`` is IEEE-754 binary and cannot represent ``0.1``
exactly. Every price, quantity, notional and equity field uses :data:`Money`, which
*rejects* ``float`` input outright rather than quietly converting it. Construct these from
``str`` (preferred, exact) or ``int`` or ``Decimal``.

**D-2 - every record carries its environment.** ``env`` is required, with no default. A
module receiving a record whose ``env`` differs from its own raises
:class:`~okxq.errors.EnvironmentMismatchError` and halts - see :func:`require_env`. It does not
coerce, warn, or fall back, because the failure mode being prevented is a simulated run
placing a real order.

All models are frozen: a record is a fact, and facts are not edited in place.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from okxq.errors import EnvironmentMismatchError
from okxq.risk.policy import FROZEN_RISK_POLICY

Env = Literal["DEMO", "PAPER", "LIVE"]

ENVS: tuple[Env, ...] = ("DEMO", "PAPER", "LIVE")

Side = Literal["LONG", "SHORT"]
Regime = Literal["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "CRISIS"]
OrderType = Literal["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
OrderState = Literal["NEW", "SENT", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"]
TimeInForce = Literal["GTC", "IOC", "FOK", "POST_ONLY"]
Verdict = Literal["APPROVED", "REJECTED"]
RegimeSource = Literal["RULE", "LLM_CONFIRMED", "RULE_LLM_DISAGREE"]


# --- D-1 enforcement --------------------------------------------------------------------


def _reject_float(value: object) -> object:
    """Reject ``float`` input for monetary fields (contract rule D-1).

    Raising here is the point. Accepting a float and converting it would inherit the binary
    representation error - ``Decimal(0.1)`` is ``0.1000000000000000055511151231257827``,
    and that error compounds through a sizing calculation.
    """
    if isinstance(value, float):
        raise ValueError(
            "D-1 violation: monetary/quantity fields reject float. "
            "Pass a str (e.g. '0.1'), int, or Decimal - never a float."
        )
    return value


#: A monetary or quantity value. Exact by construction; rejects ``float``.
Money = Annotated[Decimal, BeforeValidator(_reject_float)]

#: A value constrained to [0, 1] - confidences and fractions.
UnitInterval = Annotated[Decimal, BeforeValidator(_reject_float), Field(ge=0, le=1)]

#: A value constrained to [-1, +1] - bounded scores. Clamping LLM output into this range
#: is how qualitative input is prevented from carrying an unbounded number (architecture §5.2).
SignedUnit = Annotated[Decimal, BeforeValidator(_reject_float), Field(ge=-1, le=1)]


class _Record(BaseModel):
    """Base for all contract records: frozen, strict, no unexpected fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)


# --- D-2 enforcement --------------------------------------------------------------------


class _HasEnv(Protocol):
    """Structural type for any record carrying an environment tag."""

    @property
    def env(self) -> Env: ...


def require_env(record: _HasEnv, expected: Env) -> None:
    """Assert a record belongs to ``expected``, else raise (contract rule D-2).

    Call this at every module boundary that accepts a record from elsewhere. Deliberately
    returns ``None`` and raises on mismatch, so it cannot be mistaken for a predicate that
    a caller might ignore the result of.

    Raises:
        EnvironmentMismatchError: if the record's environment differs. Fatal by design.
    """
    actual = record.env
    if actual != expected:
        raise EnvironmentMismatchError(
            f"D-2 violation: {type(record).__name__} carries env={actual!r} but this "
            f"process is bound to env={expected!r}. Halting rather than coercing."
        )


# --- market data ------------------------------------------------------------------------


class Candle(_Record):
    """An OHLCV bar. An immutable market fact."""

    env: Env
    symbol: str
    timeframe: str
    ts_open: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: Money
    is_closed: bool
    source: str
    ingested_at: datetime

    @field_validator("ts_open", "ingested_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes. A bar timestamp without a timezone is ambiguous."""
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC)")
        return v

    @field_validator("volume")
    @classmethod
    def _non_negative_volume(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("volume cannot be negative")
        return v

    @field_validator("close")
    @classmethod
    def _ohlc_sane(cls, v: Decimal, info: object) -> Decimal:
        """Validate OHLC ordering and positivity once all four prices are present."""
        data = getattr(info, "data", {})
        o, h, lo = data.get("open"), data.get("high"), data.get("low")
        if o is None or h is None or lo is None:
            return v  # an earlier field already failed; do not mask its error
        if any(p <= 0 for p in (o, h, lo, v)):
            raise ValueError("OHLC prices must be positive")
        if lo > min(o, v) or h < max(o, v) or lo > h:
            raise ValueError(f"OHLC out of order: o={o} h={h} l={lo} c={v}")
        return v


# --- qualitative inputs (LLM-sourced; see architecture §5) ------------------------------


class RegimeLabel(_Record):
    """A market regime classification.

    The deterministic rule classifier is authoritative. An LLM may confirm or disagree, and
    disagreement can only *reduce* risk appetite (architecture §8.3, §13.4) - it never
    changes the label.
    """

    env: Env
    symbol: str
    ts: datetime
    regime: Regime
    confidence: UnitInterval
    determined_by: RegimeSource
    rule_inputs: dict[str, str] = Field(default_factory=dict)


class SentimentScore(_Record):
    """A bounded sentiment score derived from an LLM.

    Consumed **only** in boolean comparisons as a veto gate (architecture §13.4). It never
    multiplies a position size. ``score`` is schema-clamped to [-1, +1] so an LLM cannot
    emit an unbounded number into the system.
    """

    env: Env
    symbol: str
    ts: datetime
    score: SignedUnit
    confidence: UnitInterval
    model_id: str
    source_urls: tuple[str, ...] = ()


# --- strategy output --------------------------------------------------------------------


class Signal(_Record):
    """A strategy's output. Pure function of market context.

    Every signal carries a stop-loss. The system has no concept of an unprotected entry, so
    ``stop_loss`` is required rather than optional - a signal without one cannot be built.
    """

    env: Env
    strategy_id: str
    strategy_version: str
    symbol: str
    ts: datetime
    side: Side
    entry_ref: Money
    stop_loss: Money
    take_profit: tuple[Money, ...] = Field(min_length=1)
    conviction: UnitInterval
    features: dict[str, str] = Field(default_factory=dict)

    @field_validator("stop_loss")
    @classmethod
    def _stop_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("stop_loss must be positive")
        return v

    @field_validator("take_profit")
    @classmethod
    def _stop_on_correct_side(cls, v: tuple[Decimal, ...], info: object) -> tuple[Decimal, ...]:
        """Validate stop/target geometry against the side.

        A stop on the wrong side of entry is not a bad trade, it is a bug: it would invert
        the sizing formula's denominator and manufacture a position of the wrong magnitude.
        """
        data = getattr(info, "data", {})
        side, entry, stop = data.get("side"), data.get("entry_ref"), data.get("stop_loss")
        if side is None or entry is None or stop is None:
            return v
        if side == "LONG" and stop >= entry:
            raise ValueError(f"LONG requires stop_loss < entry_ref (got {stop} >= {entry})")
        if side == "SHORT" and stop <= entry:
            raise ValueError(f"SHORT requires stop_loss > entry_ref (got {stop} <= {entry})")
        for tp in v:
            if side == "LONG" and tp <= entry:
                raise ValueError(f"LONG take_profit must exceed entry_ref (got {tp})")
            if side == "SHORT" and tp >= entry:
                raise ValueError(f"SHORT take_profit must be below entry_ref (got {tp})")
        return v


# --- risk engine output -----------------------------------------------------------------


class RiskCheckResult(_Record):
    """The outcome of a single pre-trade check, with the values that produced it.

    Observed and limit values are recorded, not just the boolean, so the audit trail can
    answer *why* a trade was approved or rejected without re-running anything.
    """

    check_id: str
    passed: bool
    observed: Money | None = None
    limit: Money | None = None
    detail: str = ""


class TradeProposal(_Record):
    """The only executable artefact in the system (architecture §13, §15.1).

    The Execution Engine's sole entry point accepts one of these and nothing else. There is
    no code path from :class:`Signal` to an order that bypasses the Risk Engine.
    """

    proposal_id: UUID
    env: Env
    signal: Signal
    qty_base: Money
    notional_quote: Money
    risk_amount: Money
    risk_pct_of_equity: UnitInterval
    leverage: Money
    liquidation_estimate: Money | None = None
    risk_checks: tuple[RiskCheckResult, ...] = ()
    verdict: Verdict
    expires_at: datetime
    hmac: str = ""

    @field_validator("qty_base", "notional_quote", "risk_amount", "leverage")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("quantities, notionals and leverage cannot be negative")
        return v

    @model_validator(mode="after")
    def _verdict_matches_checks(self) -> TradeProposal:
        """The verdict cannot disagree with the checks (M5 checkpoint 1, B-3).

        APPROVED requires exactly the pinned check set, every one passed, a positive size,
        and risk and leverage within the pinned policy (imported, never restated). REJECTED
        requires zero size and at least one failed check, so a rejection is explained.
        """
        p = FROZEN_RISK_POLICY
        ids = [c.check_id for c in self.risk_checks]
        if self.verdict == "APPROVED":
            if len(ids) != len(set(ids)) or set(ids) != set(p.required_checks):
                raise ValueError("APPROVED requires exactly the pinned risk-check set")
            if not all(c.passed for c in self.risk_checks):
                raise ValueError("APPROVED with a failed risk check")
            if self.qty_base <= 0:
                raise ValueError("APPROVED requires a positive quantity")
            if self.risk_pct_of_equity > p.max_risk_per_trade:
                raise ValueError("APPROVED risk exceeds the pinned per-trade limit")
            if not Decimal(1) <= self.leverage <= p.max_leverage:
                raise ValueError("APPROVED leverage outside [1, pinned cap]")
        else:
            if self.qty_base != 0 or self.notional_quote != 0 or self.risk_amount != 0:
                raise ValueError("REJECTED requires zero quantity, notional and risk")
            if all(c.passed for c in self.risk_checks):
                raise ValueError("REJECTED requires at least one failed check")
        return self

    def is_executable_at(self, now: datetime) -> bool:
        """Whether this proposal may be sent to the venue at ``now``.

        Necessary, never sufficient: the Execution Engine additionally verifies the HMAC,
        the environment, single-use consumption of ``proposal_id``, and - when HITL is
        enabled - a recorded human approval, then re-runs the full risk battery against
        current prices (architecture §19.2 control 5).
        """
        return self.verdict == "APPROVED" and now < self.expires_at


# --- execution --------------------------------------------------------------------------


class Order(_Record):
    """An order. ``client_order_id`` is our own deterministic idempotency key.

    Derived from ``(proposal_id, leg, attempt_class)`` so that a retry after a network
    timeout re-sends the *same* id and the venue deduplicates, rather than us creating a
    second position (architecture §15.2, finding R-6).
    """

    env: Env
    client_order_id: str
    proposal_id: UUID
    symbol: str
    side: Side
    type: OrderType
    qty_base: Money
    price: Money | None = None
    reduce_only: bool = False
    time_in_force: TimeInForce = "GTC"
    state: OrderState = "NEW"
    exchange_order_id: str | None = None


class Fill(_Record):
    """An execution. ``slippage_bps`` feeds the §11.4 cost-reconciliation gate."""

    env: Env
    client_order_id: str
    exchange_trade_id: str
    ts: datetime
    price: Money
    qty_base: Money
    fee: Money
    fee_currency: str
    slippage_bps: Money


class Position(_Record):
    """An open position.

    ``protective_orders_confirmed`` is false until a ``reduce_only`` stop has been read back
    from the venue. A position without a confirmed stop is an incident, not a state
    (architecture §15.3) - the execution engine closes it immediately.
    """

    env: Env
    symbol: str
    side: Side
    qty_base: Money
    avg_entry: Money
    stop_loss: Money
    take_profit: tuple[Money, ...] = ()
    unrealised_pnl: Money = Decimal(0)
    realised_pnl: Money = Decimal(0)
    opened_at: datetime
    strategy_id: str
    protective_orders_confirmed: bool = False


__all__ = [
    "ENVS",
    "Candle",
    "Env",
    "Fill",
    "Money",
    "Order",
    "OrderState",
    "OrderType",
    "Position",
    "Regime",
    "RegimeLabel",
    "RegimeSource",
    "RiskCheckResult",
    "SentimentScore",
    "Side",
    "Signal",
    "SignedUnit",
    "TimeInForce",
    "TradeProposal",
    "UnitInterval",
    "Verdict",
    "require_env",
]
