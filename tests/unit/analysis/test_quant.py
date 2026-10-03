"""Quant engine against hand-computed or analytically known values."""

from __future__ import annotations

import math

import numpy as np
import pytest

from okxq.analysis import quant as q


def test_log_returns_and_vol_by_hand() -> None:
    c = np.array([100.0, 110.0, 99.0])
    r = q.log_returns(c)
    assert r == pytest.approx([math.log(1.1), math.log(0.9)])
    sd = abs(math.log(1.1) - math.log(0.9)) / math.sqrt(2)  # sample sd of two values
    assert q.realised_vol(r, 4) == pytest.approx(sd * 2)
    assert math.isnan(q.realised_vol(r[:1], 4))


def test_parkinson_by_hand() -> None:
    h, lo = np.array([110.0, 121.0]), np.array([100.0, 110.0])
    expected = math.sqrt(math.log(1.1) ** 2 / (4 * math.log(2)))
    assert q.parkinson_vol(h, lo, 1) == pytest.approx(expected)


def test_iid_returns_look_like_a_random_walk() -> None:
    r = np.random.default_rng(0).normal(0, 0.01, 20_000)
    assert abs(q.autocorrelation(r, 1)) < 0.03
    assert q.variance_ratio(r, 10) == pytest.approx(1.0, abs=0.08)
    assert q.hurst_aggregated_variance(r) == pytest.approx(0.5, abs=0.06)


def test_mean_reverting_and_trending_series_are_told_apart() -> None:
    rng = np.random.default_rng(1)
    e = rng.normal(0, 0.01, 20_000)
    reverting = e - 0.6 * np.concatenate(([0.0], e[:-1]))  # MA(1), negative autocorrelation
    trending = np.zeros_like(e)
    for i in range(1, len(e)):
        trending[i] = 0.5 * trending[i - 1] + e[i]  # AR(1), positive autocorrelation
    assert q.variance_ratio(reverting, 10) < 0.7
    assert q.variance_ratio(trending, 10) > 1.5
    assert q.hurst_aggregated_variance(reverting) < 0.45
    assert q.hurst_aggregated_variance(trending) > 0.55


def test_beta_by_construction() -> None:
    rng = np.random.default_rng(2)
    m = rng.normal(0, 0.01, 5_000)
    a = 1.5 * m + rng.normal(0, 0.002, 5_000)
    assert q.beta(a, m) == pytest.approx(1.5, abs=0.02)


def test_correlation_is_pairwise_complete_never_filled() -> None:
    rng = np.random.default_rng(3)
    base = rng.normal(size=200)
    a = {t: float(base[t]) for t in range(200)}
    b = {t: float(base[t] + 0.1 * rng.normal()) for t in range(0, 200, 2)}  # half the stamps
    c = {t: float(rng.normal()) for t in range(150, 200)}  # 50 obs only
    cm = q.correlation_matrix({"A": a, "B": b, "C": c}, min_overlap=60)
    i = {n: k for k, n in enumerate(cm.names)}
    assert cm.overlap[i["A"], i["B"]] == 100  # only stamps both have
    assert cm.rho[i["A"], i["B"]] > 0.95
    assert math.isnan(cm.rho[i["A"], i["C"]])  # 50 < 60 common observations: undefined


def test_average_linkage_groups_correlated_names() -> None:
    names = ("BTC", "ETH", "SOL", "XAU")
    rho = np.array(
        [
            [1.0, 0.9, 0.8, 0.1],
            [0.9, 1.0, 0.85, 0.05],
            [0.8, 0.85, 1.0, 0.0],
            [0.1, 0.05, 0.0, 1.0],
        ]
    )
    cm = q.CorrelationMatrix(names, rho, np.full((4, 4), 500, dtype=np.int64))
    merges = q.average_linkage(cm)
    assert merges[0].distance == pytest.approx(0.1)  # BTC-ETH first
    assert {frozenset({"BTC", "ETH", "SOL"}), frozenset({"XAU"})} == set(q.clusters_at(cm, 0.5))


def test_funding_stats_read_the_interval() -> None:
    h = 3_600_000
    st = q.funding_stats([0, 4 * h, 8 * h, 12 * h], [0.0001, -0.0001, 0.0002, 0.0002])
    assert st is not None
    assert st.interval_hours == 4
    assert st.annualised == pytest.approx(0.0001 * 6 * 365)
    assert st.share_positive == 0.75


def test_every_quant_function_is_marked_batch() -> None:
    public = {
        n
        for n in dir(q)
        if not n.startswith("_")
        and callable(getattr(q, n))
        and getattr(getattr(q, n), "__module__", "") == q.__name__
        and not isinstance(getattr(q, n), type)
    }
    assert public <= set(q.KIND), public - set(q.KIND)
    assert set(q.KIND.values()) == {"batch"}
