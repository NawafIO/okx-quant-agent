"""The M5 risk limits inside a backtest (cycle 2, owner decision D3; docs/CYCLE2_DESIGN.md §2).

**The same M5 functions, not a copy.**
- A ``PortfolioState`` is driven with ``okxq.risk.portfolio.apply`` from the engine's fills.
- Halts come from ``cycle.halt_triggers``, evaluated after EVERY applied event.
- The snapshot comes from ``cycle.snapshot``, sizing from ``risk.sizing.size``, the qualitative
  gate from ``risk.qualitative.gate``, and each applicable check from ``checks.BATTERY``.

**Check accounting.**
- ``APPLIED``, ``NOT_APPLICABLE`` and the SZ checks together equal ``REQUIRED_CHECKS``,
  disjoint (guard test). The four not-applicable checks are named, with the reason, on every
  report; none is silently passed.

**Persistence: none.** This module writes no file and touches no store, kill switch or audit
chain (guards). Its latch is in memory and lives for one backtest:
- RC-11 drawdown and a non-positive equity basis latch PERMANENTLY;
- RC-10 day loss, a crossed stop and stale marks latch until the first 00:00 UTC at least
  24 h after the halt, standing in for the human review a live latch requires (advisor
  ruling).

**Modes.**
- ``ENFORCE`` is the only mode research trials may use.
- ``OBSERVE_HALTS`` exists for the signal-free baseline alone. It records halts and the RC-01 /
  RC-10 / RC-11 failures without blocking, because under ENFORCE a zero-edge baseline latches
  RC-11 early and stops measuring cost per trade. The mode is a constructor argument only,
  and it is stamped on every report.

**Engine mapping (advisor-reviewed).**
- Requires ``entry_ttl_bars == 1``, so ``Opened`` is the single entry fill.
- The portfolio keeps the full quantity until the engine position is fully closed.
- ``Closed`` uses the effective price ``entry + sign * gross / qty`` (a liquidation included).
  Its fee is the exit fees minus any funding the engine attributed to the closed lifecycle
  within the closing instant.
- Only a gap settlement's timestamp is clamped to the state's last; any other backwards event
  raises. Funding attributed to a lifecycle in its closing instant is booked as funding before
  ``Closed`` (it never decides a win or loss for RC-13, as in M5).

**Known deviations from live (recorded, not hidden).**
- The temporary latch SLIDES: halts are re-evaluated on every event, so it ends at the first
  00:00 UTC at least 24 h after the LAST event at which the condition held. Live engages once
  and waits for a human disarm. Harsher than live, accepted (advisor).
- No ``entry_price_tolerance`` re-check at the fill: on continuous 1h bars the next open is
  about the decision close, so it is immaterial (advisor).
- The day boundary: the first event stamped 00:00 is a mark, so the hour 23:00-24:00 counts
  toward the new day - a shift of at most one bar (advisor ruling).
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast, get_args

from okxq.backtest.sizing import SizeDecision
from okxq.backtest.types import Side as EngineSide
from okxq.contracts import Regime, RegimeLabel, RiskCheckResult, Side, Signal
from okxq.risk.atr import wilder_atr
from okxq.risk.checks import BATTERY, Ctx
from okxq.risk.cycle import Halt, halt_triggers, snapshot
from okxq.risk.inputs import MarketFacts, OpenPosition, QualInputs
from okxq.risk.num import risk_context
from okxq.risk.policy import FROZEN_RISK_POLICY, PINNED_RISK_POLICY_SHA256, REQUIRED_CHECKS
from okxq.risk.portfolio import (
    Closed,
    Event,
    FundingAccrued,
    Init,
    MarkUpdate,
    Opened,
    PortfolioError,
    PortfolioState,
    apply,
    initial,
)
from okxq.risk.qualitative import gate as qual_gate
from okxq.risk.sizing import size

DAY_MS = 86_400_000
ONE = Decimal(1)
ZERO = Decimal(0)

#: Checks with no meaning in a backtest, with the reason. Never evaluated, never "passed".
NOT_APPLICABLE: Mapping[str, str] = MappingProxyType(
    {
        "RC-02": "no environment or phase in a backtest; research runs as PAPER",
        "RC-03": "the engine refuses any decision without a bar closed at T",
        "RC-04": "historical venue status is unknown (V-14); only listed instruments exist",
        "RC-15": "no venue API in a backtest",
    }
)
SIZING_CHECKS: tuple[str, ...] = tuple(c for c in REQUIRED_CHECKS if c.startswith("SZ-"))
APPLIED: tuple[str, ...] = tuple(
    c for c in REQUIRED_CHECKS if c not in NOT_APPLICABLE and c not in SIZING_CHECKS
)
#: Failures recorded but not enforced in OBSERVE_HALTS.
HALT_CHECKS: frozenset[str] = frozenset({"RC-01", "RC-10", "RC-11"})


def _side(side: EngineSide) -> Side:
    return "LONG" if side is EngineSide.LONG else "SHORT"


class GateMode(StrEnum):
    ENFORCE = "enforce"
    OBSERVE_HALTS = "observe_halts"


@dataclass(frozen=True)
class GateFacts:
    tick_size: Decimal
    lot_size_base: Decimal
    min_size_base: Decimal
    #: maxMktSz x ctVal, MEASURED 2026-10-03 (docs/instrument_specs.raw.json).
    max_size_base: Decimal
    mmr: Decimal


@dataclass(frozen=True)
class DailyLabel:
    """``okxq.analysis.regime.classify`` output for the daily bar opening at ``day_open_ms``.
    Usable from that bar's close (``day_open_ms + DAY_MS``); ``UNDEFINED`` -> no label."""

    day_open_ms: int
    regime: str


@dataclass(frozen=True)
class PendingEntry:
    inst_id: str
    side: EngineSide
    qty: Decimal
    entry_ref: Decimal
    stop: Decimal
    leverage: Decimal


@dataclass
class _Latch:
    """The kill switch for one backtest (cycle.Switch protocol). In memory only."""

    mode: GateMode
    now_ms: int = 0
    permanent: bool = False
    until_ms: int | None = None
    engagements: list[tuple[int, str]] = field(default_factory=list)

    def _would_block(self) -> bool:
        return self.permanent or (self.until_ms is not None and self.now_ms < self.until_ms)

    def engaged(self) -> bool:
        """OBSERVE_HALTS tracks the latch it WOULD hold but never blocks."""
        return self.mode is GateMode.ENFORCE and self._would_block()

    def latched(self) -> bool:
        return self.engaged()

    def sentinel_present(self) -> bool:
        return False

    def engage(self, trigger: str, detail: dict[str, Any]) -> None:
        """Called for EVERY active halt, latched or not, so a permanent halt escalates a
        temporary latch. An engagement is recorded when the latch starts or turns permanent."""
        was = self._would_block()
        if trigger.startswith("RC-11") or trigger.startswith("non-finite"):
            if not self.permanent:
                self.permanent = True
                self.engagements.append((self.now_ms, trigger))
            return
        resume = -(-(self.now_ms + DAY_MS) // DAY_MS) * DAY_MS
        self.until_ms = max(self.until_ms or 0, resume)
        if not was:
            self.engagements.append((self.now_ms, trigger))


@dataclass(frozen=True)
class GateReport:
    mode: str
    policy_sha256: str
    not_applicable: Mapping[str, str]
    evaluated: int
    approved: int
    #: Check id -> number of evaluations in which it failed (several per refusal possible).
    failures: Mapping[str, int]
    #: Check ids that blocked, joined, -> count (the refusal reasons).
    refusals: Mapping[str, int]
    #: Halt trigger -> number of distinct event timestamps (bar opens/closes) at which it held.
    halts: Mapping[str, int]
    engagements: tuple[tuple[int, str], ...]
    #: Calendar year -> entries refused by RC-16 for lack of a regime label.
    no_label_by_year: Mapping[int, int]


class ResearchRiskGate:
    def __init__(
        self,
        *,
        initial_equity: Decimal,
        start_ms: int,
        facts: Mapping[str, GateFacts],
        labels: Mapping[str, Sequence[DailyLabel]],
        spec_sha: str,
        mode: GateMode = GateMode.ENFORCE,
    ) -> None:
        self.mode = GateMode(mode)
        self._p = FROZEN_RISK_POLICY
        self._facts = dict(facts)
        self._labels = {k: sorted(v, key=lambda x: x.day_open_ms) for k, v in labels.items()}
        self._label_keys = {k: [x.day_open_ms for x in v] for k, v in self._labels.items()}
        self._spec_sha = spec_sha
        self._latch = _Latch(self.mode, now_ms=start_ms)
        self._state = initial("PAPER", Init(start_ms, initial_equity))
        self._entry_fee: dict[str, Decimal] = {}
        self._funding_sent: dict[str, Decimal] = {}
        self._evaluated = 0
        self._approved = 0
        self._failures: Counter[str] = Counter()
        self._refusals: Counter[str] = Counter()
        self._halts: Counter[str] = Counter()
        self._halt_seen: dict[str, int] = {}
        self._no_label: Counter[int] = Counter()

    # -- the single path every portfolio event takes ---------------------------------------

    def _apply(self, event: Event) -> None:
        """Apply, then evaluate the halts at once (the B-1 rule: halts run on every event,
        not only when a signal arrives). The ONLY call of ``apply`` in this module (guard)."""
        ts = event.ts_ms
        if isinstance(event, FundingAccrued) and ts < self._state.last_ts_ms:
            # Only a settlement inside a data gap (F < T_open) arrives after other events of
            # the same instant. Any other event going backwards is a bug: apply raises.
            ts = self._state.last_ts_ms
            event = replace(event, ts_ms=ts)
        with localcontext(risk_context()):
            self._state = apply(self._state, event)
            halts = halt_triggers(self._state, ts, self._p)
        self._halt(halts, ts)

    def _halt(self, halts: tuple[Halt, ...], now_ms: int) -> None:
        self._latch.now_ms = max(self._latch.now_ms, now_ms)
        for h in halts:
            key = h.trigger.split(":")[0]
            if self._halt_seen.get(key) != now_ms:  # count distinct timestamps, not events
                self._halt_seen[key] = now_ms
                self._halts[key] += 1
            self._latch.engage(h.trigger, {"observed": str(h.observed), "limit": str(h.limit)})

    # -- engine notifications -------------------------------------------------------------

    def opened(
        self,
        ts: int,
        inst: str,
        side: EngineSide,
        qty: Decimal,
        price: Decimal,
        stop: Decimal,
        leverage: Decimal,
        fee: Decimal,
    ) -> None:
        self._entry_fee[inst] = fee
        self._funding_sent[inst] = ZERO
        self._apply(Opened(ts, inst, _side(side), qty, price, stop, leverage, fee))

    def funding(self, ts: int, inst: str, amount: Decimal) -> None:
        self._funding_sent[inst] = self._funding_sent.get(inst, ZERO) + amount
        self._apply(FundingAccrued(ts, inst, amount))

    def closed(
        self, ts: int, inst: str, gross: Decimal, total_fees: Decimal, total_funding: Decimal
    ) -> None:
        held = self._state.held(inst)
        if held is None:
            raise PortfolioError(f"{inst} closed in the engine but not held")
        sign = ONE if held.side == "LONG" else -ONE
        price = held.entry + sign * gross / held.qty
        exit_fee = total_fees - self._entry_fee.pop(inst)
        unsent = total_funding - self._funding_sent[inst]
        if unsent:
            # Funding the engine attributed to this lifecycle in its closing instant: still
            # held here, so it is booked as funding, NOT folded into the exit fee - as in M5,
            # funding never decides a trade's win/loss for RC-13 (advisor).
            self.funding(ts, inst, unsent)
        self._funding_sent.pop(inst)
        self._apply(Closed(ts, inst, price, exit_fee))

    def marks(self, ts: int, closes: Mapping[str, Decimal]) -> None:
        """Every instrument whose bar closed at ``ts`` - held or not: an unheld mark rolls the
        UTC day so ``day_open_equity`` is current."""
        for inst in sorted(closes):
            self._apply(MarkUpdate(ts, inst, closes[inst]))

    # -- decision -----------------------------------------------------------------------

    def _label(self, inst: str, ts: int) -> RegimeLabel | None:
        keys = self._label_keys.get(inst, [])
        j = bisect.bisect_right(keys, ts - DAY_MS) - 1
        if j < 0:
            return None
        lab = self._labels[inst][j]
        if lab.regime not in get_args(Regime):  # UNDEFINED or anything unexpected
            return None
        return RegimeLabel(
            env="PAPER",
            symbol=inst,
            ts=datetime.fromtimestamp((lab.day_open_ms + DAY_MS) / 1000, tz=UTC),
            regime=cast(Regime, lab.regime),
            confidence=ONE,
            determined_by="RULE",
        )

    def size(
        self,
        *,
        ts: int,
        inst: str,
        side: EngineSide,
        entry_ref: Decimal,
        stop: Decimal,
        take_profit: Decimal | None,
        highs: Sequence[Decimal],
        lows: Sequence[Decimal],
        closes: Sequence[Decimal],
        timeframe_ms: int,
        pending: Sequence[PendingEntry],
    ) -> SizeDecision:
        """Evaluate one entry through the M5 functions. qty 0 with the blocking check ids as
        the reason when anything that is enforced fails."""
        self._evaluated += 1
        self._latch.now_ms = max(self._latch.now_ms, ts)
        f = self._facts.get(inst)
        if f is None or take_profit is None:
            why = "no_facts" if f is None else "no_take_profit"
            self._refusals[why] += 1
            return SizeDecision(ZERO, ONE, why)
        try:
            sig = Signal(
                env="PAPER",
                strategy_id="research-risk-gate",
                strategy_version=PINNED_RISK_POLICY_SHA256[:12],
                symbol=inst,
                ts=datetime.fromtimestamp(ts / 1000, tz=UTC),
                side=_side(side),
                entry_ref=entry_ref,
                stop_loss=stop,
                take_profit=(take_profit,),
                conviction=ONE,
            )
        except ValueError:
            self._refusals["invalid_signal"] += 1
            return SizeDecision(ZERO, ONE, "invalid_signal")
        with localcontext(risk_context()):
            snap = snapshot(self._state, ts, self._latch, tuple(p.inst_id for p in pending), None)
            margin = sum((p.qty * p.entry_ref / p.leverage for p in pending), ZERO)
            snap = replace(
                snap,
                positions=snap.positions
                + tuple(
                    OpenPosition(p.inst_id, _side(p.side), p.qty, p.stop, p.entry_ref)
                    for p in pending
                ),
                entries_last_hour=snap.entries_last_hour + len(pending),
                free_margin=snap.free_margin - margin,
            )
            facts = MarketFacts(
                env="PAPER",
                symbol=inst,
                tick_size=f.tick_size,
                lot_size_base=f.lot_size_base,
                min_size_base=f.min_size_base,
                max_size_base=f.max_size_base,
                mmr=f.mmr,
                atr_1h=wilder_atr(highs, lows, closes, self._p),
                last_bar_close_ts_ms=ts,
                timeframe_ms=timeframe_ms,
                tradable=None,
                spec_sha=self._spec_sha,
            )
            qual = QualInputs(regime=self._label(inst, ts), sentiment=None)
            g = qual_gate(sig, snap, qual, self._p)
            sized, results = size(sig, snap, facts, g.multiplier, self._p)
            ctx = Ctx(sig, snap, facts, g, sized, self._p)
            checks: list[RiskCheckResult] = [*results, *(BATTERY[c](ctx) for c in APPLIED)]
        failed = [r.check_id for r in checks if not r.passed]
        self._failures.update(failed)
        if g.block == "no regime label":
            self._no_label[datetime.fromtimestamp(ts / 1000, tz=UTC).year] += 1
        blocking = [
            c for c in failed if not (self.mode is GateMode.OBSERVE_HALTS and c in HALT_CHECKS)
        ]
        if blocking or sized is None:
            why = ",".join(blocking) or "unsized"
            self._refusals[why] += 1
            return SizeDecision(ZERO, ONE, why)
        self._approved += 1
        return SizeDecision(sized.qty, sized.leverage)

    # -- reporting ----------------------------------------------------------------------

    @property
    def state(self) -> PortfolioState:
        return self._state

    def report(self) -> GateReport:
        return GateReport(
            mode=self.mode.value,
            policy_sha256=PINNED_RISK_POLICY_SHA256,
            not_applicable=NOT_APPLICABLE,
            evaluated=self._evaluated,
            approved=self._approved,
            failures=MappingProxyType(dict(self._failures)),
            refusals=MappingProxyType(dict(self._refusals)),
            halts=MappingProxyType(dict(self._halts)),
            engagements=tuple(self._latch.engagements),
            no_label_by_year=MappingProxyType(dict(self._no_label)),
        )
