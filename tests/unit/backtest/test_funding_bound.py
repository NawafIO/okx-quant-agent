"""funding-bound-v1: the adverse bound ruled after D1 failed validation."""

from __future__ import annotations

from decimal import Decimal as D  # noqa: N817 - fixture brevity
from pathlib import Path

import pytest

from okxq.backtest import funding_bound as fb
from okxq.backtest.types import G9_STRESS, Action, FundingRate, OrderIntent

from .conftest import INST, Scripted, engine, series, ts

FLAT = ("100", "101", "99", "100", "1000")
H = 3_600_000


def realised() -> list[FundingRate]:
    rates = ["0.0001", "-0.0003", "0.00005", "0.0001", "0"]
    return [FundingRate(ts(100) + i * 8 * H, D(r)) for i, r in enumerate(rates)]


def test_derive_takes_the_adverse_extreme_per_side() -> None:
    b = fb.derive(INST, realised())
    assert (b.cost_long, b.cost_short) == (D("0.0001"), D("0.0003"))
    assert b.interval_ms == 8 * H
    assert b.anchor_ms == ts(100)


def test_a_one_sided_history_never_credits() -> None:
    b = fb.derive(INST, [FundingRate(ts(i * 8), D("0.0001")) for i in range(5)])
    assert b.cost_short == 0  # never negative: a bound is never a receipt


def test_bound_series_stops_at_the_first_realised_settlement() -> None:
    b = fb.derive(INST, realised())
    s = fb.bound_series(b, ts(60), ts(200))
    assert [r.ts_ms for r in s] == [ts(100) - k * 8 * H for k in (5, 4, 3, 2, 1)]
    assert all(r.is_bound and r.modelled for r in s)


def test_round_trip_through_the_frozen_file(tmp_path: Path) -> None:
    b = fb.derive(INST, realised())
    path = tmp_path / "bound.json"
    fb.write_bounds([b], path)
    assert fb.load_bounds(path) == {INST: b}
    assert fb.load_bounds(tmp_path / "missing.json") == {}


@pytest.mark.parametrize(
    ("action", "stop", "expected"),
    [
        # Three charged settlements: ts(2) (entry boundary, charged adversely), ts(3)
        # (held across) and ts(4) (exit boundary, charged adversely).
        # long: 3 x bound_long 0.0001 x open 100
        (Action.ENTER_LONG, "90", D("-0.03")),
        # short: 3 x bound_short 0.0003 x 100 - although the "rate" field is positive,
        # which would normally be a RECEIPT for a short
        (Action.ENTER_SHORT, "110", D("-0.09")),
    ],
)
def test_bound_is_a_cost_to_either_side(action: Action, stop: str, expected: D) -> None:
    bars = series([FLAT] * 6)
    fund = [FundingRate(ts(i), D("0.0001"), True, D("0.0001"), D("0.0003")) for i in range(-1, 8)]
    r = engine(bars, funding=fund, start=ts(1), end=ts(5)).run(
        Scripted(
            {ts(2): [OrderIntent(INST, action, D(stop))], ts(4): [OrderIntent(INST, Action.EXIT)]}
        )
    )
    (t,) = r.trades
    assert t.funding == expected
    assert all(e.bound for e in r.funding)


def test_g9_doubles_the_bound() -> None:
    bars = series([FLAT] * 6)
    fund = [FundingRate(ts(i), D("0.0001"), True, D("0.0001"), D("0.0003")) for i in range(-1, 8)]
    script = {
        ts(2): [OrderIntent(INST, Action.ENTER_LONG, D(90))],
        ts(4): [OrderIntent(INST, Action.EXIT)],
    }
    base = engine(bars, funding=fund, start=ts(1), end=ts(5)).run(Scripted(dict(script)))
    stressed = engine(bars, funding=fund, start=ts(1), end=ts(5), stress=G9_STRESS).run(
        Scripted(dict(script))
    )
    assert stressed.trades[0].funding == 2 * base.trades[0].funding
