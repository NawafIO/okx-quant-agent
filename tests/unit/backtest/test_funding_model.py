"""D1 funding reconstruction: fitting, quarantine, and the validation gate, on synthetic data.

These prove the MACHINERY. Whether the model is good enough for OKX is decided only by
``validate`` on the real 95-day overlap, which needs the market data (not yet available
in this environment).
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from okxq.backtest import funding_model as fm
from okxq.backtest.types import BarSeries, FundingRate

H = 3_600_000
T0 = 1_782_000_000_000 // (8 * H) * (8 * H)
DAYS = 95


def premium_path(seed: int, hours: int) -> list[float]:
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture
    p, out = 0.0, []
    for _ in range(hours):
        p = 0.95 * p + rng.gauss(0, 0.0002)
        out.append(p)
    return out


def bars(inst: str, closes: list[float], skip: set[int] = frozenset()) -> BarSeries:  # type: ignore[assignment]
    idx = [i for i in range(len(closes)) if i not in skip]
    d = [Decimal(repr(round(closes[i], 8))) for i in idx]
    return BarSeries(
        inst,
        H,
        tuple(T0 + i * H for i in idx),
        tuple(d),
        tuple(d),
        tuple(d),
        tuple(d),
        tuple(Decimal(0) for _ in idx),
    )


def instrument(
    inst: str, seed: int, interval_h: int, *, related: bool = True, skip: set[int] | None = None
) -> tuple[BarSeries, BarSeries, list[FundingRate]]:
    hours = DAYS * 24
    prem = premium_path(seed, hours)
    index = [100.0] * hours
    mark = [100.0 * (1 + p) for p in prem]
    rng = random.Random(seed + 1000)  # noqa: S311
    realised = []
    for k in range(1, hours // interval_h):
        t = T0 + k * interval_h * H
        window = prem[(k - 1) * interval_h : k * interval_h]
        mean_p = sum(window) / len(window)
        rate = 0.5 * mean_p + 0.0001 if related else rng.choice([-1, 1]) * 0.0003
        realised.append(FundingRate(t, Decimal(repr(round(rate, 8)))))
    return bars(inst, mark, skip or set()), bars(inst, index), realised


def test_recovers_a_known_relationship_and_passes() -> None:
    data = {"A": instrument("A", 1, 8), "B": instrument("B", 2, 8), "C": instrument("C", 3, 4)}
    report = fm.validate(data)
    assert report.verdict is fm.FundingVerdict.PASS, report
    assert report.model is not None
    assert report.model.a == pytest.approx(0.5, rel=0.02)
    assert report.model.b == pytest.approx(0.0001, rel=0.05)
    by = {v.inst_id: v for v in report.instruments}
    assert by["C"].interval_ms == 4 * H  # read per instrument, never assumed (V-13)
    assert by["A"].interval_ms == 8 * H


def test_escalates_when_funding_is_unrelated_to_the_premium() -> None:
    data = {k: instrument(k, s, 8, related=False) for k, s in (("A", 1), ("B", 2))}
    report = fm.validate(data)
    assert report.verdict is fm.FundingVerdict.ESCALATE
    assert not all(v.passed for v in report.instruments)


def test_fit_and_validation_windows_are_disjoint_in_time() -> None:
    mark, index, realised = instrument("A", 1, 8)
    report = fm.validate({"A": (mark, index, realised)})
    (v,) = report.instruments
    assert report.model is not None
    n = len(realised)
    # ~60% fit, ~40% validation; neither may contain the other.
    assert report.model.n_fit + v.n_intervals <= n
    assert v.n_intervals == pytest.approx(0.4 * n, rel=0.05)


def test_missing_premium_hours_are_quarantined_not_filled() -> None:
    hours = DAYS * 24
    prem = {T0 + i * H: 0.001 for i in range(hours) if not 100 <= i < 108}
    settle = T0 + 108 * H  # interval (100h, 108h] entirely missing
    assert fm.interval_premium(prem, settle, 8 * H, H, 0.75) is None
    model = fm.FundingModel(a=1.0, b=0.0, lo=-1, hi=1, n_fit=10)
    rates = fm.model_series(model, prem, [settle, settle + 8 * H], 8 * H)
    assert [r.ts_ms for r in rates] == [settle + 8 * H]  # the uncovered settlement is absent
    assert all(r.modelled for r in rates)


def test_backward_schedule_keeps_the_measured_grid() -> None:
    first = T0 + 8 * H
    s = fm.schedule(first, 8 * H, first - 5 * 8 * H - 3 * H, first)
    assert s == [first - k * 8 * H for k in (5, 4, 3, 2, 1)]


def test_near_zero_realised_drag_uses_the_absolute_floor() -> None:
    mark, index, realised = instrument("A", 1, 8)
    # Alternate signs so cumulative realised drag is ~0.
    flipped = [
        FundingRate(r.ts_ms, Decimal("0.0001") * (1 if i % 2 else -1))
        for i, r in enumerate(realised)
    ]
    report = fm.validate({"A": (mark, index, flipped)})
    (v,) = report.instruments
    assert v.used_abs_floor
    assert v.rel_error is None


def test_too_little_data_is_not_a_pass() -> None:
    mark, index, realised = instrument("A", 1, 8)
    assert (
        fm.validate({"A": (mark, index, realised[:5])}).verdict
        is fm.FundingVerdict.INSUFFICIENT_DATA
    )


def test_model_output_is_clipped_to_the_fit_range() -> None:
    model = fm.FundingModel(a=1.0, b=0.0, lo=-0.001, hi=0.002, n_fit=3)
    assert model.rate(0.5) == 0.002
    assert model.rate(-0.5) == -0.001
