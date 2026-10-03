"""Regime classifier on SYNTHETIC data only. Real-data validation runs only after the answer
keys are committed (pre-registration, Chief Advisor M3 checkpoint 1)."""

from __future__ import annotations

import inspect
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from okxq.analysis import regime as rg
from okxq.analysis.regime import DAY_MS, Regime
from okxq.analysis.ta import Bars


def walk(rets: np.ndarray, rng_pct: float = 0.01) -> Bars:
    c = 100 * np.cumprod(1 + rets)
    o = np.concatenate(([100.0], c[:-1]))
    h = np.maximum(o, c) * (1 + rng_pct)
    lo = np.minimum(o, c) * (1 - rng_pct)
    return Bars.of(o, h, lo, c, np.ones(len(c)))


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 10_000),
    n=st.integers(min_value=230, max_value=320),
    cut=st.integers(min_value=1, max_value=320),
)
def test_labels_have_no_look_ahead(seed: int, n: int, cut: int) -> None:
    """T-1 on the classifier itself (finding M-2): label(x[:k])[-1] == label(x)[k-1]."""
    rets = np.random.default_rng(seed).normal(0.0005, 0.03, n)
    b = walk(rets)
    k = min(cut, n)
    assert rg.classify(b.head(k))[-1] == rg.classify(b)[k - 1]


def test_labels_are_defined_and_varied_past_warmup() -> None:
    """Non-vacuity: past the warm-up the T-1 property is checked on real labels."""
    rets = np.random.default_rng(5).normal(0.0, 0.03, 400)
    labels = rg.classify(walk(rets))
    late = labels[250:]
    assert Regime.UNDEFINED not in late
    assert len(set(late)) >= 2


def test_undefined_until_inputs_exist_never_default_range() -> None:
    labels = rg.classify(walk(np.full(150, 0.001)))
    # vol rank needs 20 + 180 bars: until then, everything but CRISIS is UNDEFINED.
    assert set(labels[:150]) == {Regime.UNDEFINED}


def test_crisis_fires_from_the_first_week() -> None:
    rets = np.full(30, 0.001)
    rets[6] = -0.12  # one-day fall of 12%
    labels = rg.classify(walk(rets))
    assert labels[6] is Regime.CRISIS
    assert labels[7] is Regime.UNDEFINED  # no vol history yet: not a default label


def test_five_day_crash_is_crisis() -> None:
    rets = np.full(40, 0.0)
    rets[20:25] = -0.05  # five days of -5%: -22.6% over 5 days, no single day below -10%
    labels = rg.classify(walk(rets))
    assert labels[24] is Regime.CRISIS
    assert all(lbl is not Regime.CRISIS for lbl in labels[:23])


def test_vol_rank_compares_against_the_past_only() -> None:
    rets = np.random.default_rng(1).normal(0, 0.01, 400)
    rets[-1] = 0.09  # a spike on the last day (below the crisis threshold)
    x = rg.inputs(walk(rets), rg.FROZEN_REGIME)
    assert x.vol_rank[-1] > 0.95
    # the earlier ranks did not know about the spike
    x_before = rg.inputs(walk(rets[:-1]), rg.FROZEN_REGIME)
    assert np.array_equal(x.vol_rank[:-1], x_before.vol_rank, equal_nan=True)


def _share(labels: list[Regime], lab: Regime) -> float:
    return sum(1 for x in labels[-80:] if x is lab) / 80


def test_trends_are_recognised() -> None:
    rng = np.random.default_rng(2)
    calm = rng.normal(0, 0.005, 250)  # vol history for the rank
    up = np.concatenate([calm, rng.normal(0.006, 0.006, 120)])
    down = np.concatenate([calm, rng.normal(-0.006, 0.006, 120)])
    assert _share(rg.classify(walk(up, 0.003)), Regime.TREND_UP) >= 0.6
    assert _share(rg.classify(walk(down, 0.003)), Regime.TREND_DOWN) >= 0.6


def test_a_mean_reverting_market_is_range() -> None:
    """RANGE needs a genuinely mean-reverting fixture: a driftless random WALK wanders, and
    the classifier rightly calls its excursions trends (measured: 55/80 TREND_DOWN)."""
    rng = np.random.default_rng(2)
    p = [100.0]
    for _ in range(369):
        p.append(p[-1] + 0.2 * (100 - p[-1]) + rng.normal(0, 0.5))  # Ornstein-Uhlenbeck
    prices = np.array(p)
    rets = np.concatenate(([0.0], prices[1:] / prices[:-1] - 1))
    assert _share(rg.classify(walk(rets, 0.003)), Regime.RANGE) >= 0.6


def test_hourly_alignment_uses_the_previous_closed_day() -> None:
    """Finding M-1: day D's label is known at D's close (D+1 00:00), never during day D."""
    days = [0, DAY_MS, 2 * DAY_MS]
    labels = [Regime.RANGE, Regime.TREND_UP, Regime.CRISIS]
    hours = [h * 3_600_000 for h in range(0, 72)]
    out = rg.align_to_hourly(days, labels, hours)
    assert set(out[:24]) == {Regime.UNDEFINED}  # day 0 has not closed yet
    assert set(out[24:48]) == {Regime.RANGE}  # day 0's label, known at 24:00
    assert set(out[48:72]) == {Regime.TREND_UP}  # day 1's label; day 2's not yet known


@settings(max_examples=30, deadline=None)
@given(n_days=st.integers(2, 20), cut=st.integers(1, 480))
def test_hourly_alignment_has_no_look_ahead(n_days: int, cut: int) -> None:
    days = [d * DAY_MS for d in range(n_days)]
    labels = [list(Regime)[d % 5] for d in range(n_days)]
    hours = [h * 3_600_000 for h in range(n_days * 24)]
    k = min(cut, len(hours))
    # Only days that have CLOSED by hour k-1 may be visible to it.
    closed = [d for d in days if d + DAY_MS <= hours[k - 1]]
    visible = labels[: len(closed)]
    full = rg.align_to_hourly(days, labels, hours)[k - 1]
    truncated = rg.align_to_hourly(days[: len(closed)], visible, hours[:k])[-1]
    assert full == truncated


def test_thresholds_are_pinned_and_not_a_parameter() -> None:
    with pytest.raises(rg.RegimeTamperError):
        replace(rg.FROZEN_REGIME, adx_trend=25.0)
    assert list(inspect.signature(rg.classify).parameters) == ["b"]
    assert rg.LIVE_MIN_HISTORY_DAYS == 450


def test_sensitivity_path_accepts_other_thresholds() -> None:
    rets = np.random.default_rng(3).normal(0, 0.03, 450)
    strict = rg.RegimeParams(adx_trend=40.0)
    assert rg.classify_with(walk(rets), strict) != rg.classify(walk(rets))
