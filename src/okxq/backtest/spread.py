"""Historical bid-ask spread estimate from closed OHLC bars (slip-v2 spread term).

Chief Advisor ruling (2026-10-03): a 1-tick floor measured from five Saturday order-book
snapshots in 2026 is no floor for 2020-2022 alt spreads, so the slippage model adds a
time-varying half-spread estimated from past bars only.

Estimator: Abdi & Ranaldo (2017), "A Simple Estimation of Bid-Ask Spreads from Daily Close,
High, and Low Prices", Review of Financial Studies 30(12). With ``c`` the log close and
``eta`` the log mid-range ``(log h + log l) / 2``::

    s_t^2 = 4 (c_t - eta_t)(c_t - eta_{t+1})          S = sqrt(max(0, mean s_t^2))

``S`` is the relative FULL spread. The pair (t, t+1) needs bar t+1, so a window ending at the
last closed bar uses pairs up to (n-2, n-1) - nothing unclosed. Negative means are set to zero,
as in the paper's averaged variant. On high-frequency crypto bars the estimator is noisy and
biased upward by volatility; that bias is in the adverse direction, which is acceptable for
an unreconciled cost model (ruling) and is reconciled against PAPER spreads at M7.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def abdi_ranaldo_spread(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> float:
    """Relative full spread over the given CLOSED bars (>= 0). 0.0 with fewer than 2 bars."""
    n = len(closes)
    if n < 2:
        return 0.0
    eta = [(math.log(h) + math.log(lo)) / 2 for h, lo in zip(highs, lows, strict=True)]
    c = [math.log(x) for x in closes]
    terms = [4 * (c[t] - eta[t]) * (c[t] - eta[t + 1]) for t in range(n - 1)]
    mean = math.fsum(terms) / len(terms)
    return math.sqrt(mean) if mean > 0 else 0.0


def abdi_ranaldo_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], lookback: int
) -> list[float]:
    """Value at index n uses only bars ``[max(0, n - lookback + 1), n]`` - the T-1 form the
    property test checks: ``series(x[:n])[-1] == series(x)[n-1]``."""
    return [
        abdi_ranaldo_spread(
            highs[max(0, n - lookback + 1) : n + 1],
            lows[max(0, n - lookback + 1) : n + 1],
            closes[max(0, n - lookback + 1) : n + 1],
        )
        for n in range(len(closes))
    ]
