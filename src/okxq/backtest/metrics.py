"""Performance metrics (architecture §17) - pure functions, no I/O, no state.

Two numeric regimes, chosen per metric rather than globally:

* **Gate-bearing quantities are exact.** Max drawdown, profit factor and trade concentration
  are compared against frozen thresholds (G-1, G-2, G-7). A float rounding a 15.000% drawdown
  to 15.0000000001% would flip a verdict, so these are computed in ``Decimal`` from exact P&L.
* **Distributional statistics are floats.** Sharpe, Sortino, skew and kurtosis are estimates
  with sampling error many orders of magnitude larger than float error; pretending otherwise
  would be false precision.

Undefined is ``None``, never a sentinel number. A profit factor with no losing trades is not
"999", and a Sharpe ratio of a flat curve is not 0 - substituting a number would let an
undefined statistic pass a gate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

#: Crypto perps trade continuously, so a year of hourly bars is 24 x 365 periods.
HOURS_PER_YEAR = 24 * 365


@dataclass(frozen=True)
class EquityPoint:
    """Account equity marked at one instant (UTC epoch ms)."""

    ts_ms: int
    equity: Decimal


def max_drawdown(equity: Sequence[Decimal]) -> Decimal:
    """Largest peak-to-trough decline as a fraction of the peak, in ``[0, 1]``.

    Measured on the marked equity curve, not on closed-trade P&L: an open position's adverse
    excursion is real drawdown even if the trade later recovers. An equity value at or below
    zero is a total loss (drawdown 1).
    """
    peak: Decimal | None = None
    worst = Decimal(0)
    for value in equity:
        if peak is None or value > peak:
            peak = value
        if peak is not None and peak > 0:
            dd = Decimal(1) if value <= 0 else (peak - value) / peak
            worst = max(worst, dd)
    return worst


def profit_factor(pnls: Sequence[Decimal]) -> Decimal | None:
    """Gross profit / gross loss over closed trades.

    ``None`` when undefined: no trades, or no losing trades. A strategy with no losers in its
    sample has an unbounded estimate, which is evidence of too few trades (G-3), not of edge.
    """
    gross_win = sum((p for p in pnls if p > 0), Decimal(0))
    gross_loss = -sum((p for p in pnls if p < 0), Decimal(0))
    if gross_loss == 0:
        return None
    return gross_win / gross_loss


def top_n_share(pnls: Sequence[Decimal], n: int = 5) -> Decimal | None:
    """Share of net profit contributed by the ``n`` most profitable trades (G-7).

    ``None`` when net profit is not positive: concentration of a loss is meaningless, and such
    a strategy already fails G-2.
    """
    net = sum(pnls, Decimal(0))
    if net <= 0:
        return None
    top = sorted(pnls, reverse=True)[:n]
    return sum(top, Decimal(0)) / net


def win_rate(pnls: Sequence[Decimal]) -> Decimal | None:
    if not pnls:
        return None
    return Decimal(sum(1 for p in pnls if p > 0)) / Decimal(len(pnls))


def expectancy(pnls: Sequence[Decimal]) -> Decimal | None:
    """Mean net P&L per closed trade, after all costs."""
    if not pnls:
        return None
    return sum(pnls, Decimal(0)) / Decimal(len(pnls))


def payoff_ratio(pnls: Sequence[Decimal]) -> Decimal | None:
    """Average win / average loss magnitude."""
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    if not wins or not losses:
        return None
    return (sum(wins, Decimal(0)) / len(wins)) / (sum(losses, Decimal(0)) / len(losses))


def simple_returns(equity: Sequence[Decimal]) -> list[float]:
    """Per-period simple returns of the equity curve.

    Stops at the first non-positive equity: after a total loss there is no capital for a
    return to be a fraction of.
    """
    out: list[float] = []
    for prev, cur in pairwise(equity):
        if prev <= 0:
            break
        out.append(float((cur - prev) / prev))
    return out


def _mean(xs: Sequence[float]) -> float:
    return math.fsum(xs) / len(xs)


def _sample_std(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def sharpe_ratio(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Annualised Sharpe with zero risk-free rate and sample (n-1) standard deviation.

    ``None`` for fewer than two returns or zero variance.
    """
    if len(returns) < 2:
        return None
    sd = _sample_std(returns)
    if sd == 0:
        return None
    return _mean(returns) / sd * math.sqrt(periods_per_year)


def per_period_sharpe(returns: Sequence[float]) -> float | None:
    """Non-annualised Sharpe - the quantity the Deflated Sharpe Ratio is defined on."""
    if len(returns) < 2:
        return None
    sd = _sample_std(returns)
    if sd == 0:
        return None
    return _mean(returns) / sd


def sortino_ratio(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Annualised Sortino: mean over downside deviation ``sqrt(mean(min(r, 0)^2))``.

    ``None`` when there is no downside at all - an undefined ratio, not an infinite one.
    """
    if len(returns) < 2:
        return None
    downside = math.sqrt(math.fsum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    if downside == 0:
        return None
    return _mean(returns) / downside * math.sqrt(periods_per_year)


def skewness(xs: Sequence[float]) -> float | None:
    """Population skewness ``m3 / m2^1.5``."""
    if len(xs) < 3:
        return None
    m = _mean(xs)
    m2 = math.fsum((x - m) ** 2 for x in xs) / len(xs)
    if m2 == 0:
        return None
    m3 = math.fsum((x - m) ** 3 for x in xs) / len(xs)
    return m3 / math.pow(m2, 1.5)


def kurtosis(xs: Sequence[float]) -> float | None:
    """Population kurtosis ``m4 / m2^2`` - NOT excess kurtosis (a normal gives 3)."""
    if len(xs) < 4:
        return None
    m = _mean(xs)
    m2 = math.fsum((x - m) ** 2 for x in xs) / len(xs)
    if m2 == 0:
        return None
    m4 = math.fsum((x - m) ** 4 for x in xs) / len(xs)
    return m4 / m2**2


def cagr(first: Decimal, last: Decimal, years: float) -> float | None:
    """Compound annual growth rate. ``None`` for a non-positive start, span or end."""
    if first <= 0 or years <= 0:
        return None
    if last <= 0:
        return -1.0
    growth: float = float(last / first) ** (1.0 / years)
    return growth - 1.0


def calmar_ratio(cagr_value: float | None, mdd: Decimal) -> float | None:
    if cagr_value is None or mdd == 0:
        return None
    return cagr_value / float(mdd)


__all__ = [
    "HOURS_PER_YEAR",
    "EquityPoint",
    "cagr",
    "calmar_ratio",
    "expectancy",
    "kurtosis",
    "max_drawdown",
    "payoff_ratio",
    "per_period_sharpe",
    "profit_factor",
    "sharpe_ratio",
    "simple_returns",
    "skewness",
    "sortino_ratio",
    "top_n_share",
    "win_rate",
]
