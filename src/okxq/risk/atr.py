"""Wilder ATR(14) on closed 1h bars, FIXED-BUFFER definition (policy.atr_definition; SZ-2).

The value depends on exactly the last ``atr_buffer_bars`` closed bars: the recursion is
seeded with the mean of the first ``atr_period`` true ranges inside that buffer, then
smoothed ``atr = (atr * (n - 1) + tr) / n`` over the rest. So it is identical whatever the
series or walk-forward fold starts at - exact by construction, not merely close (advisor
ruling, cycle 2). Research (backtest risk gate) and M6 live call this same function.

Default deny: fewer bars than the buffer, or any non-positive or non-finite price, returns
None, and SZ-2 then REJECTS.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

from okxq.risk.num import positive, risk_context
from okxq.risk.policy import RiskPolicy


def wilder_atr(
    highs: Sequence[Decimal], lows: Sequence[Decimal], closes: Sequence[Decimal], p: RiskPolicy
) -> Decimal | None:
    """ATR at the LAST bar of the given closed bars, or None (see module doc)."""
    n, buf = p.atr_period, p.atr_buffer_bars
    if not len(highs) == len(lows) == len(closes) or len(closes) < buf:
        return None
    h: list[Decimal] = []
    lo: list[Decimal] = []
    c: list[Decimal] = []
    for out, seq in ((h, highs), (lo, lows), (c, closes)):
        for x in seq[-buf:]:
            v = positive(x)
            if v is None:
                return None
            out.append(v)
    with localcontext(risk_context()):
        tr = [max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1])) for k in range(1, buf)]
        atr = sum(tr[:n], Decimal(0)) / n
        for x in tr[n:]:
            atr = (atr * (n - 1) + x) / n
        return atr
