"""Decimal validation and the arithmetic context of the risk engine."""

from __future__ import annotations

from decimal import (
    ROUND_FLOOR,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)

#: Precision 50 (the V-12 lesson).
PRECISION = 50


def risk_context() -> Context:
    """A FRESH context - never the caller's traps or rounding (closing audit #9): invalid
    operations, division by zero and overflow raise; rounding is toward -infinity, so a
    budget or quantity is never rounded up."""
    return Context(
        prec=PRECISION, rounding=ROUND_FLOOR, traps=[InvalidOperation, DivisionByZero, Overflow]
    )


def finite(x: object) -> Decimal | None:
    """``x`` if it is a finite Decimal (not NaN, sNaN, +-inf, not a float/int/None)."""
    if isinstance(x, Decimal) and x.is_finite():
        return x
    return None


def positive(x: object) -> Decimal | None:
    d = finite(x)
    if d is not None and d > 0:
        return d
    return None


def count(x: object) -> int | None:
    """A non-negative int that is not a bool."""
    if isinstance(x, int) and not isinstance(x, bool) and x >= 0:
        return x
    return None
