"""Position sizing for research backtests - PROVISIONAL, NOT the M5 Risk Engine.

M4 research needs a size for every entry, but the real sizer (architecture §13.2, with the
RC-01..RC-12 battery and 100% branch coverage) is milestone M5. Until then the engine takes an
injected :class:`Sizer`, and this module ships one plain fixed-fractional implementation that
is labelled as provisional everywhere it is recorded. Nothing in M6+ may import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from okxq.backtest.types import InstrumentSpec, Side


@dataclass(frozen=True)
class SizeDecision:
    qty_base: Decimal
    leverage: Decimal
    reason: str = ""


class Sizer(Protocol):
    sizer_id: str

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
