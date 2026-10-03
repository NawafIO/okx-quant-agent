"""Bit-identical determinism, future-corruption invariance, golden fixture (M2 acceptance)."""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

from okxq.backtest.engine import BacktestResult
from okxq.backtest.reference_strategies import RandomEntry
from okxq.backtest.sizing import ProvisionalFixedFractionalSizer
from okxq.backtest.types import BarSeries, FundingRate

from .conftest import INST, engine, series, ts

N = 400
CUT = 250


def random_walk(n: int, seed: int) -> BarSeries:
    """Deterministic synthetic bars on a 0.1 tick grid."""
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture
    price = 1000.0
    rows = []
    for _ in range(n):
        o = round(price, 1)
        c = round(max(1.0, o * (1 + rng.gauss(0, 0.01))), 1)
        h = round(max(o, c) * (1 + abs(rng.gauss(0, 0.004))), 1)
        low = round(min(o, c) * (1 - abs(rng.gauss(0, 0.004))), 1)
        v = round(50 + rng.random() * 100, 3)
        rows.append((str(o), str(h), str(low), str(c), str(v)))
        price = c
    return series(rows)


def funding(n: int, seed: int) -> list[FundingRate]:
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture
    return [
        FundingRate(ts(i), Decimal(str(round(rng.gauss(0.0001, 0.0002), 6))))
        for i in range(-8, n + 8, 8)
    ]


SIZER = ProvisionalFixedFractionalSizer(risk_fraction=Decimal("0.01"), leverage=Decimal(5))


def run(bars: BarSeries, fund: list[FundingRate], *, end: int, seed: int = 7) -> BacktestResult:
    return engine(
        bars, funding=fund, start=ts(20), end=end, sizer=SIZER, participation_cap="0.05", ttl=2
    ).run(RandomEntry(seed=seed, p_entry=0.08))


def corrupt_after(
    bars: BarSeries, fund: list[FundingRate], cut: int
) -> tuple[BarSeries, list[FundingRate]]:
    """Replace every bar opening at or after bar ``cut``, and every later funding rate, with
    nonsense that is still structurally valid."""
    k = cut

    def mangle(col: tuple[Decimal, ...], factor: str) -> tuple[Decimal, ...]:
        return col[:k] + tuple(x * Decimal(factor) for x in col[k:])

    bad = replace(
        bars,
        open=mangle(bars.open, "3"),
        high=mangle(bars.high, "5"),
        low=mangle(bars.low, "0.2"),
        close=mangle(bars.close, "0.4"),
        volume_base=mangle(bars.volume_base, "0.01"),
    )
    bad_fund = [r if r.ts_ms < ts(cut) else replace(r, rate=Decimal("0.05")) for r in fund]
    return bad, bad_fund


BARS = random_walk(N, seed=1)
FUND = funding(N, seed=2)


def test_identical_inputs_give_bit_identical_results() -> None:
    a = run(BARS, FUND, end=ts(N))
    b = run(BARS, FUND, end=ts(N))
    assert len(a.trades) > 10  # the fixture must exercise the engine, not idle
    assert a.canonical_json() == b.canonical_json()


def test_corrupting_all_future_data_leaves_a_completed_backtest_bit_identical() -> None:
    clean = run(BARS, FUND, end=ts(CUT))
    bad_bars, bad_fund = corrupt_after(BARS, FUND, CUT)
    dirty = run(bad_bars, bad_fund, end=ts(CUT))
    assert len(clean.trades) > 5
    assert clean.digest() == dirty.digest()


def test_nothing_decided_before_the_cut_depends_on_data_after_it() -> None:
    """Stronger form: run THROUGH the corrupted region; everything up to the cut must agree."""
    clean = run(BARS, FUND, end=ts(N))
    bad_bars, bad_fund = corrupt_after(BARS, FUND, CUT)
    dirty = run(bad_bars, bad_fund, end=ts(N))

    cut = ts(CUT)
    assert [p for p in clean.equity_curve if p.ts_ms <= cut] == [
        p for p in dirty.equity_curve if p.ts_ms <= cut
    ]
    assert [f for f in clean.fills if f.ts_ms < ts(CUT)] == [
        f for f in dirty.fills if f.ts_ms < ts(CUT)
    ]
    assert [e for e in clean.funding if e.ts_ms < ts(CUT)] == [
        e for e in dirty.funding if e.ts_ms < ts(CUT)
    ]
    assert clean.digest() != dirty.digest()  # the corruption itself did take effect


#: Golden fixture. If this changes, engine SEMANTICS changed: review the diff of
#: ``canonical_json()`` deliberately, record why in the commit, then update the digest.
GOLDEN_DIGEST = "dece7ca1dee4e92535084fb08f573954e670ac7d67a4089c7aaeb02aa5b37396"


def test_golden_fixture() -> None:
    result = run(BARS, FUND, end=ts(N))
    assert result.digest() == GOLDEN_DIGEST, result.digest()


def test_random_entry_seed_is_the_only_source_of_randomness() -> None:
    a = run(BARS, FUND, end=ts(N), seed=11)
    b = run(BARS, FUND, end=ts(N), seed=12)
    assert a.digest() != b.digest()
    assert INST in {t.inst_id for t in a.trades}
