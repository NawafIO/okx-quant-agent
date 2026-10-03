"""slip-v2 spread estimator (T-1 property) and the non-vacuous sanity evaluator."""

from __future__ import annotations

import math
from decimal import Decimal as D  # noqa: N817 - fixture brevity

from hypothesis import given, settings
from hypothesis import strategies as st

from okxq.backtest import sanity
from okxq.backtest.engine import BacktestResult
from okxq.backtest.metrics import EquityPoint
from okxq.backtest.spread import abdi_ranaldo_series, abdi_ranaldo_spread
from okxq.backtest.types import ClosedTrade, Side

bar = st.tuples(
    st.floats(min_value=10, max_value=1000),
    st.floats(min_value=0.0, max_value=0.05),
    st.floats(min_value=0.0, max_value=1.0),
)


def ohlc(raw: list[tuple[float, float, float]]) -> tuple[list[float], list[float], list[float]]:
    h, lo, c = [], [], []
    for mid, rng, pos in raw:
        hi, low = mid * (1 + rng), mid * (1 - rng)
        h.append(hi)
        lo.append(low)
        c.append(low + pos * (hi - low))
    return h, lo, c


@settings(max_examples=200, deadline=None)
@given(st.lists(bar, min_size=3, max_size=40), st.integers(min_value=2, max_value=12))
def test_spread_estimator_has_no_look_ahead(raw: list[tuple[float, float, float]], lb: int) -> None:
    """T-1: the value at bar n never changes when later bars arrive."""
    h, lo, c = ohlc(raw)
    full = abdi_ranaldo_series(h, lo, c, lb)
    for n in range(1, len(c) + 1):
        assert abdi_ranaldo_series(h[:n], lo[:n], c[:n], lb)[-1] == full[n - 1]


@settings(max_examples=200, deadline=None)
@given(st.lists(bar, min_size=2, max_size=40))
def test_spread_estimate_is_never_negative(raw: list[tuple[float, float, float]]) -> None:
    h, lo, c = ohlc(raw)
    assert abdi_ranaldo_spread(h, lo, c) >= 0


def test_spread_by_hand() -> None:
    # Two bars: c0 at the high of bar 0, bar 1 symmetric around 100.
    h, lo, c = [101.0, 101.0], [99.0, 99.0], [101.0, 100.0]
    eta = (math.log(101) + math.log(99)) / 2
    expected = math.sqrt(max(0.0, 4 * (math.log(101) - eta) * (math.log(101) - eta)))
    assert abdi_ranaldo_spread(h, lo, c) == expected


# --- sanity evaluator ---------------------------------------------------------------------


def result(trades: list[tuple[str, Side, str, str, str]], final: str = "100000") -> BacktestResult:
    """trades: (inst, side, gross, fees, slippage) on a 1000-notional entry."""
    ts = [
        ClosedTrade(inst, side, 0, 1, D(10), D(100), D(100), D(g), D(f), D(0), D(s), "signal")
        for inst, side, g, f, s in trades
    ]
    return BacktestResult(
        "t", (EquityPoint(0, D(100000)), EquityPoint(1, D(final))), tuple(ts), (), (), (), {}
    )


LADDER = [(D(1000), 1.0), (D(100000), 2.0), (D(1000000), 5.0)]
FAIR = sanity.SanityPolicy(min_trades=4, max_stderr_bps=50.0)


def with_rejection(r: BacktestResult, reason: str) -> BacktestResult:
    from dataclasses import replace

    from okxq.backtest.types import Rejection

    return replace(r, rejections=(Rejection(0, "A", reason),))


def symmetric(inst: str = "A") -> list[tuple[str, Side, str, str, str]]:
    # pre-cost (gross + slippage) +5 / -5 bps on alternating trades -> mean 0
    out = []
    for side in (Side.LONG, Side.SHORT):
        out += [(inst, side, "-0.3", "1", "0.8"), (inst, side, "-1.3", "1", "0.8")]
    return out


def test_a_clean_cost_model_passes() -> None:
    rep = sanity.evaluate([result(symmetric())], D(100000), LADDER, FAIR)
    assert rep.passed, rep.checks


def test_too_few_trades_fails_on_power() -> None:
    rep = sanity.evaluate([result(symmetric())], D(100000), LADDER)  # default 10,000 trades
    assert not rep.passed
    assert {c.name for c in rep.checks if not c.passed} == {"power"}


def test_a_sizing_refusal_fails_the_gate() -> None:
    for reason in ("insufficient_margin", "sizer:below_min_size"):
        r = with_rejection(result(symmetric()), reason)
        rep = sanity.evaluate([r], D(100000), LADDER, FAIR)
        assert {c.name for c in rep.checks if not c.passed} == {"no entry degraded by sizing"}


def test_liquidity_outcomes_are_reported_not_gated() -> None:
    """Ruling: no volume -> no fill is the engine being right; report it, never drop it."""
    for reason in ("entry_no_liquidity", "entry_remainder_expired"):
        rep = sanity.evaluate(
            [with_rejection(result(symmetric()), reason)], D(100000), LADDER, FAIR
        )
        assert rep.passed
        (line,) = [c for c in rep.checks if c.name.startswith("liquidity outcomes")]
        assert not line.gated
        assert line.detail.startswith("1 entries")


def test_real_data_pre_cost_is_reported_not_gated() -> None:
    """Ruling: on real data the pre-cost mean measures the strategy meeting the market."""
    biased = [(i, s, "1.0", "1", "0.8") for i, s, *_ in symmetric()]  # pre-cost +18 bps
    rep = sanity.evaluate([result(biased)], D(100000), LADDER, FAIR)
    info = [c for c in rep.checks if c.name.startswith("pre-cost")]
    assert len(info) == 1
    assert not info[0].gated
    assert "+18" in info[0].detail


def test_a_winning_instrument_side_fails() -> None:
    # B longs receive something the cost model forgot: net positive on that cell.
    rows = [*symmetric("A"), ("B", Side.LONG, "3", "0.1", "0"), ("B", Side.LONG, "3", "0.1", "0")]
    rep = sanity.evaluate([result(rows)], D(100000), LADDER, FAIR)
    failed = {c.name: c.detail for c in rep.checks if not c.passed}
    assert "every instrument and side loses" in failed
    assert "B LONG" in failed["every instrument and side loses"]


def test_dead_impact_term_fails() -> None:
    flat = [(D(1000), 3.0), (D(100000), 3.0)]
    rep = sanity.evaluate([result(symmetric())], D(100000), flat, FAIR)
    assert "impact is live" in {c.name for c in rep.checks if not c.passed}
