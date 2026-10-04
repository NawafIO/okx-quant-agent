"""The M5 risk limits inside the backtest engine (cycle 2, D3; okxq.backtest.riskgate)."""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from decimal import Decimal

import pytest

from okxq.analysis.regime import classify
from okxq.analysis.ta import Bars
from okxq.backtest import riskgate as rg
from okxq.backtest.engine import BacktestEngine, BacktestResult, EngineConfig
from okxq.backtest.riskgate import (
    APPLIED,
    NOT_APPLICABLE,
    SIZING_CHECKS,
    DailyLabel,
    GateFacts,
    GateMode,
    ResearchRiskGate,
)
from okxq.backtest.types import (
    Action,
    BacktestConfigError,
    BarSeries,
    FundingRate,
    OrderIntent,
)
from okxq.risk.policy import REQUIRED_CHECKS

from .conftest import FEES, FLOOR_SLIPPAGE, SPEC, FixedSizer, Scripted, flat_funding, series, ts

D = Decimal
DAY = 86_400_000
BTC, ETH, SOL, XRP = "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"
N = 400
WARM = 300  # >= 257 bars before the first decision: SZ-2's fixed-buffer ATR exists
Row = tuple[str, str, str, str, str]
FLAT: Row = ("1000", "1005", "995", "1000", "1000")  # true range 10 -> ATR 10


def rows(n: int = N, events: dict[int, Row] | None = None) -> list[Row]:
    out = [FLAT] * n
    for i, r in (events or {}).items():
        out[i] = r
    return out


def labels(regime: str = "RANGE") -> list[DailyLabel]:
    first = (ts(-10) // DAY - 2) * DAY
    return [DailyLabel(first + k * DAY, regime) for k in range(40)]


FACTS = GateFacts(SPEC.tick_size, SPEC.lot_size_base, SPEC.min_size_base, D(1_000_000), SPEC.mmr)


def gate(
    insts: list[str],
    mode: GateMode = GateMode.ENFORCE,
    regime: str = "RANGE",
    start: int = ts(WARM),
) -> ResearchRiskGate:
    return ResearchRiskGate(
        initial_equity=D(10_000),
        start_ms=start,
        facts={k: FACTS for k in insts},
        labels={k: labels(regime) for k in insts},
        spec_sha="test",
        mode=mode,
    )


def build(
    bars: dict[str, BarSeries],
    g: ResearchRiskGate | None,
    *,
    funding: dict[str, list[FundingRate]] | None = None,
    ttl: int = 1,
    cap: str = "1",
) -> BacktestEngine:
    return BacktestEngine(
        series=bars,
        specs={k: replace(SPEC, inst_id=k) for k in bars},
        funding=funding or {k: flat_funding(N + 5) for k in bars},
        config=EngineConfig(
            fees=FEES,
            slippage=FLOOR_SLIPPAGE,
            initial_equity=D(10_000),
            participation_cap=D(cap),
            entry_ttl_bars=ttl,
            synthetic=True,
        ),
        sizer=FixedSizer(),
        trade_start_ms=ts(WARM),
        end_ms=ts(N),
        risk_gate=g,
    )


def long(inst: str, ref: str = "1000", stop: str = "980", tp: str = "1100") -> OrderIntent:
    return OrderIntent(inst, Action.ENTER_LONG, D(stop), D(tp))


def refused(r: BacktestResult) -> list[tuple[int, str, str]]:
    return [(x.ts_ms, x.inst_id, x.reason) for x in r.rejections]


# --- accounting --------------------------------------------------------------------------


def test_every_required_check_is_applied_sized_or_named_not_applicable() -> None:
    a, n, s = set(APPLIED), set(NOT_APPLICABLE), set(SIZING_CHECKS)
    assert a | n | s == set(REQUIRED_CHECKS)
    assert not (a & n) and not (a & s) and not (n & s)
    assert n == {"RC-02", "RC-03", "RC-04", "RC-15"}
    assert all(NOT_APPLICABLE[c] for c in n)  # each carries its reason


def test_the_gate_needs_single_fill_entries() -> None:
    with pytest.raises(BacktestConfigError, match="entry_ttl_bars"):
        build({BTC: series(rows(), inst=BTC)}, gate([BTC]), ttl=2)


def test_without_a_gate_nothing_about_the_engine_changes() -> None:
    r = build({BTC: series(rows(), inst=BTC)}, None).run(Scripted({ts(WARM + 1): [long(BTC)]}))
    assert "risk_gate" not in r.assumptions
    assert r.fills[0].qty_delta == D(1)  # the injected sizer, not M5


# --- sizing and the round trip -----------------------------------------------------------


def test_m5_sizes_the_entry_and_the_portfolio_matches_the_engine_cash_exactly() -> None:
    ev = {WARM + 4: ("1000", "1001", "975", "990", "1000")}  # stop 980 crossed
    g = gate([BTC])
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g).run(
        Scripted({ts(WARM + 2): [long(BTC)]})
    )
    entry = r.fills[0]
    # risk 0.5% of 10,000 = 50 over a 20 stop distance -> 2.5, floored to the 0.001 lot
    assert entry.qty_delta == D("2.5")
    assert [t.exit_reason for t in r.trades] == ["stop"]
    cash = D(10_000) + sum((t.net_pnl for t in r.trades), D(0))
    assert g.state.positions == () and g.state.realised == cash
    assert g.state.consecutive_losses == 1
    assert r.assumptions["risk_gate"].startswith("mode=enforce")


def test_an_entry_without_a_take_profit_is_refused() -> None:
    no_tp = OrderIntent(BTC, Action.ENTER_LONG, D(980))
    r = build({BTC: series(rows(), inst=BTC)}, gate([BTC])).run(Scripted({ts(WARM + 2): [no_tp]}))
    assert refused(r) == [(ts(WARM + 2), BTC, "risk:no_take_profit")]


def test_too_short_a_history_fails_sz2() -> None:
    g = gate([BTC], start=ts(100))
    eng = BacktestEngine(
        series={BTC: series(rows(), inst=BTC)},
        specs={BTC: replace(SPEC, inst_id=BTC)},
        funding={BTC: flat_funding(N + 5)},
        config=EngineConfig(FEES, FLOOR_SLIPPAGE, D(10_000), synthetic=True),
        sizer=FixedSizer(),
        trade_start_ms=ts(100),
        end_ms=ts(N),
        risk_gate=g,
    )
    r = eng.run(Scripted({ts(102): [long(BTC)]}))
    (when, inst, why), *rest = refused(r)
    # M5 sizes nothing once SZ-2 fails, so every size-dependent check fails with it.
    assert (when, inst) == (ts(102), BTC) and why.startswith("risk:SZ-2,") and not rest


def test_no_regime_label_is_refused_by_rc16_and_counted_by_year() -> None:
    g = gate([BTC], regime="UNDEFINED")
    r = build({BTC: series(rows(), inst=BTC)}, g).run(Scripted({ts(WARM + 2): [long(BTC)]}))
    assert refused(r) == [(ts(WARM + 2), BTC, "risk:RC-16")]
    assert sum(g.report().no_label_by_year.values()) == 1


def test_crisis_blocks_entries() -> None:
    r = build({BTC: series(rows(), inst=BTC)}, gate([BTC], regime="CRISIS")).run(
        Scripted({ts(WARM + 2): [long(BTC)]})
    )
    assert refused(r) == [(ts(WARM + 2), BTC, "risk:RC-16")]


# --- capacity at one decision time -------------------------------------------------------


def test_the_cluster_cap_admits_three_in_hash_order_not_alphabetical() -> None:
    insts = [BTC, ETH, SOL, XRP]
    bars = {k: series(rows(), inst=k) for k in insts}

    def last(t: int) -> str:
        return max(insts, key=lambda k: hashlib.sha256(f"{t}|{k}".encode()).hexdigest())

    # A decision time at which the hash order and the alphabetical order disagree on who
    # is left out - otherwise this test could not tell them apart (mutation-checked).
    t = next(ts(i) for i in range(WARM + 2, WARM + 60) if last(ts(i)) != XRP)
    r = build(bars, gate(insts)).run(Scripted({t: [long(k) for k in insts]}))
    filled = {f.inst_id for f in r.fills if f.qty_delta > 0}
    ranked = sorted(insts, key=lambda k: hashlib.sha256(f"{t}|{k}".encode()).hexdigest())
    assert filled == set(ranked[:3])
    assert [x[1] for x in refused(r)] == [ranked[3]]
    assert "RC-07" in refused(r)[0][2]


# --- RC-13 -------------------------------------------------------------------------------


def stopped_out_three_times() -> tuple[dict[int, Row], dict[int, list[OrderIntent]]]:
    ev: dict[int, Row] = {}
    script: dict[int, list[OrderIntent]] = {}
    for k in range(3):
        d = WARM + 2 + 5 * k
        script[ts(d)] = [long(BTC)]
        ev[d + 2] = ("1000", "1001", "975", "990", "1000")
    script[ts(WARM + 20)] = [long(BTC)]
    return ev, script


@pytest.mark.parametrize("mode", [GateMode.ENFORCE, GateMode.OBSERVE_HALTS])
def test_three_losses_in_a_row_start_the_cooldown_in_both_modes(mode: GateMode) -> None:
    ev, script = stopped_out_three_times()
    g = gate([BTC], mode=mode)
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g).run(Scripted(script))
    assert len(r.trades) == 3 and g.state.consecutive_losses == 3
    assert refused(r) == [(ts(WARM + 20), BTC, "risk:RC-13")]


# --- halts -------------------------------------------------------------------------------


def gap_loss(to: str) -> tuple[dict[int, Row], dict[int, list[OrderIntent]]]:
    """One entry, then a gap through the stop to ``to``; later entries probe the latch."""
    ev: dict[int, Row] = {WARM + 4: (to, to, to, to, "1000")}
    for i in range(WARM + 5, N):
        ev[i] = (to, str(D(to) + 5), str(D(to) - 5), to, "1000")
    script = {
        ts(WARM + 2): [long(BTC)],
        ts(WARM + 10): [long(BTC, stop=str(D(to) - 20), tp=str(D(to) + 100))],
        ts(WARM + 60): [long(BTC, stop=str(D(to) - 20), tp=str(D(to) + 100))],
    }
    return ev, script


def test_a_drawdown_halt_latches_permanently_under_enforce() -> None:
    ev, script = gap_loss("590")  # 2.5 x 410 = 1,025 = 10.25% of 10,000
    g = gate([BTC])
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g).run(Scripted(script))
    reasons = [x[2] for x in refused(r)]
    assert len(reasons) == 2 and all("RC-01" in x and "RC-11" in x for x in reasons)
    rep = g.report()
    assert any(t.startswith("RC-11") for _, t in rep.engagements)


def test_a_day_loss_halt_resumes_at_the_first_midnight_24h_later() -> None:
    ev, script = gap_loss("900")  # 2.5 x 100 = 250 = 2.5%: RC-10 only
    g = gate([BTC])
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g).run(Scripted(script))
    halted_at = next(t for t, trig in g.report().engagements if trig.startswith("RC-10"))
    resume = -(-(halted_at + DAY) // DAY) * DAY
    blocked = [x for x in refused(r) if x[0] == ts(WARM + 10)]
    assert blocked and "RC-01" in blocked[0][2]
    assert ts(WARM + 60) >= resume
    assert not [x for x in refused(r) if x[0] == ts(WARM + 60)]  # trading resumed


def permanent(g: ResearchRiskGate) -> bool:
    return g._latch.permanent


def test_a_permanent_halt_escalates_a_temporary_latch_already_in_force() -> None:
    """RC-10 latches first (temporary); RC-11 in the same evaluation must still make it
    permanent, even if the drawdown later recovers inside the temporary window."""
    from okxq.risk.cycle import Halt

    g = gate([BTC])
    t = ts(WARM + 5)
    g._halt((Halt("RC-10 daily loss", D("0.05"), D("0.02")),), t)
    assert not permanent(g) and g._latch.engaged()
    g._halt((Halt("RC-11 drawdown", D("0.11"), D("0.10")),), t + 1)
    assert permanent(g)
    g._latch.now_ms = t + 30 * DAY
    assert g._latch.engaged()
    assert [trig for _, trig in g.report().engagements] == ["RC-10 daily loss", "RC-11 drawdown"]


def test_observe_halts_records_but_never_blocks_on_a_halt() -> None:
    ev, script = gap_loss("590")
    g = gate([BTC], mode=GateMode.OBSERVE_HALTS)
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g).run(Scripted(script))
    assert not any("RC-01" in x[2] or "RC-11" in x[2] for x in refused(r))
    rep = g.report()
    assert rep.mode == "observe_halts" and rep.halts.get("RC-11 drawdown", 0) > 0
    assert rep.failures.get("RC-11", 0) > 0  # recorded


def test_halts_are_evaluated_after_every_applied_event(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"apply": 0, "halts": 0}
    from okxq.risk.cycle import halt_triggers as real_halts
    from okxq.risk.portfolio import apply as real_apply

    def counted_apply(*a: object) -> object:
        calls["apply"] += 1
        return real_apply(*a)  # type: ignore[arg-type]

    def counted_halts(*a: object) -> object:
        assert calls["halts"] == calls["apply"] - 1  # strictly after its apply
        calls["halts"] += 1
        return real_halts(*a)  # type: ignore[arg-type]

    monkeypatch.setattr(rg, "apply", counted_apply)
    monkeypatch.setattr(rg, "halt_triggers", counted_halts)
    ev, script = stopped_out_three_times()
    build({BTC: series(rows(events=ev), inst=BTC)}, gate([BTC])).run(Scripted(script))
    assert calls["apply"] > 100 and calls["halts"] == calls["apply"]


# --- funding attributed to a lifecycle closing in the same instant -----------------------


def test_funding_charged_at_the_exit_open_travels_with_the_close() -> None:
    """An exit fills at T_open and a settlement at T_open is charged to the position held
    BEFORE the open (adverse rule). The portfolio no longer holds it; the flow must still
    reach realised P&L exactly."""
    exit_at = WARM + 6
    fund = {BTC: flat_funding(N + 5, {exit_at: "0.001", WARM + 4: "0.0005"})}
    script = {ts(WARM + 2): [long(BTC)], ts(exit_at): [OrderIntent(BTC, Action.EXIT)]}
    g = gate([BTC])
    r = build({BTC: series(rows(), inst=BTC)}, g, funding=fund).run(Scripted(script))
    assert len(r.trades) == 1 and r.trades[0].funding < 0
    cash = D(10_000) + r.trades[0].net_pnl
    assert g.state.realised == cash


def test_a_partial_stop_exit_keeps_the_full_position_until_it_is_closed() -> None:
    # cap 0.003 x volume 1000 = 3.0 admits the 2.5 entry; the bar before the stop trades
    # only 500, so the stop can exit 1.5 and the 1.0 residual leaves at the next open.
    ev = {
        WARM + 3: ("1000", "1005", "995", "1000", "500"),
        WARM + 4: ("1000", "1001", "975", "990", "1000"),
    }
    g = gate([BTC])
    r = build({BTC: series(rows(events=ev), inst=BTC)}, g, cap="0.003").run(
        Scripted({ts(WARM + 2): [long(BTC)]})
    )
    exits = [f for f in r.fills if f.qty_delta < 0]
    assert len(exits) >= 2  # the cap split the stop exit
    cash = D(10_000) + sum((t.net_pnl for t in r.trades), D(0))
    assert g.state.positions == () and g.state.realised == cash


# --- the regime labels the caller feeds the gate are causal ------------------------------


def test_a_daily_label_never_changes_when_later_days_are_appended() -> None:
    rng = random.Random(5)  # noqa: S311 - synthetic bars
    c, px = [], 100.0
    for _ in range(400):
        px *= 1 + rng.gauss(0, 0.03)
        c.append(px)
    h = [x * 1.01 for x in c]
    lo = [x * 0.99 for x in c]
    v = [1000.0] * len(c)
    full = classify(Bars.of(c, h, lo, c, v))
    for n in (60, 150, 300):
        assert classify(Bars.of(c[:n], h[:n], lo[:n], c[:n], v[:n])) == full[:n]


# --- review fixes (advisor, code review of bd3342d) --------------------------------------


def test_a_backwards_event_other_than_gap_funding_raises() -> None:
    from okxq.risk.portfolio import PortfolioError

    g = gate([BTC])
    g.marks(ts(WARM + 5), {BTC: D(1000)})
    with pytest.raises(PortfolioError, match="precedes"):
        g.marks(ts(WARM + 4), {BTC: D(1000)})


def test_same_instant_funding_is_booked_as_funding_and_never_flips_rc13() -> None:
    """Gross +5 beats both fees (2) but not the 10 funding paid in the closing instant: M5
    judges win/loss without funding, so this is a WIN for RC-13; realised still drops."""
    g = gate([BTC])
    from okxq.backtest.types import Side

    g.opened(ts(WARM + 1), BTC, Side.LONG, D(1), D(1000), D(980), D(1), D(1))
    g.closed(ts(WARM + 2), BTC, gross=D(5), total_fees=D(2), total_funding=D(-10))
    assert g.state.consecutive_losses == 0
    assert g.state.realised == D(10_000) + D(5) - D(2) - D(10)


def test_halts_count_bars_not_events() -> None:
    ev, script = gap_loss("590")
    bars = {BTC: series(rows(events=ev), inst=BTC), ETH: series(rows(), inst=ETH)}
    g = gate([BTC, ETH], mode=GateMode.OBSERVE_HALTS)
    build(bars, g).run(Scripted(script))
    n = g.report().halts["RC-11 drawdown"]
    # At most one count per bar open and one per bar close after the gap.
    assert 0 < n <= 2 * (N - (WARM + 4))


def test_the_gate_holds_exactly_what_the_engine_holds_at_every_decision() -> None:
    insts = [BTC, ETH, SOL, XRP]
    ev, script = stopped_out_three_times()
    bars = {k: series(rows(events=ev if k == BTC else None), inst=k) for k in insts}
    for i in (WARM + 30, WARM + 31, WARM + 40):
        script.setdefault(ts(i), []).extend(long(k) for k in insts)
    script.setdefault(ts(WARM + 35), []).append(OrderIntent(ETH, Action.EXIT))
    g = gate(insts)
    mismatches: list[int] = []

    def check(ctx: object) -> None:
        eng = {k: str(p.side) for k, p in ctx.positions.items()}  # type: ignore[attr-defined]
        held = {h.symbol: h.side for h in g.state.positions}
        if eng != held:
            mismatches.append(ctx.ts_ms)  # type: ignore[attr-defined]

    r = build(bars, g).run(Scripted(script, hook=check))
    assert len(r.trades) >= 5 and mismatches == []
