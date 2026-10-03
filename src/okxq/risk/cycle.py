"""The decision cycle around the pure engine (M5_DESIGN §11 B-1, §12.2).

* ``halt_triggers`` is pure. ``on_mark`` runs it on every mark update and ``on_tick`` on a
  timer, so a day-loss or drawdown halt fires with no signal pending and a dead feed (a held
  position's mark older than the staleness budget) is itself a halt (§19.3).
* A halt ENGAGES the kill switch, and only a manual, audited disarm releases it: a loss that
  recovers never resumes trading by itself. If the switch cannot be written the error
  propagates and the caller's process must stop; entries are blocked regardless, because an
  unwritten switch reads ENGAGED.
* ``decide`` builds the snapshot - reading the switch FRESH every cycle - evaluates, appends
  the proposal to the audit chain and returns a typed ``Decision``.
I/O is injected (``Switch``, ``Audit``), so this module is inside the 100% coverage set.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from okxq.contracts import Signal, TradeProposal
from okxq.risk.engine import evaluate
from okxq.risk.inputs import MarketFacts, OpenPosition, PortfolioSnapshot, QualInputs
from okxq.risk.policy import FROZEN_RISK_POLICY, RiskPolicy
from okxq.risk.portfolio import HOUR_MS, Event, PortfolioState, apply


class Switch(Protocol):
    def engaged(self) -> bool: ...

    def engage(self, trigger: str, detail: dict[str, Any]) -> None: ...


class Audit(Protocol):
    def append(self, kind: str, payload: dict[str, Any]) -> object: ...


@dataclass(frozen=True)
class Halt:
    trigger: str
    observed: Decimal | None
    limit: Decimal | None


@dataclass(frozen=True)
class Decision:
    proposal: TradeProposal
    halts: tuple[Halt, ...]


def halt_triggers(
    state: PortfolioState, now_ms: int, mark_budget_ms: int, p: RiskPolicy
) -> tuple[Halt, ...]:
    out: list[Halt] = []
    eq = state.equity
    if eq <= 0 or state.day_open_equity <= 0 or state.high_water_mark <= 0:
        return (Halt("non-positive equity basis", eq, Decimal(0)),)
    loss = (state.day_open_equity - eq) / state.day_open_equity
    if loss >= p.daily_loss_limit:
        out.append(Halt("RC-10 daily loss", loss, p.daily_loss_limit))
    dd = (state.high_water_mark - eq) / state.high_water_mark
    if dd >= p.max_dd_halt:
        out.append(Halt("RC-11 drawdown", dd, p.max_dd_halt))
    stale = [h.symbol for h in state.positions if now_ms - h.mark_ts_ms > mark_budget_ms]
    if stale:
        out.append(Halt(f"stale marks: {','.join(stale)}", None, Decimal(mark_budget_ms)))
    return tuple(out)


def _latch(halts: tuple[Halt, ...], switch: Switch) -> None:
    if halts and not switch.engaged():
        switch.engage(
            "; ".join(h.trigger for h in halts),
            {h.trigger: [str(h.observed), str(h.limit)] for h in halts},
        )


def on_mark(
    state: PortfolioState, event: Event, now_ms: int, mark_budget_ms: int, switch: Switch
) -> PortfolioState:
    new = apply(state, event)
    _latch(halt_triggers(new, now_ms, mark_budget_ms, FROZEN_RISK_POLICY), switch)
    return new


def on_tick(state: PortfolioState, now_ms: int, mark_budget_ms: int, switch: Switch) -> None:
    _latch(halt_triggers(state, now_ms, mark_budget_ms, FROZEN_RISK_POLICY), switch)


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
    mark_budget_ms: int,
    switch: Switch,
    audit: Audit,
    signals_this_bar: tuple[str, ...],
    api_error_breach: bool | None,
) -> Decision:
    halts = halt_triggers(state, now_ms, mark_budget_ms, FROZEN_RISK_POLICY)
    _latch(halts, switch)
    snap = snapshot(state, now_ms, switch, signals_this_bar, api_error_breach)
    proposal = evaluate(signal, snap, facts, qual)
    audit.append("risk_proposal", proposal.model_dump(mode="json"))
    return Decision(proposal, halts)
