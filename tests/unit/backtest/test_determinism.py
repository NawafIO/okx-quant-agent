"""Bit-identical determinism, future-corruption invariance, golden fixture (M2 acceptance)."""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

from okxq.backtest.engine import BacktestResult
from okxq.backtest.reference_strategies import RandomEntry
from okxq.backtest.sizing import ProvisionalFixedFractionalSizer
from okxq.backtest.types import BarSeries, FillRecord, FundingRate

from .conftest import INST, engine, series, ts

INTRABAR = {"stop", "take_profit", "liquidation"}

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
#: History: dece7ca1... -> 4b1f375c... (funding-bound-v1 added a ``bound`` flag to funding
#: events and an assumption entry; with both stripped the output hashes to dece7ca1 exactly).
#: 4b1f375c... -> a6882ae7... (slip-v2: only the slippage assumption LABEL changed for this
#: floor-only fixture; with the old label restored it hashes to 4b1f375c exactly).
#: a6882ae7... -> 37e9cb8b... (label now records stop_overshoot_k=0; the fixture runs
#: with the term disabled, and with that token stripped it hashes to a6882ae7 exactly).
GOLDEN_DIGEST = "37e9cb8bd04eba9771ecb38b38e149f031ba3b8d9ae9f2b0a5c84706e9d4b084"


def test_golden_fixture() -> None:
    result = run(BARS, FUND, end=ts(N))
    assert result.digest() == GOLDEN_DIGEST, result.digest()


def test_random_entry_seed_is_the_only_source_of_randomness() -> None:
    a = run(BARS, FUND, end=ts(N), seed=11)
    b = run(BARS, FUND, end=ts(N), seed=12)
    assert a.digest() != b.digest()
    assert INST in {t.inst_id for t in a.trades}


def corrupt_from_open(bars: BarSeries, cut: int) -> BarSeries:
    """Corrupt bar ``cut``'s high/low/close/volume and everything after it, but keep its OPEN:
    the open is the price AT T_open, which a fill at T_open legitimately uses."""

    def mangle(col: tuple[Decimal, ...], factor: str, first: int) -> tuple[Decimal, ...]:
        return col[:first] + tuple(x * Decimal(factor) for x in col[first:])

    return replace(
        bars,
        open=mangle(bars.open, "3", cut + 1),
        high=mangle(bars.high, "5", cut),
        low=mangle(bars.low, "0.2", cut),
        close=mangle(bars.close, "0.4", cut),
        volume_base=mangle(bars.volume_base, "0.01", cut),
    )


def test_multi_instrument_decisions_at_t_open_ignore_the_bar_that_follows() -> None:
    """General invariant across instruments: nothing known at T_open - fills at the open,
    rejections - may depend on any instrument's data after T_open.

    NOTE: this does NOT catch checkpoint-3 finding 1 on the old engine (verified): corrupting
    A also fires A's own stops, which frees margin and masks the leak. The targeted
    regression for that defect is ``test_margin_check_at_t_open_cannot_see_another_
    instruments_bar`` in test_engine.py, which does fail on the old engine.
    """
    from okxq.backtest.sizing import ProvisionalFixedFractionalSizer

    a = replace(random_walk(N, seed=21), inst_id="A-USDT-SWAP")
    b = replace(random_walk(N, seed=22), inst_id="B-USDT-SWAP")
    fund = funding(N, seed=23)
    # Each position is capped at 45% of equity at 1x, so two just fit. A corrupted 60% drop
    # in one instrument's close would flip the other's margin check - if the engine read it.
    tight = ProvisionalFixedFractionalSizer(Decimal("0.5"), Decimal(1), Decimal("0.45"))

    def go(sa: BarSeries, sb: BarSeries) -> BacktestResult:
        return engine(
            {"A-USDT-SWAP": sa, "B-USDT-SWAP": sb},
            funding={"A-USDT-SWAP": fund, "B-USDT-SWAP": fund},
            start=ts(20),
            end=ts(N),
            sizer=tight,
            participation_cap="0.5",
        ).run(RandomEntry(seed=3, p_entry=0.3, hold_bars=3))

    clean = go(a, b)
    assert {f.inst_id for f in clean.fills} == {"A-USDT-SWAP", "B-USDT-SWAP"}
    for cut in range(30, N - 10, 7):
        dirty = go(corrupt_from_open(a, cut), corrupt_from_open(b, cut))
        t = ts(cut)

        def known_at_open(f: FillRecord, t: int = t) -> bool:
            # Intrabar exits are stamped with their bar's T_open but resolve from its
            # high/low, which is legitimately unknown at T_open.
            return f.ts_ms < t or (f.ts_ms == t and f.reason not in INTRABAR)

        assert [f for f in clean.fills if known_at_open(f)] == [
            f for f in dirty.fills if known_at_open(f)
        ]
        assert [x for x in clean.rejections if x.ts_ms <= t] == [
            x for x in dirty.rejections if x.ts_ms <= t
        ]
