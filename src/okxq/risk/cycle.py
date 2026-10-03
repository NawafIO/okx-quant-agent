"""The decision cycle around the pure engine (M5_DESIGN §11 B-1, §12.2; closing audit).

* ``ingest`` is the ONLY way a portfolio event enters the system: it persists the event
  (store + audit chain) and evaluates the halts in the same call, so no caller can persist
  marks without latching (closing audit #4; guard: nothing else calls ``apply`` or holds a
  ``PortfolioStore``).
* ``halt_triggers`` is pure: day loss, drawdown, a non-finite or non-positive equity basis,
  a held position whose mark is beyond its stop (a failed protective stop, §15.3), and a
  held mark older than the PINNED ``mark_stale_s`` (a dead feed, §19.3).
* A halt LATCHES: it writes an ENGAGE unless the chain's latest switch record already is one
  - never skipped merely because the switch happens to read engaged (sentinel, unreadable
  cache). ``on_tick`` and ``decide`` both turn a seen ``KILL`` sentinel into a durable
  ENGAGE, so deleting the file never disarms (closing audit #1 and its confirmation
  note). Only an audited manual disarm releases.
* If the ENGAGE cannot be written the error propagates and the caller's process must stop.
* ``decide`` reads the switch FRESH, evaluates, audits the proposal, returns a ``Decision``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Protocol

from okxq.contracts import Signal, TradeProposal
from okxq.risk.engine import evaluate
from okxq.risk.inputs import MarketFacts, OpenPosition, PortfolioSnapshot, QualInputs
from okxq.risk.num import risk_context
from okxq.risk.policy import FROZEN_RISK_POLICY, RiskPolicy
from okxq.risk.portfolio import HOUR_MS, Event, PortfolioState


class Switch(Protocol):
    def engaged(self) -> bool: ...

    def latched(self) -> bool: ...

    def sentinel_present(self) -> bool: ...

    def engage(self, trigger: str, detail: dict[str, Any]) -> None: ...


class Audit(Protocol):
    def append(self, kind: str, payload: dict[str, Any]) -> object: ...


class Store(Protocol):
    def append(self, event: Event) -> PortfolioState: ...


@dataclass(frozen=True)
class Halt:
    trigger: str
    observed: Decimal | None
    limit: Decimal | None


@dataclass(frozen=True)
class Decision:
    proposal: TradeProposal
    halts: tuple[Halt, ...]


def halt_triggers(state: PortfolioState, now_ms: int, p: RiskPolicy) -> tuple[Halt, ...]:
    with localcontext(risk_context()):
        eq, start, hwm = state.equity, state.day_open_equity, state.high_water_mark
        if not all(x.is_finite() and x > 0 for x in (eq, start, hwm)):
            return (Halt("non-finite or non-positive equity basis", None, Decimal(0)),)
        out: list[Halt] = []
        loss = (start - eq) / start
        if loss >= p.daily_loss_limit:
            out.append(Halt("RC-10 daily loss", loss, p.daily_loss_limit))
        dd = (hwm - eq) / hwm
        if dd >= p.max_dd_halt:
            out.append(Halt("RC-11 drawdown", dd, p.max_dd_halt))
        crossed = [
            h.symbol
            for h in state.positions
            if (h.mark < h.stop if h.side == "LONG" else h.mark > h.stop)
        ]
        if crossed:
            out.append(Halt(f"stop crossed: {','.join(crossed)}", None, None))
        budget = p.mark_stale_s * 1000
        stale = [h.symbol for h in state.positions if now_ms - h.mark_ts_ms > budget]
        if stale:
            out.append(Halt(f"stale marks: {','.join(stale)}", None, Decimal(budget)))
        return tuple(out)


def _latch(halts: tuple[Halt, ...], switch: Switch) -> None:
    if halts and not switch.latched():
        switch.engage(
            "; ".join(h.trigger for h in halts),
            {h.trigger: [str(h.observed), str(h.limit)] for h in halts},
        )


def ingest(store: Store, event: Event, now_ms: int, switch: Switch) -> PortfolioState:
    new = store.append(event)
    _latch(halt_triggers(new, now_ms, FROZEN_RISK_POLICY), switch)
    return new


def _halts_and_sentinel(state: PortfolioState, now_ms: int, switch: Switch) -> tuple[Halt, ...]:
    halts = halt_triggers(state, now_ms, FROZEN_RISK_POLICY)
    if switch.sentinel_present():
        halts = (*halts, Halt("KILL sentinel", None, None))
    return halts


def on_tick(state: PortfolioState, now_ms: int, switch: Switch) -> None:
    _latch(_halts_and_sentinel(state, now_ms, switch), switch)


def snapshot(
    state: PortfolioState,
    now_ms: int,
    switch: Switch,
    signals_this_bar: tuple[str, ...],
    api_error_breach: bool | None,
) -> PortfolioSnapshot:
    """The ONLY way the cycle builds a snapshot: the switch is read fresh here."""
    return PortfolioSnapshot(
        env=state.env,
        cycle_ts_ms=now_ms,
        kill_switch_engaged=switch.engaged(),
        equity=state.equity,
        free_margin=state.equity - state.margin_used,
        high_water_mark=state.high_water_mark,
        day_open_equity=state.day_open_equity,
        positions=tuple(
            OpenPosition(h.symbol, h.side, h.qty, h.stop, h.mark) for h in state.positions
        ),
        entries_last_hour=sum(1 for t in state.entry_times_ms if t > now_ms - HOUR_MS),
        consecutive_losses=state.consecutive_losses,
        last_loss_ts_ms=state.last_loss_ts_ms,
        signals_this_bar=signals_this_bar,
        api_error_breach=api_error_breach,
    )


def decide(
    signal: Signal,
    state: PortfolioState,
    facts: MarketFacts,
    qual: QualInputs,
    now_ms: int,
    switch: Switch,
    audit: Audit,
    signals_this_bar: tuple[str, ...],
    api_error_breach: bool | None,
) -> Decision:
    halts = _halts_and_sentinel(state, now_ms, switch)
    _latch(halts, switch)
    snap = snapshot(state, now_ms, switch, signals_this_bar, api_error_breach)
    proposal = evaluate(signal, snap, facts, qual)
    audit.append("risk_proposal", proposal.model_dump(mode="json"))
    return Decision(proposal, halts)
