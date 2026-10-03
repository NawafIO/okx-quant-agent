"""The cycle: latching halts via the single ingest path and a timer, fresh switch reads,
audited proposals (M5_DESIGN §11 B-1, §12.2; closing audit #1, #4, #5, #6)."""

from __future__ import annotations

import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from okxq.risk.cycle import decide, halt_triggers, ingest, on_tick, snapshot
from okxq.risk.killswitch import KillSwitch
from okxq.risk.policy import FROZEN_RISK_POLICY as P
from okxq.risk.portfolio import HOUR_MS, Event, Init, MarkUpdate, Opened, apply, initial

from .builders import T_MS, facts, qual, signal

D = Decimal
STALE_MS = P.mark_stale_s * 1000
ETH = "ETH-USDT-SWAP"


class Switch:
    def __init__(self, fail: bool = False) -> None:
        self.on = False
        self.durable = False
        self.sentinel = False
        self.fail = fail
        self.reads = 0
        self.triggers: list[str] = []

    def engaged(self) -> bool:
        self.reads += 1
        return self.on or self.sentinel

    def latched(self) -> bool:
        return self.durable

    def sentinel_present(self) -> bool:
        return self.sentinel

    def engage(self, trigger: str, detail: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("cannot persist")
        self.on = self.durable = True
        self.triggers.append(trigger)


class Audit:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append((kind, payload))


class Store:
    """In-memory stand-in for PortfolioStore (the real one is tested in test_portfolio)."""

    def __init__(self, state: Any) -> None:
        self.state = state

    def append(self, event: Event) -> Any:
        self.state = apply(self.state, event)
        return self.state


def holding(entry_ts: int = T_MS - 10) -> Any:
    s = initial("PAPER", Init(T_MS - 3 * HOUR_MS, D(10_000)))
    return apply(s, Opened(entry_ts, ETH, "LONG", D(100), D(100), D(50), D(1), D(0)))


def test_daily_loss_halt_fires_at_exactly_2pct_through_ingest() -> None:
    sw, st = Switch(), Store(holding())
    s = ingest(st, MarkUpdate(T_MS - 5, ETH, D("98.01")), T_MS, sw)
    assert sw.on is False and s.equity == D(9801)  # 1.99%: no halt
    ingest(st, MarkUpdate(T_MS - 4, ETH, D(98)), T_MS, sw)
    assert sw.triggers == ["RC-10 daily loss"]  # exactly 2.00%


def test_a_halt_latches_a_recovered_loss_does_not_resume() -> None:
    sw, st = Switch(), Store(holding())
    ingest(st, MarkUpdate(T_MS - 5, ETH, D(97)), T_MS, sw)
    ingest(st, MarkUpdate(T_MS - 4, ETH, D(100)), T_MS, sw)
    assert sw.on is True and len(sw.triggers) == 1


def test_a_halt_is_written_even_while_the_switch_already_reads_engaged() -> None:
    """Closing audit #1: reading engaged (sentinel, unreadable cache) is not latched."""
    sw, st = Switch(), Store(holding())
    sw.sentinel = True
    ingest(st, MarkUpdate(T_MS - 5, ETH, D(97)), T_MS, sw)
    assert sw.durable is True and sw.triggers == ["RC-10 daily loss"]


def test_deleting_the_sentinel_never_disarms(tmp_path: Path) -> None:
    """Closing audit #1 end to end with the REAL switch: a seen sentinel becomes a durable
    ENGAGE, so removing the file leaves the switch engaged until an audited disarm."""
    ks = KillSwitch("PAPER", tmp_path / "state", tmp_path / "audit.jsonl")
    ks.engage("setup", {})
    import contextlib

    with contextlib.ExitStack() as stack:
        mp = stack.enter_context(pytest.MonkeyPatch.context())
        mp.setattr(sys.stdin, "isatty", lambda: True)
        ks.disarm("op", "DISARM PAPER", "setup")
    assert ks.engaged() is False
    ks.sentinel.touch()
    on_tick(initial("PAPER", Init(T_MS, D(10_000))), T_MS, ks)
    ks.sentinel.unlink()
    assert ks.latched() is True and ks.engaged() is True


def test_drawdown_halt_at_exactly_10pct() -> None:
    s = initial("PAPER", Init(T_MS - 100, D(10_000)))
    s = apply(s, Opened(T_MS - 50, ETH, "LONG", D(100), D(100), D(10), D(1), D(0)))
    s = apply(s, MarkUpdate(T_MS - 40, ETH, D(90)))
    halts = halt_triggers(s, T_MS, P)
    assert [h.trigger for h in halts] == ["RC-10 daily loss", "RC-11 drawdown"]
    assert halts[1].observed == D("0.1") and halts[1].limit == D("0.10")


def test_a_dead_feed_halts_on_the_timer_alone_at_the_pinned_budget() -> None:
    sw = Switch()
    s = holding(entry_ts=T_MS - STALE_MS - 1)  # mark time = entry time
    on_tick(s, T_MS - 1, sw)
    assert sw.on is False  # exactly at the budget: still fresh
    on_tick(s, T_MS, sw)
    assert sw.triggers == [f"stale marks: {ETH}"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_a_mark_beyond_the_stop_is_an_incident_halt(side: str) -> None:
    """Closing audit #6: a failed protective stop halts; it is never zero risk."""
    s = initial("PAPER", Init(T_MS - 100, D(1_000_000)))
    stop = D(99) if side == "LONG" else D(101)
    s = apply(s, Opened(T_MS - 50, ETH, side, D(1), D(100), stop, D(1), D(0)))  # type: ignore[arg-type]
    beyond = D("98.9") if side == "LONG" else D("101.1")
    s = apply(s, MarkUpdate(T_MS - 40, ETH, beyond))
    assert [h.trigger for h in halt_triggers(s, T_MS, P)] == [f"stop crossed: {ETH}"]
    at = apply(s, MarkUpdate(T_MS - 30, ETH, stop))
    assert halt_triggers(at, T_MS, P) == ()  # at the stop exactly: not crossed


@pytest.mark.parametrize("field", ["realised", "day_open_equity", "high_water_mark"])
@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D(0), D(-1)])
def test_a_non_finite_or_non_positive_basis_is_a_halt(field: str, value: D) -> None:
    change: dict[str, Any] = {field: value}
    s = replace(initial("PAPER", Init(T_MS, D(10_000))), **change)
    (h,) = halt_triggers(s, T_MS, P)
    assert h.trigger == "non-finite or non-positive equity basis"


def test_an_engage_that_fails_propagates_so_the_process_stops() -> None:
    with pytest.raises(OSError):
        ingest(Store(holding()), MarkUpdate(T_MS - 5, ETH, D(90)), T_MS, Switch(fail=True))


def test_already_latched_is_not_re_engaged() -> None:
    sw = Switch()
    sw.durable = True
    on_tick(holding(entry_ts=T_MS - 2 * STALE_MS), T_MS, sw)
    assert sw.triggers == []


def test_decide_reads_the_switch_fresh_and_audits_every_proposal() -> None:
    sw, audit = Switch(), Audit()
    s = initial("PAPER", Init(T_MS - 100, D(10_000)))
    d = decide(signal(), s, facts(), qual(), T_MS, sw, audit, (), False)
    assert d.proposal.verdict == "APPROVED" and d.halts == ()
    assert sw.reads >= 1 and audit.records[-1][0] == "risk_proposal"
    sw.on = True
    d2 = decide(signal(), s, facts(), qual(), T_MS, sw, audit, (), False)
    assert [c.check_id for c in d2.proposal.risk_checks if not c.passed] == ["RC-01"]
    assert len(audit.records) == 2


def test_decide_latches_a_pending_halt_before_evaluating() -> None:
    sw = Switch()
    s = apply(holding(), MarkUpdate(T_MS - 5, ETH, D(97)))
    d = decide(signal(), s, facts(), qual(), T_MS, sw, Audit(), (), False)
    assert sw.on is True and d.halts and d.proposal.verdict == "REJECTED"


def test_snapshot_counts_entries_in_the_trailing_hour_only() -> None:
    s = holding(entry_ts=T_MS - HOUR_MS)
    assert snapshot(s, T_MS, Switch(), (), False).entries_last_hour == 0
    assert snapshot(s, T_MS - 1, Switch(), (), False).entries_last_hour == 1


def test_the_protocol_stubs_are_inert() -> None:
    """Covers the Protocol method bodies without a coverage exclusion (B-4: none allowed)."""
    from okxq.risk import cycle

    assert cycle.Switch.engaged(Switch()) is None
    assert cycle.Switch.latched(Switch()) is None
    assert cycle.Switch.sentinel_present(Switch()) is None
    cycle.Switch.engage(Switch(), "t", {})  # returns None by signature
    assert cycle.Audit.append(Audit(), "k", {}) is None
    assert cycle.Store.append(Store(None), Init(0, D(1))) is None


def test_decide_also_turns_a_seen_sentinel_into_a_durable_engage() -> None:
    """Confirmation note on #1: a sentinel seen by decide (not only on_tick) latches."""
    sw = Switch()
    sw.sentinel = True
    d = decide(
        signal(),
        initial("PAPER", Init(T_MS - 100, D(10_000))),
        facts(),
        qual(),
        T_MS,
        sw,
        Audit(),
        (),
        False,
    )
    assert sw.durable is True and sw.triggers == ["KILL sentinel"]
    assert d.proposal.verdict == "REJECTED"
