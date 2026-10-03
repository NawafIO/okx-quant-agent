"""Engine realism and M2 acceptance tests on synthetic data.

Every expected number is worked by hand in a comment. Slippage is configured at its
one-tick floor (0.1) so the arithmetic stays checkable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal as D  # noqa: N817 - fixture brevity

import pytest

from okxq.backtest.engine import BacktestEngine, BacktestResult, EngineConfig, StrategyContext
from okxq.backtest.sizing import SizeDecision
from okxq.backtest.types import (
    G9_STRESS,
    Action,
    BacktestConfigError,
    BarSeries,
    InstrumentSpec,
    OrderIntent,
    Provenance,
    SlippageModel,
)

from .conftest import (
    FEES,
    INST,
    SPEC,
    FixedSizer,
    Scripted,
    engine,
    flat_funding,
    series,
    ts,
)

FLAT = ("100", "101", "99", "100", "1000")


def enter(stop: str, tp: str | None = None, action: Action = Action.ENTER_LONG) -> OrderIntent:
    return OrderIntent(INST, action, D(stop), D(tp) if tp else None)


EXIT = OrderIntent(INST, Action.EXIT)

# --- oracle: arithmetically expected P&L to the cent (M2 acceptance) ---------------------

ORACLE_BARS = series(
    [
        FLAT,  # 0 warm-up
        FLAT,  # 1 close at ts(2): signal ENTER_LONG
        ("100", "111", "99", "110", "1000"),  # 2 entry fills at open 100 + 0.1
        ("110", "121", "109", "120", "1000"),  # 3 close at ts(4): signal EXIT
        ("120", "121", "119", "120", "1000"),  # 4 exit fills at open 120 - 0.1
    ]
)
ORACLE_FUNDING = flat_funding(6, {3: "0.0001", 4: "0.0001"})
ORACLE_SCRIPT = {ts(2): [enter("90")], ts(4): [EXIT]}


def test_oracle_pnl_to_the_cent_with_fees_and_funding() -> None:
    result = engine(ORACLE_BARS, funding=ORACLE_FUNDING, start=ts(1), end=ts(5)).run(
        Scripted(dict(ORACLE_SCRIPT))
    )
    # entry 100.1, fee 100.1 * 0.0005 = 0.05005
    # exit  119.9, fee 119.9 * 0.0005 = 0.05995      gross = 119.9 - 100.1 = 19.8
    # funding ts(3): long 1 x open 110 x 0.0001 = -0.011 (held across)
    # funding ts(4): exit AT the settlement -> charged adversely: 1 x 120 x 0.0001 = -0.012
    # net = 19.8 - 0.11 - 0.023 = 19.667
    (trade,) = result.trades
    assert trade.avg_entry == D("100.1")
    assert trade.avg_exit == D("119.9")
    assert trade.gross_pnl == D("19.8")
    assert trade.fees == D("0.11")
    assert trade.funding == D("-0.023")
    assert trade.net_pnl == D("19.667")
    assert result.equity_curve[-1].equity == D("10019.667")
    # MTM at ts(3) (close of bar 2): cash 10000 - 0.05005, unrealised 110 - 100.1 = 9.9
    curve = {p.ts_ms: p.equity for p in result.equity_curve}
    assert curve[ts(3)] == D("10009.84995")
    assert curve[ts(4)] == D("10019.83895")  # funding -0.011, unrealised 120 - 100.1


def test_oracle_under_g9_stress() -> None:
    result = engine(
        ORACLE_BARS, funding=ORACLE_FUNDING, start=ts(1), end=ts(5), stress=G9_STRESS
    ).run(Scripted(dict(ORACLE_SCRIPT)))
    # slippage 2 ticks: entry 100.2, exit 119.8 -> gross 19.6
    # fees x2: (100.2 + 119.8) * 0.0005 * 2 = 0.22; funding paid x2: -0.022 - 0.024
    (trade,) = result.trades
    assert trade.net_pnl == D("19.334")


# --- look-ahead (M2 acceptance) ----------------------------------------------------------


def test_a_strategy_that_peeks_at_the_next_bar_is_unable_to() -> None:
    def peek(ctx: StrategyContext) -> None:
        view = ctx.views[INST]
        view.bar(len(view))  # the next bar

    with pytest.raises(IndexError, match="not in the closed history"):
        engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(Scripted(hook=peek))


def test_the_view_ends_at_the_bar_that_just_closed() -> None:
    strat = Scripted()
    engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(strat)
    for ctx in strat.seen:
        view = ctx.views[INST]
        assert view.bar(-1)[0] + 3_600_000 == ctx.ts_ms
        assert len(view.closes()) == len(view)
        assert int(view.ts_open_ms()[-1]) < ctx.ts_ms


def test_view_arrays_are_copies() -> None:
    def vandal(ctx: StrategyContext) -> None:
        ctx.views[INST].closes()[:] = 0.0

    strat = Scripted(hook=vandal)
    engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(strat)
    assert strat.seen[-1].views[INST].last_close == 120.0


def test_history_buffer_never_holds_an_unclosed_bar() -> None:
    def inspect(ctx: StrategyContext) -> None:
        buf = ctx.views[INST]._buf
        assert len(buf) == len(ctx.views[INST])
        import numpy as np

        assert np.isnan(buf._cols["close"][len(buf) :]).all()

    engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(Scripted(hook=inspect))


# --- execution timing --------------------------------------------------------------------


def test_signal_executes_at_the_next_open_not_the_signal_close() -> None:
    result = engine(ORACLE_BARS, funding=ORACLE_FUNDING, start=ts(1), end=ts(5)).run(
        Scripted(dict(ORACLE_SCRIPT))
    )
    entry = result.fills[0]
    assert entry.ts_ms == ts(2)
    assert entry.price == D("100.1")  # bar 2 open, not bar 1 close


# --- stops, targets, gaps, liquidation ---------------------------------------------------


def _one_trade(
    bars: list[tuple[str, str, str, str, str]], intent: OrderIntent, **kw: object
) -> BacktestResult:
    s = series([FLAT, FLAT, *bars])
    end = ts(len(bars) + 2)
    return engine(s, start=ts(1), end=end, **kw).run(Scripted({ts(2): [intent]}))  # type: ignore[arg-type]


def test_stop_fills_at_the_gapped_open_not_the_stop_price() -> None:
    # Opens at 92: through the 95 stop but above liquidation (90.54 at 10x).
    r = _one_trade([FLAT, ("92", "93", "91", "92", "1000")], enter("95"))
    (t,) = r.trades
    assert t.exit_reason == "stop_gap"
    assert t.avg_exit == D("91.9")  # open 92 less one tick, well below the 95 stop


def test_a_gap_through_liquidation_loses_the_margin_at_the_open() -> None:
    r = _one_trade([FLAT, ("90", "91", "89", "90", "1000")], enter("95"))
    (t,) = r.trades
    assert t.exit_reason == "liquidation_gap"
    assert t.gross_pnl == D("-10.01")


def test_intrabar_stop_fills_at_stop_less_slippage() -> None:
    r = _one_trade([FLAT, ("100", "101", "94", "96", "1000")], enter("95"))
    assert r.trades[0].avg_exit == D("94.9")
    assert r.trades[0].exit_reason == "stop"


def test_stop_and_target_in_the_same_bar_resolve_stop_first() -> None:
    r = _one_trade([FLAT, ("100", "120", "90", "100", "1000")], enter("95", "110"))
    assert r.trades[0].exit_reason == "stop"


def test_take_profit_needs_a_strict_trade_through() -> None:
    touch = _one_trade([FLAT, ("100", "110", "99", "105", "1000")], enter("95", "110"))
    assert touch.trades[0].exit_reason == "window_end"
    through = _one_trade([FLAT, ("100", "110.1", "99", "105", "1000")], enter("95", "110"))
    (t,) = through.trades
    assert t.exit_reason == "take_profit"
    assert t.avg_exit == D("110")  # exactly the target: maker, no slippage
    # fees: entry taker 100.1 * 0.0005 + exit maker 110 * 0.0002
    assert t.fees == D("0.05005") + D("0.022")


def test_take_profit_never_fills_at_a_better_gapped_price() -> None:
    r = _one_trade([FLAT, ("130", "131", "129", "130", "1000")], enter("95", "110"))
    assert r.trades[0].avg_exit == D("110")


def test_liquidation_loses_the_whole_isolated_margin() -> None:
    # entry 100.1 x 1 at 10x: margin 10.01. liq = (100.1 - 10.01) / 0.995 = 90.54...
    # The stop at 80 sits beyond liquidation, so liquidation is reached first.
    r = _one_trade([FLAT, ("100", "101", "85", "88", "1000")], enter("80"))
    (t,) = r.trades
    assert t.exit_reason == "liquidation"
    assert t.gross_pnl == D("-10.01")


def test_stop_before_liquidation_is_hit_first_on_the_way_down() -> None:
    r = _one_trade([FLAT, ("100", "101", "85", "88", "1000")], enter("95"))
    assert r.trades[0].exit_reason == "stop"


def test_short_side_mirrors() -> None:
    r = _one_trade(
        [FLAT, ("100", "106", "99", "100", "1000")], enter("105", None, Action.ENTER_SHORT)
    )
    (t,) = r.trades
    assert t.exit_reason == "stop"
    assert t.avg_entry == D("99.9")  # sell at open less a tick
    assert t.avg_exit == D("105.1")  # buy back at stop plus a tick
    assert t.gross_pnl == D("-5.2")


# --- data gaps: quarantine, never repair -------------------------------------------------


def test_pending_entry_is_cancelled_across_a_data_gap() -> None:
    s = series([FLAT, FLAT, FLAT, FLAT], index=[0, 1, 3, 4])  # hour 2 missing
    r = engine(s, start=ts(1), end=ts(5)).run(Scripted({ts(2): [enter("90")]}))
    assert r.trades == ()
    assert any(x.reason == "entry_cancelled_data_gap" for x in r.rejections)


# --- partial fills -----------------------------------------------------------------------


def test_entry_is_capped_by_participation_in_the_previous_bar() -> None:
    thin = ("100", "101", "99", "100", "2")  # 10% of 2 = 0.2 base per bar
    s = series([thin, thin, thin, thin, thin])
    r = engine(s, start=ts(1), end=ts(5), participation_cap="0.1").run(
        Scripted({ts(2): [enter("90")]})
    )
    (t,) = r.trades
    assert t.max_qty == D("0.2")
    assert any(x.reason == "entry_remainder_expired" for x in r.rejections)


def test_stop_exit_residual_leaves_at_later_opens() -> None:
    # cap = 0.25 x previous volume 2 = 0.5 base per bar, shared by all fills in the bar.
    thin = ("100", "101", "99", "100", "2")
    stop_bar = ("100", "101", "94", "96", "2")
    s = series([thin, thin, thin, thin, stop_bar, thin, thin])
    r = engine(s, start=ts(1), end=ts(7), participation_cap="0.25", ttl=3).run(
        Scripted({ts(2): [enter("95")]})
    )
    got = [((f.ts_ms - ts(0)) // 3_600_000, f.qty_delta, f.price, f.reason) for f in r.fills]
    assert got == [
        (2, D("0.5"), D("100.1"), "entry:signal"),  # first half
        (3, D("0.5"), D("100.1"), "entry:signal"),  # remainder within TTL
        (4, D("-0.5"), D("94.9"), "stop"),  # stop: only half the size is absorbable
        (5, D("-0.5"), D("99.9"), "stop_residual"),  # rest leaves at the next open
    ]
    (t,) = r.trades
    assert t.max_qty == 1
    assert t.exit_reason == "stop_residual"


# --- funding -----------------------------------------------------------------------------


def test_funding_receipt_at_an_entry_boundary_is_not_credited() -> None:
    # Negative rate: longs RECEIVE. The entry fills exactly at the ts(2) settlement, so the
    # adverse rule must not credit it; the ts(3) settlement (held across) is credited.
    fund = flat_funding(6, {2: "-0.001", 3: "-0.001"})
    r = engine(ORACLE_BARS, funding=fund, start=ts(1), end=ts(5)).run(
        Scripted({ts(2): [enter("90")]})
    )
    (t,) = r.trades
    assert t.funding == D("0.11")  # only ts(3): 1 x open 110 x 0.001


def test_funding_cost_at_an_entry_boundary_is_charged() -> None:
    fund = flat_funding(6, {2: "0.001"})
    r = engine(ORACLE_BARS, funding=fund, start=ts(1), end=ts(5)).run(
        Scripted({ts(2): [enter("90")]})
    )
    assert r.trades[0].funding == D("-0.1")  # 1 x open 100 x 0.001


def test_funding_interval_is_read_per_instrument_not_assumed() -> None:
    # 4h cadence (fact V-13): exactly two settlements inside an 8-bar hold.
    bars = series([FLAT] * 12)
    four_h = [
        __import__("okxq.backtest.types", fromlist=["FundingRate"]).FundingRate(ts(i), D("0.001"))
        for i in range(-4, 16, 4)
    ]
    r = engine(bars, funding=four_h, start=ts(1), end=ts(11)).run(Scripted({ts(2): [enter("90")]}))
    assert [e.ts_ms for e in r.funding] == [ts(4), ts(8)]


def test_refuses_to_run_without_funding_coverage() -> None:
    with pytest.raises(BacktestConfigError, match="funding covers"):
        engine(ORACLE_BARS, funding=flat_funding(6)[:3], start=ts(1), end=ts(5))
    with pytest.raises(BacktestConfigError, match="no funding series"):
        engine(ORACLE_BARS, funding=[], start=ts(1), end=ts(5))


def test_refuses_a_funding_series_with_a_hole() -> None:
    fund = [r for r in flat_funding(6) if r.ts_ms not in (ts(2), ts(3))]
    with pytest.raises(BacktestConfigError, match="funding gap"):
        engine(ORACLE_BARS, funding=fund, start=ts(1), end=ts(5))


# --- provenance --------------------------------------------------------------------------


def test_refuses_synthetic_parameters_outside_synthetic_mode() -> None:
    with pytest.raises(BacktestConfigError, match="provenance SYNTHETIC"):
        engine(ORACLE_BARS, start=ts(1), end=ts(5), synthetic=False)


def test_refuses_unmeasured_parameters_even_in_synthetic_mode() -> None:
    from dataclasses import replace

    with pytest.raises(BacktestConfigError, match="UNMEASURED"):
        engine(
            ORACLE_BARS,
            start=ts(1),
            end=ts(5),
            spec=replace(SPEC, provenance=Provenance.UNMEASURED),
        )


# --- order validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        (OrderIntent(INST, Action.ENTER_LONG), "entry_without_stop"),
        (enter("105"), "stop_on_wrong_side"),
        (enter("90", "95"), "take_profit_on_wrong_side"),
        (EXIT, "exit_without_position"),
        (OrderIntent("NOPE", Action.EXIT), "unknown_instrument"),
    ],
)
def test_invalid_intents_are_rejected(intent: OrderIntent, reason: str) -> None:
    r = engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(Scripted({ts(2): [intent]}))
    assert [x.reason for x in r.rejections] == [reason]
    assert r.fills == ()


def test_sizer_below_minimum_is_rejected() -> None:
    r = engine(ORACLE_BARS, start=ts(1), end=ts(5), sizer=FixedSizer(qty=D(0))).run(
        Scripted({ts(2): [enter("90")]})
    )
    assert r.rejections[0].reason.startswith("sizer:")


# --- cross-sectional publication (A-7) ---------------------------------------------------


def test_all_bars_closing_at_t_are_published_before_any_decision() -> None:
    a = series([FLAT] * 4, inst="A-USDT-SWAP")
    b = series([FLAT] * 4, inst="B-USDT-SWAP")
    strat = Scripted()
    engine({"A-USDT-SWAP": a, "B-USDT-SWAP": b}, start=ts(1), end=ts(4)).run(strat)
    for ctx in strat.seen:
        assert ctx.closed_now == {"A-USDT-SWAP", "B-USDT-SWAP"}
        for v in ctx.views.values():
            assert v.bar(-1)[0] + 3_600_000 == ctx.ts_ms


# --- end of series -----------------------------------------------------------------------


def test_position_is_forced_flat_when_the_series_ends() -> None:
    short_life = series([FLAT] * 4, inst="DEAD-USDT-SWAP")
    long_life = series([FLAT] * 8)
    fund = flat_funding(10)
    r = engine(
        {"DEAD-USDT-SWAP": short_life, INST: long_life},
        funding={"DEAD-USDT-SWAP": fund, INST: fund},
        start=ts(1),
        end=ts(8),
    ).run(Scripted({ts(2): [OrderIntent("DEAD-USDT-SWAP", Action.ENTER_LONG, D("90"))]}))
    (t,) = r.trades
    assert t.exit_reason == "series_end"
    assert t.exit_ts_ms == ts(4)


# --- checkpoint-3 regressions ------------------------------------------------------------


@dataclass(frozen=True)
class PerInstSizer:
    qty: dict[str, D]
    sizer_id: str = "test-per-inst"

    def size(self, *, spec: InstrumentSpec, **_: object) -> SizeDecision:
        return SizeDecision(self.qty[spec.inst_id], D(1))


@pytest.mark.parametrize("rally_name", ["A-USDT-SWAP", "Z-USDT-SWAP"])
def test_margin_check_at_t_open_cannot_see_another_instruments_bar(rally_name: str) -> None:
    """Advisor counter-example (finding 1). The rallying instrument's +50% bar must not fund
    the other instrument's entry at that bar's OPEN - whatever the instruments are called."""
    other = "M-USDT-SWAP"
    rally = series([FLAT, FLAT, FLAT, ("100", "151", "99", "150", "1000"), FLAT], inst=rally_name)
    quiet = series([FLAT] * 5, inst=other)
    sizer = PerInstSizer({rally_name: D(50), other: D(60)})  # leverage 1: margin = notional
    r = engine({rally_name: rally, other: quiet}, start=ts(1), end=ts(5), sizer=sizer).run(
        Scripted(
            {
                ts(2): [OrderIntent(rally_name, Action.ENTER_LONG, D(50))],
                ts(3): [OrderIntent(other, Action.ENTER_LONG, D(50))],
            }
        )
    )
    # At ts(3) open: equity 10000 - fees, rally margin ~5005 -> 60 x 100.1 does not fit.
    assert "insufficient_margin" in [x.reason for x in r.rejections if x.inst_id == other]
    assert not [f for f in r.fills if f.inst_id == other]


def test_mixed_timeframes_are_refused() -> None:
    from dataclasses import replace as dc_replace

    a = series([FLAT] * 4, inst="A-USDT-SWAP")
    b = dc_replace(
        series([FLAT] * 4, inst="B-USDT-SWAP"),
        timeframe_ms=2 * 3_600_000,
        ts_open_ms=tuple(ts(2 * i) for i in range(4)),
    )
    with pytest.raises(BacktestConfigError, match="one timeframe"):
        engine({"A-USDT-SWAP": a, "B-USDT-SWAP": b}, start=ts(1), end=ts(4))


def test_no_reentry_in_the_same_decision_as_an_exit() -> None:
    r = engine(ORACLE_BARS, start=ts(1), end=ts(5)).run(
        Scripted({ts(2): [enter("90")], ts(3): [EXIT, enter("95")]})
    )
    assert "already_positioned" in [x.reason for x in r.rejections]
    assert len(r.trades) == 1


def _slip_engine(bars: BarSeries, model: SlippageModel, start: int) -> BacktestEngine:
    return BacktestEngine(
        series={INST: bars},
        specs={INST: SPEC},
        funding={INST: flat_funding(12)},
        config=EngineConfig(
            fees=FEES,
            slippage=model,
            initial_equity=D(10_000),
            participation_cap=D(1),
            synthetic=True,
        ),
        sizer=FixedSizer(),
        trade_start_ms=start,
        end_ms=ts(len(bars)),
    )


def test_slippage_impact_term_by_hand() -> None:
    """slip-v2 impact (finding 7). Entry at bar 3's open = 100, vol lookback 2.
    Closes before it: 100, 110, 100 -> sample sd of log returns = ln(1.1) * sqrt(2).
    participation = 1 / previous bar's volume 100 -> sqrt = 0.1.
    slip = max(tick, 0) + 100 * 1 * sd * 0.1 = 0.1 + 1.3479 -> rounded UP to 15 ticks."""
    bars = series(
        [
            ("100", "101", "99", "100", "100"),
            ("100", "111", "99", "110", "100"),
            ("110", "111", "99", "100", "100"),
            ("100", "101", "99", "100", "100"),
            FLAT,
        ]
    )
    raw = 0.1 + 100 * math.log(1.1) * math.sqrt(2) * 0.1
    expected = D(math.ceil(raw / 0.1)) * D("0.1")
    assert expected == D("1.5")
    r = _slip_engine(bars, SlippageModel(D(1), 2, 0, "t"), ts(2)).run(
        Scripted({ts(3): [enter("90")]})
    )
    assert r.fills[0].price == D(100) + expected
    assert r.fills[0].slippage_cost == expected


def test_slippage_spread_term_from_past_bars_only() -> None:
    """slip-v2 spread term: half the Abdi-Ranaldo spread of the 4 bars BEFORE the fill,
    re-derived here from the paper's formula (not by calling the module under test)."""
    rows = [
        ("100", "103", "97", "102", "1000"),
        ("102", "104", "98", "99", "1000"),
        ("99", "103", "96", "102", "1000"),
        ("102", "105", "99", "100", "1000"),
        ("100", "101", "99", "100", "1000"),  # the fill bar: its own H/L must not matter
        FLAT,
    ]
    h = [float(r[1]) for r in rows[:4]]
    lo = [float(r[2]) for r in rows[:4]]
    c = [float(r[3]) for r in rows[:4]]
    eta = [(math.log(a) + math.log(b)) / 2 for a, b in zip(h, lo, strict=True)]
    terms = [4 * (math.log(c[t]) - eta[t]) * (math.log(c[t]) - eta[t + 1]) for t in range(3)]
    rel = math.sqrt(max(0.0, sum(terms) / 3))
    half = 100 * rel / 2
    assert half > 0.1  # the estimate, not the tick floor, must be what binds here
    expected = D(math.ceil(half / 0.1)) * D("0.1")
    r = _slip_engine(series(rows), SlippageModel(D(0), 2, 4, "t"), ts(3)).run(
        Scripted({ts(4): [enter("90")]})
    )
    assert r.fills[0].slippage_cost == expected
