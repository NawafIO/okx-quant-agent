"""Metrics engine against hand-computed fixtures (M2 acceptance).

Expected values are worked by hand in the comments, or taken from an independent
implementation (the standard library's ``statistics``), never from the code under test.
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal as D  # noqa: N817 - fixture brevity

import pytest

from okxq.backtest import metrics as m


def test_max_drawdown_takes_the_worst_peak_to_trough() -> None:
    # Peaks 100 -> 120. Troughs: 90 (30/120 = 0.25), 80 (40/120 = 1/3). 130 is a new peak.
    curve = [D(100), D(120), D(90), D(110), D(80), D(130)]
    assert m.max_drawdown(curve) == D(40) / D(120)


def test_max_drawdown_of_a_rising_curve_is_zero() -> None:
    assert m.max_drawdown([D(1), D(2), D(3)]) == 0


def test_max_drawdown_counts_a_wipeout_as_total() -> None:
    assert m.max_drawdown([D(100), D(50), D(-10)]) == 1


def test_max_drawdown_is_exact_at_the_g1_boundary() -> None:
    # 100 -> 85 is exactly 15%. A float path could land either side of the threshold.
    assert m.max_drawdown([D(100), D(85)]) == D("0.15")


PNLS = [D(10), D(-5), D(20), D(-15), D(30)]  # wins 60, losses 20, net 40


def test_profit_factor() -> None:
    assert m.profit_factor(PNLS) == 3


def test_profit_factor_is_undefined_without_losers_or_trades() -> None:
    assert m.profit_factor([D(5), D(7)]) is None
    assert m.profit_factor([]) is None


def test_top_n_share() -> None:
    # Top two: 30 + 20 = 50 of a net 40 -> 1.25 (can exceed 1 when losers offset).
    assert m.top_n_share(PNLS, n=2) == D("1.25")
    assert m.top_n_share([D(-1), D(-2)], n=5) is None


def test_trade_statistics() -> None:
    assert m.win_rate(PNLS) == D("0.6")
    assert m.expectancy(PNLS) == 8
    assert m.payoff_ratio(PNLS) == 2  # avg win 20, avg loss 10
    assert m.win_rate([]) is None
    assert m.expectancy([]) is None
    assert m.payoff_ratio([D(1)]) is None


RETURNS = [0.1, -0.05, 0.2]


def test_simple_returns() -> None:
    assert m.simple_returns([D(100), D(110), D(99)]) == [0.1, -0.1]


def test_simple_returns_stop_at_wipeout() -> None:
    assert m.simple_returns([D(100), D(0), D(50)]) == [-1.0]


def test_sharpe_matches_an_independent_implementation() -> None:
    expected = statistics.mean(RETURNS) / statistics.stdev(RETURNS)
    assert m.per_period_sharpe(RETURNS) == pytest.approx(expected, rel=1e-12)
    assert m.sharpe_ratio(RETURNS, periods_per_year=4) == pytest.approx(2 * expected, rel=1e-12)


def test_sharpe_is_undefined_for_flat_or_short_series() -> None:
    assert m.sharpe_ratio([0.01, 0.01, 0.01], 365) is None
    assert m.sharpe_ratio([0.01], 365) is None
    assert m.per_period_sharpe([0.0]) is None


def test_sortino_by_hand() -> None:
    # mean = 1/12; downside deviation = sqrt(0.05^2 / 3) = 0.05/sqrt(3)
    # ratio = (1/12) * sqrt(3) / 0.05 = 5*sqrt(3)/3
    assert m.sortino_ratio(RETURNS, periods_per_year=1) == pytest.approx(5 * math.sqrt(3) / 3)
    assert m.sortino_ratio([0.1, 0.2], 1) is None


def test_moments_by_hand() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    # Symmetric: skew 0. m2 = 2, m4 = (16+1+0+1+16)/5 = 6.8, kurtosis = 6.8/4 = 1.7.
    assert m.skewness(xs) == pytest.approx(0.0, abs=1e-15)
    assert m.kurtosis(xs) == pytest.approx(1.7)
    # Right-skewed: [0,0,3] -> mean 1, m2 = 2, m3 = (-1-1+8)/3 = 2, skew = 2/2^1.5.
    assert m.skewness([0.0, 0.0, 3.0]) == pytest.approx(2 / 2**1.5)
    assert m.skewness([1.0, 1.0, 1.0]) is None
    assert m.kurtosis([1.0, 2.0]) is None


def test_cagr_and_calmar() -> None:
    assert m.cagr(D(100), D(121), 2.0) == pytest.approx(0.1)
    assert m.cagr(D(100), D(0), 1.0) == -1.0
    assert m.cagr(D(0), D(10), 1.0) is None
    assert m.calmar_ratio(0.3, D("0.15")) == pytest.approx(2.0)
    assert m.calmar_ratio(0.3, D(0)) is None
