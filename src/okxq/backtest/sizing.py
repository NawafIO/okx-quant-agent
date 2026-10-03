"""Position sizing for research backtests - PROVISIONAL, NOT the M5 Risk Engine.

M4 research needs a size for every entry, but the real sizer (architecture §13.2, with the
RC-01..RC-12 battery and 100% branch coverage) is milestone M5. Until then the engine takes an
injected :class:`Sizer`, and this module ships one plain fixed-fractional implementation that
is labelled as provisional everywhere it is recorded. Nothing in M6+ may import it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from okxq.backtest.types import InstrumentSpec, Side
from okxq.errors import SafetyError


@dataclass(frozen=True)
class SizeDecision:
    qty_base: Decimal
    leverage: Decimal
    reason: str = ""


class Sizer(Protocol):
    @property
    def sizer_id(self) -> str: ...

    def size(
        self,
        *,
        equity: Decimal,
        entry_ref: Decimal,
        stop: Decimal,
        side: Side,
        spec: InstrumentSpec,
    ) -> SizeDecision: ...


def round_down_to_lot(qty: Decimal, lot: Decimal) -> Decimal:
    """Quantity is never rounded up (a rounded-up size risks more than budgeted)."""
    return (qty / lot).to_integral_value(rounding=ROUND_DOWN) * lot


@dataclass(frozen=True)
class ProvisionalFixedFractionalSizer:
    """Risk ``risk_fraction`` of equity between entry reference and stop; isolated margin at
    ``leverage``; notional additionally capped at ``max_notional_fraction`` x equity x leverage.
    Returns qty 0 with a reason when the position would be below the venue minimum."""

    risk_fraction: Decimal
    leverage: Decimal
    max_notional_fraction: Decimal = Decimal(1)
    sizer_id: str = "provisional-fixed-fractional-v1 (NOT M5)"

    def size(
        self,
        *,
        equity: Decimal,
        entry_ref: Decimal,
        stop: Decimal,
        side: Side,
        spec: InstrumentSpec,
    ) -> SizeDecision:
        distance = (entry_ref - stop) * side.sign
        if equity <= 0 or distance <= 0 or entry_ref <= 0:
            return SizeDecision(Decimal(0), self.leverage, "invalid_stop_or_equity")
        qty = equity * self.risk_fraction / distance
        cap = equity * self.max_notional_fraction * self.leverage / entry_ref
        qty = round_down_to_lot(min(qty, cap), spec.lot_size_base)
        if qty < spec.min_size_base:
            return SizeDecision(Decimal(0), self.leverage, "below_min_size")
        return SizeDecision(qty, self.leverage)


@dataclass(frozen=True)
class ProvisionalFixedNotionalSizer:
    """Fixed notional per entry, rounded down to the lot. For cost-model checks, where a
    per-trade result in basis points must be interpretable and ruin must be impossible."""

    notional: Decimal
    leverage: Decimal = Decimal(1)
    sizer_id: str = "provisional-fixed-notional-v1 (NOT M5)"

    def size(
        self,
        *,
        equity: Decimal,
        entry_ref: Decimal,
        stop: Decimal,
        side: Side,
        spec: InstrumentSpec,
    ) -> SizeDecision:
        if entry_ref <= 0 or (entry_ref - stop) * side.sign <= 0:
            return SizeDecision(Decimal(0), self.leverage, "invalid_stop_or_equity")
        qty = round_down_to_lot(self.notional / entry_ref, spec.lot_size_base)
        if qty < spec.min_size_base:
            return SizeDecision(Decimal(0), self.leverage, "below_min_size")
        return SizeDecision(qty, self.leverage)


# --- the ONE research sizing (Chief Advisor, M4 checkpoint 1) ------------------------------
#
# Sizing is a gate lever: halving ``risk_fraction`` roughly halves G-1's max drawdown and
# leaves PF untouched, so a free sizer would let a favourite pass G-1 by being sized down.
# Every research run therefore uses this single pinned configuration: RC-05's 0.5% per-trade
# risk and RC-12's 3x leverage cap, identical for every candidate, never in a grid.
# ResearchProtocol builds the sizer from it and does not accept one.


class SizingTamperError(SafetyError):
    """Research sizing differs from the pin."""


#: SHA-256 of ResearchSizing().canonical_json().
PINNED_RESEARCH_SIZING_SHA256 = "e32046ed9bbc3b33e7a40d1ada9b0d6751d2b7b7ba132594f4f86e8f68908353"


@dataclass(frozen=True)
class ResearchSizing:
    risk_fraction: Decimal = Decimal("0.005")  # RC-05 max_risk_per_trade_pct
    leverage: Decimal = Decimal(3)  # RC-12 leverage cap
    max_notional_fraction: Decimal = Decimal(1)
    kind: str = "provisional-fixed-fractional-v1 (NOT M5)"

    def __post_init__(self) -> None:
        if self.sha256() != PINNED_RESEARCH_SIZING_SHA256:
            raise SizingTamperError(f"research sizing differs from the pin (got {self.sha256()})")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def sizer(self) -> ProvisionalFixedFractionalSizer:
        return ProvisionalFixedFractionalSizer(
            self.risk_fraction, self.leverage, self.max_notional_fraction
        )


RESEARCH_SIZING = ResearchSizing()
