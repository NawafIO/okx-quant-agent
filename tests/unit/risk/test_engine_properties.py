"""Engine default-deny paths and the M5 property tests (roadmap M5 acceptance)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from okxq.contracts import TradeProposal
from okxq.risk import engine
from okxq.risk import policy as pol
from okxq.risk.engine import evaluate
from okxq.risk.policy import FROZEN_RISK_POLICY as P

from .builders import facts, qual, regime, sentiment, signal, snap

D = Decimal
FAST = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def run(**over: Any) -> TradeProposal:
    return evaluate(
        over.pop("signal", signal()),
        over.pop("snap", snap()),
        over.pop("facts", facts()),
        over.pop("qual", qual()),
    )


# --- default-deny paths in the engine ------------------------------------------------------


def test_invalid_cycle_time_yields_an_already_expired_rejection() -> None:
    p = run(snap=snap(cycle_ts_ms=None))
    assert p.verdict == "REJECTED" and p.expires_at == signal().ts
    assert not p.is_executable_at(signal().ts)


def test_a_qualitative_input_that_raises_fails_rc16() -> None:
    p = run(qual=qual(regime=object()))
    assert [c.check_id for c in p.risk_checks if not c.passed] == ["RC-16"]
    assert "qualitative input error" in next(
        c.detail for c in p.risk_checks if c.check_id == "RC-16"
    )


def test_sizing_that_raises_fails_every_sizing_check() -> None:
    p = run(
        snap=snap(
            equity=D("1e999999"), high_water_mark=D("1e999999"), day_open_equity=D("1e999999")
        )
    )
    sz = [c for c in p.risk_checks if c.check_id.startswith("SZ-")]
    assert p.verdict == "REJECTED" and all(not c.passed for c in sz)
    assert all("sizing error" in c.detail for c in sz)


def test_invalid_equity_fails_sizing_without_raising() -> None:
    p = run(snap=snap(equity=None))
    assert p.verdict == "REJECTED"
    assert next(c for c in p.risk_checks if c.check_id == "SZ-4").passed is False


def test_a_check_that_raises_fails_and_a_missing_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: object) -> None:
        raise RuntimeError

    battery = dict(engine.BATTERY) | {"RC-05": boom}
    del battery["RC-08"]
    monkeypatch.setattr(engine, "BATTERY", battery)
    p = run()
    failed = {c.check_id: c.detail for c in p.risk_checks if not c.passed}
    assert failed == {"RC-05": "check error: RuntimeError", "RC-08": "no check"}
    assert p.verdict == "REJECTED"


def test_a_policy_hash_mismatch_fails_every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pol, "PINNED_RISK_POLICY_SHA256", "0" * 64)
    p = run()
    assert p.verdict == "REJECTED"
    assert all(not c.passed for c in p.risk_checks)
    assert {c.detail for c in p.risk_checks} == {"risk policy hash mismatch"}


@pytest.mark.parametrize("field", ["entry_ref", "stop_loss"])
def test_a_malformed_price_fails_sizing_even_if_the_contract_were_bypassed(field: str) -> None:
    bad = signal().model_construct(**(signal().model_dump() | {field: D("NaN")}))
    p = run(signal=bad)
    assert p.verdict == "REJECTED"
    assert next(c for c in p.risk_checks if c.check_id == "SZ-1").passed is False


def test_evaluate_signature_has_no_bypass() -> None:
    import inspect

    assert list(inspect.signature(evaluate).parameters) == ["signal", "snap", "facts", "qual"]


def test_proposal_id_is_deterministic_and_input_bound() -> None:
    assert run().proposal_id == run().proposal_id
    assert run().proposal_id != run(snap=snap(cycle_ts_ms=snap().cycle_ts_ms + 1)).proposal_id


# --- properties ----------------------------------------------------------------------------

BAD = st.sampled_from(
    [
        None,
        D("NaN"),
        D("sNaN"),
        D("Infinity"),
        D("-Infinity"),
        D(0),
        D("-0"),
        D(-1),
        0,
        1.5,
        "1",
        True,
    ]
)
#: Valid but absurd magnitudes: must never raise, and must never break the risk budget.
EXTREME = st.sampled_from([D("1e-999999"), D("1e999999"), D("1e-30"), D("1e30")])
pos_dec = st.decimals(
    min_value=D("0.0001"), max_value=D("1000000"), places=4, allow_nan=False, allow_infinity=False
)


@FAST
@given(
    equity=st.decimals(min_value=D(1), max_value=D("10000000"), places=2),
    entry=st.decimals(min_value=D("0.01"), max_value=D("100000"), places=2),
    frac=st.decimals(min_value=D("0.001"), max_value=D("0.5"), places=3),
    lot=st.sampled_from([D("0.0001"), D("0.001"), D("0.01"), D("0.1"), D(1), D(10)]),
    long=st.booleans(),
)
def test_risk_never_exceeds_budget_and_qty_is_never_rounded_up(
    equity: Decimal, entry: Decimal, frac: Decimal, lot: Decimal, long: bool
) -> None:
    dist = (entry * frac).quantize(D("0.01")) or D("0.01")
    stop = entry - dist if long else entry + dist
    if stop <= 0:
        return
    sig = signal(
        side="LONG" if long else "SHORT",
        entry_ref=entry,
        stop_loss=stop,
        take_profit=(entry + dist if long else entry - dist,),
    )
    if not long and entry - dist <= 0:
        return
    p = run(
        signal=sig,
        snap=snap(
            equity=equity, free_margin=equity * 10, high_water_mark=equity, day_open_equity=equity
        ),
        facts=facts(
            lot_size_base=lot,
            min_size_base=lot,
            tick_size=D("0.0001"),
            atr_1h=dist,
            max_size_base=D("1e30"),
        ),
    )
    budget = equity * P.max_risk_per_trade
    if p.verdict == "APPROVED":
        assert p.risk_amount <= budget
        assert p.qty_base % lot == 0
        assert (p.qty_base + lot) * dist > budget  # one more lot would exceed: floored, not up
        assert p.liquidation_estimate is not None
        gap = abs(entry - p.liquidation_estimate)
        assert gap >= P.liq_buffer_stop_multiple * dist  # liquidation beyond the stop
    else:
        assert p.qty_base == 0


@FAST
@given(min_size=pos_dec)
def test_qty_below_min_size_rejects_never_inflates(min_size: Decimal) -> None:
    p = run(facts=facts(min_size_base=min_size))
    if min_size > D(25):  # the default fixture sizes to exactly 25
        assert p.verdict == "REJECTED" and p.qty_base == 0
    else:
        assert p.verdict == "APPROVED" and p.qty_base == 25


SNAP_FIELDS = [
    "equity",
    "free_margin",
    "high_water_mark",
    "day_open_equity",
    "entries_last_hour",
    "consecutive_losses",
    "cycle_ts_ms",
    "positions",
    "signals_this_bar",
    "kill_switch_engaged",
    "api_error_breach",
]
FACT_FIELDS = [
    "tick_size",
    "lot_size_base",
    "min_size_base",
    "max_size_base",
    "mmr",
    "atr_1h",
    "last_bar_close_ts_ms",
    "timeframe_ms",
    "tradable",
]


@FAST
@given(
    where=st.sampled_from(["snap", "facts"]),
    s_field=st.sampled_from(SNAP_FIELDS),
    f_field=st.sampled_from(FACT_FIELDS),
    bad=BAD,
)
def test_malformed_input_rejects_and_evaluate_never_raises(
    where: str, s_field: str, f_field: str, bad: object
) -> None:
    if where == "snap":
        p = run(snap=replace(snap(), **{s_field: bad}))
    else:
        p = run(facts=replace(facts(), **{f_field: bad}))
    # Values that are legitimately valid for the field; repr-keyed (sNaN cannot be hashed).
    tolerated = {
        ("snap", "entries_last_hour", "0"),
        ("snap", "consecutive_losses", "0"),
        ("snap", "entries_last_hour", "1"),
        ("snap", "consecutive_losses", "1"),
        ("snap", "kill_switch_engaged", "0"),
        ("facts", "tradable", "True"),
        ("facts", "mmr", "Decimal('0')"),
        ("facts", "mmr", "Decimal('-0')"),
    }
    key = (where, s_field if where == "snap" else f_field, repr(bad))
    if key in tolerated and p.verdict == "APPROVED":
        return
    assert p.verdict == "REJECTED", key
    assert p.qty_base == 0


@FAST
@given(
    determined_by=st.sampled_from(["RULE", "LLM_CONFIRMED", "RULE_LLM_DISAGREE"]),
    regime_label=st.sampled_from(["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "CRISIS"]),
    score=st.decimals(min_value=D(-1), max_value=D(1), places=2),
    conf=st.decimals(min_value=D(0), max_value=D(1), places=2),
    with_sentiment=st.booleans(),
    side=st.sampled_from(["LONG", "SHORT"]),
)
def test_qualitative_input_is_monotone_risk_reducing(
    determined_by: str,
    regime_label: str,
    score: Decimal,
    conf: Decimal,
    with_sentiment: bool,
    side: str,
) -> None:
    sig = signal(side=side)
    base = run(signal=sig)
    q = qual(
        regime=regime(determined_by=determined_by, regime=regime_label),
        sentiment=sentiment(score=score, confidence=conf) if with_sentiment else None,
    )
    p = run(signal=sig, qual=q)
    assert p.qty_base <= base.qty_base
    assert p.risk_amount <= base.risk_amount
    assert not (p.verdict == "APPROVED" and base.verdict == "REJECTED")


@FAST
@given(
    where=st.sampled_from(["snap", "facts"]),
    s_field=st.sampled_from(["equity", "free_margin", "high_water_mark", "day_open_equity"]),
    f_field=st.sampled_from(
        ["tick_size", "lot_size_base", "min_size_base", "max_size_base", "mmr", "atr_1h"]
    ),
    big=EXTREME,
)
def test_extreme_magnitudes_never_raise_and_never_break_the_budget(
    where: str, s_field: str, f_field: str, big: Decimal
) -> None:
    sn = replace(snap(), **{s_field: big}) if where == "snap" else snap()
    fa = replace(facts(), **{f_field: big}) if where == "facts" else facts()
    p = run(snap=sn, facts=fa)
    if p.verdict == "APPROVED":
        assert p.risk_amount <= sn.equity * P.max_risk_per_trade
        assert p.risk_pct_of_equity <= P.max_risk_per_trade
    else:
        assert p.qty_base == 0


@pytest.mark.parametrize("ts", [10**20, 2**62])
def test_an_out_of_range_cycle_time_never_raises(ts: int) -> None:
    """Closing audit #11: datetime overflow in the expiry must not escape evaluate."""
    p = run(snap=snap(cycle_ts_ms=ts))
    assert p.verdict == "REJECTED" and p.expires_at == signal().ts
