"""Portfolio reducer and its persistence (architecture §14; M5 acceptance)."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from okxq.audit.chain import AuditChain, read_chain
from okxq.risk.portfolio import (
    DAY_MS,
    HOUR_MS,
    Closed,
    Event,
    FundingAccrued,
    Init,
    MarkUpdate,
    Opened,
    PortfolioError,
    StopMoved,
    apply,
    initial,
)
from okxq.risk.store import PortfolioStore, decode, encode

D = Decimal
T0 = 1_767_268_800_000  # 2026-01-01T12:00Z
BTC = "BTC-USDT-SWAP"


def start() -> Any:
    return initial("PAPER", Init(T0, D(10_000)))


def opened(sym: str = BTC, side: str = "LONG", ts: int = T0 + 1) -> Opened:
    return Opened(ts, sym, side, D(10), D(100), D(95), D(1), D(1))  # type: ignore[arg-type]


def test_open_mark_close_and_conservative_equity() -> None:
    s = apply(start(), opened())
    assert s.realised == D(9999) and s.equity == D(9999) and s.margin_used == D(1000)
    up = apply(s, MarkUpdate(T0 + 2, BTC, D(110)))
    assert up.equity == D(9999)  # unrealised GAINS excluded
    assert up.high_water_mark == D(10_000)
    down = apply(up, MarkUpdate(T0 + 3, BTC, D(90)))
    assert down.equity == D(9899)  # unrealised LOSSES counted
    shut = apply(down, Closed(T0 + 4, BTC, D(90), D(1)))
    assert shut.realised == D(9898) and shut.positions == ()
    assert (shut.consecutive_losses, shut.last_loss_ts_ms) == (1, T0 + 4)
    win = apply(apply(shut, opened(ts=T0 + 5)), Closed(T0 + 6, BTC, D(200), D(1)))
    assert win.consecutive_losses == 0 and win.last_loss_ts_ms == T0 + 4
    assert win.high_water_mark == win.equity == D(10_896)  # 9898 - 1 fee + 999


def test_a_loss_is_judged_on_the_round_trip_including_the_entry_fee() -> None:
    """Gross +0.5 at the close with no exit fee beats the exit fee but not the 1.0 entry fee:
    the trade lost 0.5 net and must count for RC-13 (cycle-2 advisor finding)."""
    s = apply(start(), opened())  # qty 10 @ 100, entry fee 1
    assert s.positions[0].entry_fee == D(1)
    shut = apply(s, Closed(T0 + 2, BTC, D("100.05"), D(0)))
    assert shut.realised == D("9999.5")
    assert (shut.consecutive_losses, shut.last_loss_ts_ms) == (1, T0 + 2)
    # Exactly break-even on the round trip is not a loss.
    even = apply(apply(start(), opened()), Closed(T0 + 2, BTC, D("100.1"), D(0)))
    assert even.realised == D(10_000) and even.consecutive_losses == 0


def test_short_side_pnl_stop_moves_and_funding() -> None:
    s = apply(start(), opened(side="SHORT"))
    s = apply(s, StopMoved(T0 + 2, BTC, D(102)))
    assert s.positions[0].stop == D(102)
    s = apply(s, FundingAccrued(T0 + 3, BTC, D("-2.5")))
    s = apply(s, Closed(T0 + 4, BTC, D(90), D(0)))
    assert s.realised == D(9999) - D("2.5") + D(100)


def test_marks_for_symbols_not_held_are_ignored() -> None:
    s = apply(start(), MarkUpdate(T0 + 1, BTC, D(1)))
    assert s.positions == () and s.equity == D(10_000)


def test_day_rollover_sets_opening_equity_at_the_first_event_of_the_utc_day() -> None:
    s = apply(apply(start(), opened()), MarkUpdate(T0 + 2, BTC, D(90)))
    nxt = (T0 // DAY_MS + 1) * DAY_MS  # 00:00:00.000 UTC next day
    before = apply(s, MarkUpdate(nxt - 1, BTC, D(80)))
    assert before.day_open_equity == D(10_000)  # 23:59:59.999 is still the old day
    after = apply(before, MarkUpdate(nxt, BTC, D(80)))
    assert after.day_open_equity == D(9799)  # equity at the boundary, before this mark


def test_entries_in_the_trailing_hour_are_pruned() -> None:
    s = apply(start(), opened(BTC, ts=T0 + 1))
    s = apply(s, opened("ETH-USDT-SWAP", ts=T0 + HOUR_MS + 1))
    assert s.entry_times_ms == (T0 + HOUR_MS + 1,)


@pytest.mark.parametrize(
    ("event", "match"),
    [
        (Init(T0 + 1, D(1)), "Init only starts"),
        (MarkUpdate(T0 - 1, BTC, D(1)), "precedes"),
        (Closed(T0 + 1, BTC, D(1), D(0)), "not held"),
        (StopMoved(T0 + 1, BTC, D(1)), "not held"),
        (FundingAccrued(T0 + 1, BTC, D(1)), "not held"),
        (replace(opened(), qty=D(0)), "qty"),
        (replace(opened(), entry=D("NaN")), "entry"),
        (replace(opened(), side="FLAT"), "side"),  # type: ignore[arg-type]
    ],
)
def test_inconsistent_events_raise(event: Any, match: str) -> None:
    with pytest.raises(PortfolioError, match=match):
        apply(start(), event)


def test_double_open_and_bad_close_price_raise() -> None:
    s = apply(start(), opened())
    with pytest.raises(PortfolioError, match="already held"):
        apply(s, opened(ts=T0 + 2))
    with pytest.raises(PortfolioError, match="price"):
        apply(s, Closed(T0 + 2, BTC, D(0), D(0)))
    with pytest.raises(PortfolioError, match="equity"):
        initial("PAPER", Init(T0, D(0)))


# --- persistence ---------------------------------------------------------------------------

EVENTS: list[Event] = [
    Init(T0, D(10_000)),
    opened(),
    MarkUpdate(T0 + 2, BTC, D(97)),
    StopMoved(T0 + 3, BTC, D(98)),
    FundingAccrued(T0 + 4, BTC, D("-0.1")),
    Closed(T0 + 5, BTC, D(99), D(1)),
]


def store(tmp_path: Path) -> PortfolioStore:
    return PortfolioStore("PAPER", tmp_path / "p.db", tmp_path / "audit.jsonl")


def test_replay_reproduces_state_bit_for_bit_and_mirrors_to_the_audit_chain(
    tmp_path: Path,
) -> None:
    st = store(tmp_path)
    assert st.load() is None  # no state: the engine will REJECT (no HWM, never reset)
    live = None
    for e in EVENTS:
        live = st.append(e)
    assert store(tmp_path).load() == live
    kinds = [r.kind for r in read_chain(tmp_path / "audit.jsonl")]
    assert kinds == ["portfolio_event"] * len(EVENTS)
    assert all(decode(encode(e)) == e for e in EVENTS)


def test_an_audit_failure_rolls_the_event_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = store(tmp_path)
    st.append(EVENTS[0])

    def disk_full(self: object, kind: str, payload: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(AuditChain, "append", disk_full)
    with pytest.raises(OSError, match="disk full"):
        st.append(EVENTS[1])
    monkeypatch.undo()
    assert st.load() == initial("PAPER", Init(T0, D(10_000)))  # the Opened row was rolled back


def test_a_lost_database_never_resets_the_baselines(tmp_path: Path) -> None:
    """Closing audit #2: deleting the database next to a chain that holds events refuses,
    rather than starting a fresh Init with HWM = day-open = current equity."""
    st = store(tmp_path)
    for e in EVENTS[:3]:
        st.append(e)
    (tmp_path / "p.db").unlink()
    fresh = store(tmp_path)
    with pytest.raises(PortfolioError, match="disagree"):
        fresh.load()
    with pytest.raises(PortfolioError, match="disagree"):
        fresh.append(Init(T0 + 10, D(9_000)))


def test_an_audit_record_without_its_database_row_refuses(tmp_path: Path) -> None:
    """Closing audit #14: a commit that failed after its audit record was written."""
    st = store(tmp_path)
    st.append(EVENTS[0])
    AuditChain(tmp_path / "audit.jsonl").append("portfolio_event", encode(EVENTS[1]))
    with pytest.raises(PortfolioError, match="disagree"):
        st.load()


def test_the_log_must_start_with_init(tmp_path: Path) -> None:
    st = store(tmp_path)
    with pytest.raises(PortfolioError, match="first event must be Init"):
        st.append(EVENTS[1])
    body = json.dumps(encode(EVENTS[1]), sort_keys=True)
    with sqlite3.connect(tmp_path / "p.db") as con:
        con.execute("INSERT INTO portfolio_events (body) VALUES (?)", (body,))
    AuditChain(tmp_path / "audit.jsonl").append("portfolio_event", json.loads(body))
    with pytest.raises(PortfolioError, match="does not start with Init"):
        st.load()


@pytest.mark.parametrize(
    "event",
    [
        replace(opened(), fee=D("NaN")),
        Closed(T0 + 2, BTC, D(100), D("Infinity")),
        FundingAccrued(T0 + 2, BTC, D("sNaN")),
    ],
    ids=["open-fee", "close-fee", "funding"],
)
def test_non_finite_fees_and_funding_are_refused(event: Any) -> None:
    """Closing audit #3: one NaN in realised P&L would stop every halt from ever firing."""
    s = start() if isinstance(event, Opened) else apply(start(), opened())
    with pytest.raises(PortfolioError, match="finite"):
        apply(s, event)


def _open_handles(path: Path) -> int:
    n = 0
    for fd in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            n += os.readlink(f"/proc/self/fd/{fd}") == str(path)
    return n


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc (Linux)")
def test_no_store_operation_leaves_the_database_open(tmp_path: Path) -> None:
    """A connection left to the garbage collector keeps the file LOCKED on Windows (the
    first real Windows run failed on it). Checked without any gc.collect()."""
    db = tmp_path / "p.db"
    st = store(tmp_path)
    assert _open_handles(db) == 0
    st.append(EVENTS[0])
    assert _open_handles(db) == 0
    st.load()
    assert _open_handles(db) == 0
