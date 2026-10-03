"""The cycle: latching halts on every mark and on a timer, fresh switch reads, audited
proposals (M5_DESIGN §11 B-1, §12.2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from okxq.risk.cycle import decide, halt_triggers, on_mark, on_tick, snapshot
from okxq.risk.policy import FROZEN_RISK_POLICY as P
from okxq.risk.portfolio import HOUR_MS, Init, MarkUpdate, Opened, apply, initial

from .builders import T_MS, facts, qual, signal

D = Decimal
BUDGET = 3_720_000  # one 1h bar + 120 s grace
ETH = "ETH-USDT-SWAP"


class Switch:
    def __init__(self, fail: bool = False) -> None:
        self.on = False
        self.fail = fail
        self.reads = 0
        self.triggers: list[str] = []

    def engaged(self) -> bool:
        self.reads += 1
        return self.on

    def engage(self, trigger: str, detail: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("cannot persist")
        self.on = True
        self.triggers.append(trigger)


class Audit:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append((kind, payload))


def holding(entry_ts: int = T_MS - 10) -> Any:
    s = initial("PAPER", Init(T_MS - 3 * HOUR_MS, D(10_000)))
    return apply(s, Opened(entry_ts, ETH, "LONG", D(100), D(100), D(50), D(1), D(0)))


def test_daily_loss_halt_fires_at_exactly_2pct_on_a_mark_with_no_signal() -> None:
    sw = Switch()
    s = on_mark(holding(), MarkUpdate(T_MS - 5, ETH, D("98.01")), T_MS, BUDGET, sw)
    assert sw.on is False and s.equity == D(9801)  # 1.99% loss: no halt
    s = on_mark(s, MarkUpdate(T_MS - 4, ETH, D(98)), T_MS, BUDGET, sw)
    assert sw.on is True and sw.triggers == ["RC-10 daily loss"]  # exactly 2.00%


def test_a_halt_latches_a_recovered_loss_does_not_resume() -> None:
    sw = Switch()
    s = on_mark(holding(), MarkUpdate(T_MS - 5, ETH, D(97)), T_MS, BUDGET, sw)
    assert sw.on is True
    on_mark(s, MarkUpdate(T_MS - 4, ETH, D(100)), T_MS, BUDGET, sw)
    assert sw.on is True and len(sw.triggers) == 1  # engaged once; only a disarm releases


def test_drawdown_halt_at_exactly_10pct() -> None:
    s = initial("PAPER", Init(T_MS - 100, D(10_000)))
    s = apply(s, Opened(T_MS - 50, ETH, "LONG", D(100), D(100), D(10), D(1), D(0)))
    s = apply(s, MarkUpdate(T_MS - 40, ETH, D(90)))
    halts = halt_triggers(s, T_MS, BUDGET, P)
    assert [h.trigger for h in halts] == ["RC-10 daily loss", "RC-11 drawdown"]
    assert halts[1].observed == D("0.1") and halts[1].limit == D("0.10")


def test_a_dead_feed_halts_on_the_timer_alone() -> None:
    sw = Switch()
    s = holding(entry_ts=T_MS - BUDGET - 1)  # mark time = entry time
    on_tick(s, T_MS - 1, BUDGET, sw)
    assert sw.on is False  # exactly at the budget: still fresh
    on_tick(s, T_MS, BUDGET, sw)
    assert sw.triggers == [f"stale marks: {ETH}"]


def test_non_positive_equity_halts_instead_of_dividing_by_zero() -> None:
    s = initial("PAPER", Init(T_MS - 100, D(100)))
    s = apply(s, Opened(T_MS - 50, ETH, "LONG", D(10), D(100), D(1), D(1), D(0)))
    s = apply(s, MarkUpdate(T_MS - 40, ETH, D(1)))
    (h,) = halt_triggers(s, T_MS, BUDGET, P)
    assert h.trigger == "non-positive equity basis" and h.observed == D(-890)


def test_an_engage_that_fails_propagates_so_the_process_stops() -> None:
    with pytest.raises(OSError):
        on_mark(holding(), MarkUpdate(T_MS - 5, ETH, D(90)), T_MS, BUDGET, Switch(fail=True))


def test_already_engaged_is_not_re_engaged() -> None:
    sw = Switch()
    sw.on = True
    on_tick(holding(entry_ts=T_MS - 2 * BUDGET), T_MS, BUDGET, sw)
    assert sw.triggers == []


def test_decide_reads_the_switch_fresh_and_audits_every_proposal() -> None:
    sw, audit = Switch(), Audit()
    s = initial("PAPER", Init(T_MS - 100, D(10_000)))
    d = decide(signal(), s, facts(), qual(), T_MS, BUDGET, sw, audit, (), False)
    assert d.proposal.verdict == "APPROVED" and d.halts == ()
    assert sw.reads >= 1 and audit.records[-1][0] == "risk_proposal"
    sw.on = True
    d2 = decide(signal(), s, facts(), qual(), T_MS, BUDGET, sw, audit, (), False)
    assert d2.proposal.verdict == "REJECTED"
    assert [c.check_id for c in d2.proposal.risk_checks if not c.passed] == ["RC-01"]
    assert len(audit.records) == 2


def test_decide_latches_a_pending_halt_before_evaluating() -> None:
    sw, audit = Switch(), Audit()
    s = apply(holding(), MarkUpdate(T_MS - 5, ETH, D(97)))
    d = decide(signal(), s, facts(), qual(), T_MS, BUDGET, sw, audit, (), False)
    assert sw.on is True and d.halts and d.proposal.verdict == "REJECTED"


def test_snapshot_counts_entries_in_the_trailing_hour_only() -> None:
    s = holding(entry_ts=T_MS - HOUR_MS)
    assert snapshot(s, T_MS, Switch(), (), False).entries_last_hour == 0
    assert snapshot(s, T_MS - 1, Switch(), (), False).entries_last_hour == 1


def test_the_protocol_stubs_are_inert() -> None:
    """Covers the Protocol method bodies without a coverage exclusion (B-4: none allowed)."""
    from okxq.risk import cycle

    assert cycle.Switch.engaged(Switch()) is None
    assert cycle.Switch.engage(Switch(), "t", {}) is None
    assert cycle.Audit.append(Audit(), "k", {}) is None
