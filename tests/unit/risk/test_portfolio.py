"""Portfolio reducer and its persistence (architecture §14; M5 acceptance)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from okxq.risk.portfolio import (
    DAY_MS,
    HOUR_MS,
    Closed,
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


class Audit:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("disk full")
        self.records.append((kind, payload))


EVENTS = [
    Init(T0, D(10_000)),
    opened(),
    MarkUpdate(T0 + 2, BTC, D(97)),
    StopMoved(T0 + 3, BTC, D(98)),
    FundingAccrued(T0 + 4, BTC, D("-0.1")),
    Closed(T0 + 5, BTC, D(99), D(1)),
]


def test_replay_reproduces_state_bit_for_bit_and_mirrors_to_the_audit_chain(
    tmp_path: Path,
) -> None:
    audit = Audit()
    store = PortfolioStore("PAPER", tmp_path / "p.db", audit)
    assert store.load() is None  # no state: the engine will REJECT (no HWM, never reset)
    live = None
    for e in EVENTS:
        live = store.append(e)
    replayed = PortfolioStore("PAPER", tmp_path / "p.db", Audit()).load()
    assert replayed == live
    assert [k for k, _ in audit.records] == ["portfolio_event"] * len(EVENTS)
    assert all(decode(encode(e)) == e for e in EVENTS)


def test_an_audit_failure_rolls_the_event_back(tmp_path: Path) -> None:
    store = PortfolioStore("PAPER", tmp_path / "p.db", Audit())
    store.append(EVENTS[0])
    broken = PortfolioStore("PAPER", tmp_path / "p.db", Audit(fail=True))
    with pytest.raises(OSError):
        broken.append(EVENTS[1])
    assert store.load() == initial("PAPER", EVENTS[0])  # Opened was not persisted


def test_the_log_must_start_with_init(tmp_path: Path) -> None:
    store = PortfolioStore("PAPER", tmp_path / "p.db", Audit())
    with pytest.raises(PortfolioError, match="first event must be Init"):
        store.append(EVENTS[1])
    import json
    import sqlite3

    with sqlite3.connect(tmp_path / "p.db") as con:
        con.execute(
            "INSERT INTO portfolio_events (body) VALUES (?)", (json.dumps(encode(EVENTS[1])),)
        )
    with pytest.raises(PortfolioError, match="does not start with Init"):
        store.load()
