"""Every check: one passing and one failing fixture with exact observed/limit values
(roadmap M5), plus the default-deny branches. Each failing case changes ONE input."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from okxq.contracts import TradeProposal
from okxq.risk import checks as ch
from okxq.risk.checks import Ctx, open_risk
from okxq.risk.engine import evaluate
from okxq.risk.policy import FROZEN_RISK_POLICY as P
from okxq.risk.qualitative import Gate
from okxq.risk.sizing import Sized

from .builders import SYM, T_MS, facts, position, qual, regime, sentiment, signal, snap

D = Decimal

H = 3_600_000


def run(**over: Any) -> TradeProposal:
    return evaluate(
        over.pop("signal", signal()),
        over.pop("snap", snap()),
        over.pop("facts", facts()),
        over.pop("qual", qual()),
    )


def check(p: TradeProposal, cid: str) -> tuple[bool, D | None, D | None, str]:
    r = next(c for c in p.risk_checks if c.check_id == cid)
    return r.passed, r.observed, r.limit, r.detail


def failed(p: TradeProposal) -> list[str]:
    return [c.check_id for c in p.risk_checks if not c.passed]


def test_the_default_fixture_is_approved_with_hand_computed_values() -> None:
    p = run()
    assert p.verdict == "APPROVED"
    assert (p.qty_base, p.notional_quote, p.risk_amount) == (D(25), D(2500), D(50))
    assert (p.risk_pct_of_equity, p.leverage, p.liquidation_estimate) == (
        D("0.005"),
        D(1),
        D("0.4"),
    )
    assert [c.check_id for c in p.risk_checks] == list(P.required_checks)


# --- sizing ------------------------------------------------------------------------------


def test_sz2_stop_tighter_than_half_an_atr() -> None:
    p = run(facts=facts(atr_1h=D(5)))
    assert check(p, "SZ-2")[:3] == (False, D(2), D("2.5"))
    assert p.verdict == "REJECTED" and p.qty_base == 0


def test_sz2_missing_atr() -> None:
    assert check(run(facts=facts(atr_1h=None)), "SZ-2")[:3] == (False, D(2), None)


def test_sz3_stop_tighter_than_ten_ticks() -> None:
    assert check(run(facts=facts(tick_size=D(1))), "SZ-3")[:3] == (False, D(2), D(10))
    assert check(run(facts=facts(tick_size=None)), "SZ-3")[0] is False


def test_sz4_a_budget_below_one_lot_rejects_never_inflates() -> None:
    p = run(snap=snap(equity=D("0.0001"), high_water_mark=D("0.0001"), day_open_equity=D("0.0001")))
    assert check(p, "SZ-4")[0] is False and p.verdict == "REJECTED" and p.qty_base == 0
    assert check(run(facts=facts(lot_size_base=None)), "SZ-4")[0] is False


def test_sz1_wrong_side_stop_rejects_even_if_the_contract_were_bypassed() -> None:
    bad = signal().model_construct(**(signal().model_dump() | {"stop_loss": D(101)}))
    p = run(signal=bad)
    assert check(p, "SZ-1")[:3] == (False, D(-1), D(0)) and p.verdict == "REJECTED"


# --- RC-01 .. RC-04 ----------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, None, 0, "False"])
def test_rc01_anything_but_false_rejects(value: object) -> None:
    assert failed(run(snap=snap(kill_switch_engaged=value))) == ["RC-01"]


def test_rc02_env_mismatch_symbol_mismatch_and_live_phase() -> None:
    assert "RC-02" in failed(run(facts=facts(env="DEMO")))
    assert "RC-02" in failed(run(facts=facts(symbol="ETH-USDT-SWAP")))
    live = run(
        signal=signal(env="LIVE"),
        snap=snap(env="LIVE"),
        facts=facts(env="LIVE"),
        qual=qual(regime=regime(env="LIVE")),
    )
    assert "RC-02" in failed(live)  # PHASE 2: LIVE is locked


def test_rc03_freshness_at_the_budget_edge() -> None:
    budget = D(H + 120_000)
    at = run(facts=facts(last_bar_close_ts_ms=T_MS - int(budget)))
    assert check(at, "RC-03")[:3] == (True, budget, budget)
    over = run(facts=facts(last_bar_close_ts_ms=T_MS - int(budget) - 1))
    assert check(over, "RC-03")[:3] == (False, budget + 1, budget)
    assert check(run(facts=facts(last_bar_close_ts_ms=T_MS + 1)), "RC-03")[0] is False
    assert check(run(facts=facts(last_bar_close_ts_ms=None)), "RC-03")[0] is False
    assert check(run(facts=facts(timeframe_ms=0)), "RC-03")[0] is False


@pytest.mark.parametrize("value", [False, None])
def test_rc04_untradable_or_unknown(value: object) -> None:
    assert failed(run(facts=facts(tradable=value))) == ["RC-04"]


# --- RC-05 .. RC-09 ----------------------------------------------------------------------


def _ctx(sized: Sized | None, **over: Any) -> Ctx:
    return Ctx(
        signal(), over.get("snap", snap()), over.get("facts", facts()), Gate(D(1), ""), sized, P
    )


SIZED = Sized(D(100), D(2), D(50), D(25), D(50), D(2500), D(1), D("0.4"))


def test_rc05_pass_and_fail_by_injection() -> None:
    assert (ch.rc05(_ctx(SIZED)).passed, ch.rc05(_ctx(SIZED)).observed) == (True, D("0.005"))
    r = ch.rc05(_ctx(replace(SIZED, actual_risk=D("50.01"))))
    assert (r.passed, r.observed, r.limit) == (False, D("0.005001"), D("0.005"))
    assert ch.rc05(_ctx(None)).passed is False


def test_rc06_portfolio_heat() -> None:
    hot = snap(positions=(position(qty_base=D(30)),))  # 30 x (100-90) = 300 open risk
    assert check(run(snap=hot), "RC-06")[:3] == (False, D("0.035"), D("0.03"))
    ok = snap(positions=(position(qty_base=D(10)),))
    assert check(run(snap=ok), "RC-06")[:3] == (True, D("0.015"), D("0.03"))


def test_rc07_cluster_cap_counts_only_the_same_cluster() -> None:
    crypto = snap(positions=(position(qty_base=D(15)),))  # 150 in CRYPTO
    assert check(run(snap=crypto), "RC-07")[:3] == (False, D("0.02"), D("0.015"))
    metal = snap(positions=(position(symbol="XAU-USDT-SWAP", qty_base=D(15)),))
    assert check(run(snap=metal), "RC-07")[:3] == (True, D("0.005"), D("0.015"))


def test_rc07_unmapped_symbol_rejects() -> None:
    p = run(
        signal=signal(symbol="NEW-USDT-SWAP"),
        facts=facts(symbol="NEW-USDT-SWAP"),
        qual=qual(regime=regime(symbol="NEW-USDT-SWAP")),
    )
    assert check(p, "RC-07")[0] is False


def test_open_risk_default_deny_and_crossed_stops() -> None:
    assert open_risk("garbage", P) is None
    assert open_risk(("x",), P) is None
    assert open_risk((position(side="FLAT"),), P) is None
    assert open_risk((position(qty_base=D("NaN")),), P) is None
    assert open_risk((position(symbol="NEW-USDT-SWAP"),), P) is None
    assert open_risk((position(mark=D(80)),), P) is None  # stop crossed: incident (#6)
    assert open_risk((position(mark=D(90)),), P) == 0  # exactly at the stop
    short = position(side="SHORT", stop=D(110), mark=D(100), qty_base=D(2))
    assert open_risk((short,), P) == D(20)


def test_rc08_max_positions() -> None:
    five = tuple(
        position(symbol=s, qty_base=D("0.001"))
        for s in (
            "ETH-USDT-SWAP",
            "SOL-USDT-SWAP",
            "XRP-USDT-SWAP",
            "DOGE-USDT-SWAP",
            "UNI-USDT-SWAP",
        )
    )
    assert check(run(snap=snap(positions=five)), "RC-08")[:3] == (False, D(6), D(5))
    assert check(run(snap=snap(positions=five[:4])), "RC-08")[:3] == (True, D(5), D(5))
    assert check(run(snap=snap(positions="x")), "RC-08")[0] is False


def test_rc09_one_per_symbol_and_no_duplicate_signal() -> None:
    assert "RC-09" in failed(run(snap=snap(positions=(position(symbol=SYM),))))
    assert failed(run(snap=snap(signals_this_bar=(SYM,)))) == ["RC-09"]
    assert check(run(snap=snap(signals_this_bar="x")), "RC-09")[0] is False


# --- RC-10 / RC-11: exact thresholds --------------------------------------------------------


def test_rc10_halts_at_exactly_the_limit() -> None:
    at = snap(equity=D(9800), high_water_mark=D(10_000))
    assert check(run(snap=at), "RC-10")[:3] == (False, D("0.02"), D("0.02"))
    just = snap(equity=D("9800.0001"))
    assert check(run(snap=just), "RC-10")[0] is True
    assert check(run(snap=snap(day_open_equity=None)), "RC-10")[0] is False
    assert check(run(snap=snap(equity=D("NaN"))), "RC-10")[0] is False


def test_rc11_halts_at_exactly_the_limit() -> None:
    at = snap(equity=D(9000), day_open_equity=D(9000))
    assert check(run(snap=at), "RC-11")[:3] == (False, D("0.1"), D("0.10"))
    just = snap(equity=D("9000.01"), day_open_equity=D("9000.01"))
    assert check(run(snap=just), "RC-11")[0] is True
    assert check(run(snap=snap(high_water_mark=None)), "RC-11")[0] is False
    assert check(run(snap=snap(high_water_mark=D(9000))), "RC-11")[0] is False  # stale HWM


# --- RC-12 ---------------------------------------------------------------------------------


def test_rc12a_leverage_cap_by_injection() -> None:
    assert ch.rc12a(_ctx(SIZED)).passed is True
    assert ch.rc12a(_ctx(replace(SIZED, leverage=D(4)))).passed is False
    assert ch.rc12a(_ctx(None)).passed is False


def test_rc12bc_tight_stop_cannot_manufacture_a_huge_position() -> None:
    p = run(signal=signal(stop_loss=D("99.9")), facts=facts(atr_1h=D("0.1"), tick_size=D("0.01")))
    assert check(p, "RC-12b")[:3] == (False, D(50_000), D(30_000))
    assert check(p, "RC-12c")[0] is False and p.verdict == "REJECTED"


def test_rc12c_free_margin_and_rc12b_equity_missing() -> None:
    assert check(run(snap=snap(free_margin=D(0))), "RC-12c")[0] is False
    assert check(run(snap=snap(free_margin=None)), "RC-12c")[0] is False
    assert ch.rc12b(_ctx(SIZED, snap=snap(equity=D(0)))).passed is False


def test_rc12d_min_size_and_lot_step() -> None:
    assert check(run(facts=facts(min_size_base=D(100))), "RC-12d")[:3] == (False, D(25), D(100))
    off_step = replace(SIZED, qty=D("25.00005"))
    assert ch.rc12d(_ctx(off_step)).passed is False
    assert check(run(facts=facts(min_size_base=None)), "RC-12d")[0] is False


def test_rc12e_unmeasured_max_size_rejects() -> None:
    assert failed(run(facts=facts(max_size_base=None))) == ["RC-12e"]
    assert check(run(facts=facts(max_size_base=D(10))), "RC-12e")[:3] == (False, D(25), D(10))


def test_rc12f_liquidation_must_sit_beyond_the_stop() -> None:
    wide = run(
        signal=signal(stop_loss=D(70)), facts=facts(atr_1h=D(1)), snap=snap(free_margin=D(100))
    )
    ok, gap, need, _ = check(wide, "RC-12f")
    assert not ok and need == D(45) and gap is not None and gap < need
    assert "RC-12f" in failed(run(facts=facts(mmr=D("NaN"))))
    wrong_side = replace(SIZED, liquidation=D(101))
    assert ch.rc12f(_ctx(wrong_side)).passed is False


def test_short_side_sizing_and_liquidation() -> None:
    p = run(signal=signal(side="SHORT"))
    assert p.verdict == "APPROVED" and p.qty_base == 25
    assert p.liquidation_estimate is not None and p.liquidation_estimate > 102


# --- RC-13 .. RC-16 ------------------------------------------------------------------------


def test_rc13_consecutive_loss_cooldown() -> None:
    hot = snap(consecutive_losses=3, last_loss_ts_ms=T_MS - H)
    assert check(run(snap=hot), "RC-13")[:3] == (False, D(3), D(3))
    cooled = snap(consecutive_losses=3, last_loss_ts_ms=T_MS - 25 * H)
    assert check(run(snap=cooled), "RC-13")[0] is True
    assert check(run(snap=snap(consecutive_losses=3)), "RC-13")[0] is False  # no last-loss time
    assert check(run(snap=snap(consecutive_losses=-1)), "RC-13")[0] is False


def test_rc14_entries_per_hour() -> None:
    assert check(run(snap=snap(entries_last_hour=3)), "RC-14")[:3] == (False, D(3), D(3))
    assert check(run(snap=snap(entries_last_hour=2)), "RC-14")[:3] == (True, D(2), D(3))
    assert check(run(snap=snap(entries_last_hour=True)), "RC-14")[0] is False


@pytest.mark.parametrize("value", [True, None])
def test_rc15_breach_or_unknown_rejects(value: object) -> None:
    assert failed(run(snap=snap(api_error_breach=value))) == ["RC-15"]


@pytest.mark.parametrize(
    "q",
    [
        qual(regime=None),
        qual(regime=regime(symbol="ETH-USDT-SWAP")),
        qual(regime=regime(env="DEMO")),
        qual(regime=regime(ts=regime().ts.replace(year=2025))),  # stale
        qual(regime=regime(ts=regime().ts.replace(year=2027))),  # from the future
        qual(regime=regime(regime="CRISIS")),
        qual(sentiment=sentiment(score=D("-0.61"), confidence=D("0.71"))),  # veto LONG
        qual(sentiment=sentiment(symbol="ETH-USDT-SWAP")),
    ],
    ids=["none", "symbol", "env", "stale", "future", "crisis", "veto", "sent-symbol"],
)
def test_rc16_blocks(q: Any) -> None:
    assert failed(run(qual=q)) == ["RC-16"]


def test_rc16_sentiment_thresholds_are_strict_and_side_specific() -> None:
    at = qual(sentiment=sentiment(score=D("-0.6"), confidence=D("0.71")))
    assert run(qual=at).verdict == "APPROVED"
    shy = qual(sentiment=sentiment(score=D("-0.9"), confidence=D("0.7")))
    assert run(qual=shy).verdict == "APPROVED"
    bull = qual(sentiment=sentiment(score=D("0.9"), confidence=D("0.9")))
    assert run(qual=bull).verdict == "APPROVED"  # a bullish score never vetoes a LONG
    assert failed(run(signal=signal(side="SHORT"), qual=bull)) == ["RC-16"]
    assert run(signal=signal(side="SHORT"), qual=at).verdict == "APPROVED"


def test_llm_disagreement_halves_the_size_and_never_enlarges() -> None:
    p = run(qual=qual(regime=regime(determined_by="RULE_LLM_DISAGREE")))
    assert p.verdict == "APPROVED" and p.qty_base == D("12.5") and p.risk_amount == D(25)


def test_quantity_is_floored_to_the_lot_on_an_inexact_division() -> None:
    """50 / 3 = 16.666...; floored to the 0.0001 lot is 16.6666. Rounding up (16.6667)
    would breach the 50 budget and REJECT - this pins the floor itself, not just the guard."""
    p = run(signal=signal(stop_loss=D(97)))
    assert p.verdict == "APPROVED"
    assert p.qty_base == D("16.6666")
    assert p.risk_amount == D("49.9998") <= D(50)


def test_a_held_position_beyond_its_stop_fails_heat_and_cluster_checks() -> None:
    crossed = snap(positions=(position(mark=D(80)),))  # LONG stop 90, mark 80
    failed_ids = failed(run(snap=crossed))
    assert "RC-06" in failed_ids and "RC-07" in failed_ids
