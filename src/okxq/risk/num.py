"""Decimal validation and the arithmetic context of the risk engine."""

from __future__ import annotations

from decimal import Decimal

#: Precision 50 (the V-12 lesson). The default context traps InvalidOperation,
#: DivisionByZero and Overflow; the engine runs every calculation under
#: ``localcontext(prec=PRECISION)``, so any of them raises and fails the check.
PRECISION = 50


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
