"""Event-driven backtest engine (architecture §11.1-11.2, milestone M2).

Single-threaded and chronological. Time advances over the distinct bar-CLOSE instants ``T`` of
the whole universe; at each ``T`` the engine runs fixed phases (advisor finding A-7: every
bar closing at ``T`` is published before ANY strategy callback at ``T``, so a cross-sectional
strategy never sees a half-updated universe):

1. **Execution**, per instrument whose bar ``[T_open, T)`` just closed, in ``inst_id`` order:

   a. orders signalled at ``T_open`` fill at this bar's OPEN - the earliest legal moment
      (signal on bar n close executes at bar n+1 open). If the bar does not open exactly where
      the signal bar closed, the data has a gap: pending ENTRIES are cancelled, never filled
      across it; pending EXITS still fill, at the gapped price;
   b. intrabar exits from this bar's high/low, pessimistically (see :meth:`_intrabar`);
   c. funding settlements in this bar's window, adversely (see :meth:`_funding`);
   d. forced flat on the instrument's last bar (series end / window end).

2. **Publication**: each such bar is appended to its instrument's history buffer.
3. **Mark-to-market**: one equity point at ``T`` (G-1 is computed on this curve, A-8).
4. **Decision**: ``strategy.on_bar`` once, with right-truncated views; intents are sized and
   queued for the next open.

Everything that prices a fill at ``T_open`` uses only data closed by ``T_open`` (previous bar
volume, past volatility). The engine therefore holds no look-ahead of its own, which is what
makes the future-corruption test meaningful for the engine and not only for the strategy.

Money is ``Decimal`` throughout (rule D-1) under a 38-digit context (fact V-12). No
randomness: identical inputs give bit-identical results (:meth:`BacktestResult.digest`).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, Decimal, localcontext
from types import MappingProxyType
from typing import Any, Protocol

from okxq.backtest.history import HistoryBuffer, HistoryView
from okxq.backtest.metrics import EquityPoint
from okxq.backtest.sizing import Sizer, round_down_to_lot
from okxq.backtest.types import (
    Action,
    BacktestConfigError,
    BarSeries,
    ClosedTrade,
    CostStress,
    FeeSchedule,
    FillRecord,
    FundingEvent,
    FundingRate,
    InstrumentSpec,
    OrderIntent,
    Provenance,
    Rejection,
    Side,
    SlippageModel,
    _TradeAccumulator,
)

ZERO = Decimal(0)
ONE = Decimal(1)

#: Funding coverage tolerance: an internal gap longer than this many intervals means the
#: series is missing settlements, and a backtest over it would under-accrue cost (A-1).
MAX_FUNDING_GAP_INTERVALS = Decimal("1.5")


# --- strategy-facing types ---------------------------------------------------------------


@dataclass(frozen=True)
class PositionSnapshot:
    side: Side
    qty_base: Decimal
    avg_entry: Decimal
    stop: Decimal
    take_profit: Decimal | None


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy may read at decision time ``ts_ms`` - and nothing later."""

    ts_ms: int
    views: Mapping[str, HistoryView]
    closed_now: frozenset[str]
    positions: Mapping[str, PositionSnapshot]
    equity: Decimal


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]: ...


# --- configuration and results -----------------------------------------------------------


@dataclass(frozen=True)
class EngineConfig:
    fees: FeeSchedule
    slippage: SlippageModel
    initial_equity: Decimal
    #: Max fraction of the PREVIOUS bar's base volume one order may fill per bar.
    participation_cap: Decimal = Decimal("0.1")
    #: Bars an unfilled entry remainder stays live before it is cancelled.
    entry_ttl_bars: int = 1
    stress: CostStress = field(default_factory=CostStress)
    #: Permit SYNTHETIC venue parameters. Production research runs leave this False.
    synthetic: bool = False


@dataclass(frozen=True)
class BacktestResult:
    """The complete, deterministic output of one run - the artefact the bit-identical and
    golden-fixture tests hash. Deliberately contains no wall-clock time (finding A-6)."""

    strategy_id: str
    equity_curve: tuple[EquityPoint, ...]
    trades: tuple[ClosedTrade, ...]
    fills: tuple[FillRecord, ...]
    funding: tuple[FundingEvent, ...]
    rejections: tuple[Rejection, ...]
    assumptions: Mapping[str, str]

    def canonical(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "equity_curve": [[p.ts_ms, str(p.equity)] for p in self.equity_curve],
            "trades": [asdict(t) | {"net_pnl": t.net_pnl} for t in self.trades],
            "fills": [asdict(f) for f in self.fills],
            "funding": [asdict(f) for f in self.funding],
            "rejections": [asdict(r) for r in self.rejections],
            "assumptions": dict(sorted(self.assumptions.items())),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), default=str)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def net_pnls(self) -> list[Decimal]:
        return [t.net_pnl for t in self.trades]

    @property
    def equity_values(self) -> list[Decimal]:
        return [p.equity for p in self.equity_curve]


# --- internal state ----------------------------------------------------------------------


@dataclass
class _Position:
    side: Side
    qty: Decimal
    avg_entry: Decimal
    margin: Decimal
    stop: Decimal
    take_profit: Decimal | None
    mmr: Decimal
    acc: _TradeAccumulator

    @property
    def liq_price(self) -> Decimal:
        """Isolated-margin liquidation price: position equity falls to MMR x notional.

        long:  margin + q(p - e) = mmr*q*p  ->  p = (e - margin/q) / (1 - mmr)
        short: margin + q(e - p) = mmr*q*p  ->  p = (e + margin/q) / (1 + mmr)
        """
        per_unit = self.margin / self.qty
        if self.side is Side.LONG:
            return (self.avg_entry - per_unit) / (ONE - self.mmr)
        return (self.avg_entry + per_unit) / (ONE + self.mmr)


@dataclass
class _Pending:
    kind: str  # "entry" | "exit"
    side: Side
    qty: Decimal
    expected_open_ms: int
    reason: str
    stop: Decimal = ZERO
    take_profit: Decimal | None = None
    leverage: Decimal = ONE
    ttl: int = 1
    #: The lifecycle this entry order opened, once its first fill happens.
    acc: _TradeAccumulator | None = None


@dataclass(frozen=True)
class _Snap:
    side: Side | None
    qty: Decimal
    acc: _TradeAccumulator | None


_FLAT = _Snap(None, ZERO, None)


@dataclass
class _Inst:
    series: BarSeries
    spec: InstrumentSpec
    funding: tuple[FundingRate, ...]
    buffer: HistoryBuffer
    idx: int = -1  # index of the last processed bar
    funding_ptr: int = 0
    last_close: Decimal | None = None
    position: _Position | None = None
    pending: list[_Pending] = field(default_factory=list)
    #: Base quantity already filled against this bar's participation budget.
    cap_used: Decimal = ZERO


# --- engine ------------------------------------------------------------------------------


class BacktestEngine:
    def __init__(
        self,
        *,
        series: Mapping[str, BarSeries],
        specs: Mapping[str, InstrumentSpec],
        funding: Mapping[str, Sequence[FundingRate]],
        config: EngineConfig,
        sizer: Sizer,
        trade_start_ms: int,
        end_ms: int,
    ) -> None:
        if trade_start_ms >= end_ms:
            raise BacktestConfigError("empty backtest window")
        self._cfg = config
        self._sizer = sizer
        self._start = trade_start_ms
        self._end = end_ms
        self._check_provenance(specs)
        if len({s.timeframe_ms for s in series.values()}) > 1:
            raise BacktestConfigError(
                "one timeframe per run: with mixed timeframes, bars closing at the same T open "
                "at different instants and the cross-instrument execution order breaks"
            )
        self._inst: dict[str, _Inst] = {}
        for inst_id in sorted(series):
            s = series[inst_id]
            if inst_id not in specs:
                raise BacktestConfigError(f"{inst_id}: no InstrumentSpec")
            rates = tuple(sorted(funding.get(inst_id, ()), key=lambda r: r.ts_ms))
            self._check_funding_coverage(s, rates)
            self._inst[inst_id] = _Inst(s, specs[inst_id], rates, HistoryBuffer(inst_id, len(s)))
        self._cash = config.initial_equity
        self._fills: list[FillRecord] = []
        self._funding_events: list[FundingEvent] = []
        self._closed: list[tuple[str, _TradeAccumulator, int]] = []
        self._rejections: list[Rejection] = []
        self._curve: list[EquityPoint] = []

    # -- input validation ----------------------------------------------------------------

    def _check_provenance(self, specs: Mapping[str, InstrumentSpec]) -> None:
        allowed = {Provenance.MEASURED} | ({Provenance.SYNTHETIC} if self._cfg.synthetic else set())
        sources = [(f"spec {k}", v.provenance) for k, v in specs.items()]
        sources.append(("fee schedule", self._cfg.fees.provenance))
        for name, prov in sources:
            if prov not in allowed:
                raise BacktestConfigError(
                    f"{name} has provenance {prov}; refusing to backtest on venue parameters "
                    f"that were not measured (synthetic mode: {self._cfg.synthetic})"
                )

    def _tradable_bars(self, s: BarSeries) -> list[int]:
        return [
            t
            for t in s.ts_open_ms
            if t >= self._start - s.timeframe_ms and t + s.timeframe_ms <= self._end
        ]

    def _check_funding_coverage(self, s: BarSeries, rates: tuple[FundingRate, ...]) -> None:
        """Refuse a window the funding series does not cover (advisor finding A-1).

        A missing settlement would accrue zero - a silent cost understatement in the
        profitable direction. The interval is inferred from the series itself (fact V-13),
        never assumed.
        """
        bars = self._tradable_bars(s)
        if not bars:
            return
        first_t, last_t = max(bars[0], self._start), bars[-1] + s.timeframe_ms
        if len(rates) < 2:
            raise BacktestConfigError(f"{s.inst_id}: no funding series to accrue against")
        deltas = sorted(b.ts_ms - a.ts_ms for a, b in itertools.pairwise(rates))
        interval = deltas[len(deltas) // 2]
        limit = MAX_FUNDING_GAP_INTERVALS * interval
        if rates[0].ts_ms > first_t + interval or rates[-1].ts_ms < last_t - interval:
            raise BacktestConfigError(
                f"{s.inst_id}: funding covers {rates[0].ts_ms}..{rates[-1].ts_ms}, "
                f"window needs {first_t}..{last_t}"
            )
        for a, b in itertools.pairwise(rates):
            if a.ts_ms < last_t and b.ts_ms > first_t and b.ts_ms - a.ts_ms > limit:
                raise BacktestConfigError(
                    f"{s.inst_id}: funding gap {a.ts_ms}..{b.ts_ms} exceeds "
                    f"{MAX_FUNDING_GAP_INTERVALS} x the {interval} ms interval"
                )

    # -- main loop -----------------------------------------------------------------------

    def run(self, strategy: Strategy) -> BacktestResult:
        with localcontext() as ctx:
            ctx.prec = 38
            return self._run(strategy)

    def _run(self, strategy: Strategy) -> BacktestResult:
        timeline: dict[int, list[str]] = {}
        for inst_id, st in self._inst.items():
            tf = st.series.timeframe_ms
            for t_open in st.series.ts_open_ms:
                if t_open + tf <= self._end:
                    timeline.setdefault(t_open + tf, []).append(inst_id)
        last_bar_close = {
            inst_id: max((t for t, ids in timeline.items() if inst_id in ids), default=None)
            for inst_id in self._inst
        }

        for t_close in sorted(timeline):
            closed = sorted(timeline[t_close])
            states = [self._inst[k] for k in closed]
            for st in states:
                st.idx += 1
            if t_close > self._start:
                self._execute_instant(states, t_close, last_bar_close)
            # Publication strictly AFTER every instrument's execution at T (Chief Advisor
            # checkpoint-3 finding 1): no fill at T_open may see a close at T.
            for st in states:
                self._publish(st)
            if t_close < self._start:
                continue
            equity = self._equity()
            self._curve.append(EquityPoint(t_close, equity))
            ctx = StrategyContext(
                ts_ms=t_close,
                views=MappingProxyType(
                    {k: v.buffer.view() for k, v in self._inst.items() if len(v.buffer)}
                ),
                closed_now=frozenset(closed),
                positions=MappingProxyType(self._snapshots()),
                equity=equity,
            )
            for intent in strategy.on_bar(ctx):
                self._accept(intent, t_close, frozenset(closed), equity)

        return BacktestResult(
            strategy_id=strategy.strategy_id,
            equity_curve=tuple(self._curve),
            trades=self._materialise(),
            fills=tuple(self._fills),
            funding=tuple(self._funding_events),
            rejections=tuple(self._rejections),
            assumptions=MappingProxyType(self._assumptions()),
        )

    def _assumptions(self) -> dict[str, str]:
        c = self._cfg
        return {
            "sizer": self._sizer.sizer_id,
            "slippage": f"{c.slippage.assumption_id} k_vol={c.slippage.k_vol} "
            f"k_impact={c.slippage.k_impact} lookback={c.slippage.vol_lookback}",
            "fees": f"maker={c.fees.maker} taker={c.fees.taker} ({c.fees.provenance})",
            "stress": f"fees={c.stress.fees} slippage={c.stress.slippage} "
            f"funding_paid={c.stress.funding_paid} funding_received={c.stress.funding_received}",
            "participation_cap": str(c.participation_cap),
            "funding_bound": "funding-bound-v1 where no realised or validated modelled rate "
            "exists: longs pay the 95-day max realised rate, shorts pay -min, never a receipt. "
            "Derived from a 95-day low-funding regime; NOT a bound on 2020-2021 funding; "
            "long-biased exposure in 2021 is under-costed by an unknown amount",
            "funding_boundary": "adverse: a settlement coinciding with an entry/exit is charged "
            "if it is a cost to either side of the boundary, credited only if a receipt to both",
            "funding_notional_price": "bar open at the settlement (last close inside a data gap); "
            "last price, not mark - mark 1h exists for 5 instruments only",
            "liquidation": "isolated margin, whole margin lost, triggered on last-price high/low",
            "intrabar_order": "stop before target in the same bar; along the price path from "
            "the open the nearer of stop/liquidation triggers first; take-profit needs a strict "
            "trade-through and never fills at a better gapped price",
            "mtm_stale_marks": "an instrument without a bar at T is marked at its last close",
        }

    def _publish(self, st: _Inst) -> None:
        s, i = st.series, st.idx
        st.buffer.append(
            s.ts_open_ms[i],
            float(s.open[i]),
            float(s.high[i]),
            float(s.low[i]),
            float(s.close[i]),
            float(s.volume_base[i]),
        )
        st.last_close = s.close[i]

    def _equity(self) -> Decimal:
        unreal = ZERO
        for st in self._inst.values():
            p = st.position
            if p is not None and st.last_close is not None:
                unreal += p.side.sign * p.qty * (st.last_close - p.avg_entry)
        return self._cash + unreal

    def _snapshots(self) -> dict[str, PositionSnapshot]:
        return {
            k: PositionSnapshot(p.side, p.qty, p.avg_entry, p.stop, p.take_profit)
            for k, st in self._inst.items()
            if (p := st.position) is not None
        }

    # -- decision -> pending order ----------------------------------------------------------

    def _reject(self, ts: int, inst_id: str, reason: str) -> None:
        self._rejections.append(Rejection(ts, inst_id, reason))

    def _accept(
        self, intent: OrderIntent, ts: int, closed: frozenset[str], equity: Decimal
    ) -> None:
        st = self._inst.get(intent.inst_id)
        if st is None:
            self._reject(ts, intent.inst_id, "unknown_instrument")
            return
        if intent.inst_id not in closed:
            self._reject(ts, intent.inst_id, "no_bar_closed_at_decision_time")
            return
        if intent.action is Action.EXIT:
            if st.position is None:
                self._reject(ts, intent.inst_id, "exit_without_position")
                return
            st.pending.append(_Pending("exit", st.position.side, st.position.qty, ts, "signal"))
            return

        side = Side.LONG if intent.action is Action.ENTER_LONG else Side.SHORT
        ref = st.series.close[st.idx]
        stop, tp = intent.stop_price, intent.take_profit
        if stop is None:
            self._reject(ts, intent.inst_id, "entry_without_stop")
            return
        if (ref - stop) * side.sign <= 0:
            self._reject(ts, intent.inst_id, "stop_on_wrong_side")
            return
        if tp is not None and (tp - ref) * side.sign <= 0:
            self._reject(ts, intent.inst_id, "take_profit_on_wrong_side")
            return
        # No reversal or re-entry in one step (checkpoint-3 finding 9): with a capped exit
        # the new entry would merge into the old position and inherit its stop.
        if st.position is not None:
            self._reject(ts, intent.inst_id, "already_positioned")
            return
        if any(p.kind == "entry" for p in st.pending):
            self._reject(ts, intent.inst_id, "entry_already_pending")
            return
        decision = self._sizer.size(
            equity=equity, entry_ref=ref, stop=stop, side=side, spec=st.spec
        )
        if decision.qty_base <= 0:
            self._reject(ts, intent.inst_id, f"sizer:{decision.reason}")
            return
        st.pending.append(
            _Pending(
                "entry",
                side,
                decision.qty_base,
                ts,
                intent.tag or "signal",
                stop=stop,
                take_profit=tp,
                leverage=decision.leverage,
                ttl=self._cfg.entry_ttl_bars,
            )
        )

    # -- execution ------------------------------------------------------------------------

    def _snap(self, st: _Inst) -> _Snap:
        p = st.position
        return _FLAT if p is None else _Snap(p.side, p.qty, p.acc)

    def _execute_instant(
        self, states: list[_Inst], t_close: int, last_bar_close: dict[str, int | None]
    ) -> None:
        """Execute every bar closing at ``t_close`` in time order ACROSS instruments.

        All open fills (at T_open) happen before any instrument's intrabar event (inside
        ``[T_open, T)``). Otherwise an entry on B at T_open could be margin-checked against
        A's intrabar exit or A's close at T - look-ahead that also made results depend on
        inst_id ordering (checkpoint-3 finding 1). Simultaneous entries at T_open are still
        served in inst_id order when margin is short: that is capacity allocation at one
        instant, not look-ahead, and it is deterministic.
        """
        snaps: dict[str, tuple[_Snap, _Snap]] = {}
        for st in states:  # phase a: T_open
            st.cap_used = ZERO
            before = self._snap(st)
            self._fill_pending_at_open(st)
            snaps[st.series.inst_id] = (before, self._snap(st))
        for st in states:  # phase b: inside the bar
            self._intrabar(st)
        for st in states:  # phase c/d: settlements, then end-of-series
            before, after_open = snaps[st.series.inst_id]
            self._funding(st, before, after_open, self._snap(st))
            if t_close == last_bar_close[st.series.inst_id]:
                self._final_exit(st)

    def _final_exit(self, st: _Inst) -> None:
        s, i = st.series, st.idx
        t_close = s.ts_open_ms[i] + s.timeframe_ms
        st.pending.clear()
        if st.position is not None:
            reason = "window_end" if t_close >= self._end else "series_end"
            self._market_exit(st, st.position.qty, s.close[i], t_close, reason, cap=False)

    def _prev_volume(self, st: _Inst) -> Decimal:
        return st.series.volume_base[st.idx - 1] if st.idx > 0 else ZERO

    def _cap(self, st: _Inst) -> Decimal:
        """Remaining participation budget for this bar - shared by every taker and maker fill
        on the instrument in the bar, so a residual exit and a fresh stop cannot both spend
        the full budget."""
        return max(ZERO, self._cfg.participation_cap * self._prev_volume(st) - st.cap_used)

    def _slip(self, st: _Inst, price: Decimal, qty: Decimal) -> Decimal:
        """Adverse price offset for a taker fill at ``st.idx``, from data closed before it."""
        m = self._cfg.slippage
        closes = st.series.close[max(0, st.idx - m.vol_lookback - 1) : st.idx]
        rets = [math.log(float(b) / float(a)) for a, b in itertools.pairwise(closes)]
        if len(rets) >= 2:
            mu = math.fsum(rets) / len(rets)
            sigma = math.sqrt(math.fsum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        else:
            sigma = 0.0
        prev_vol = self._prev_volume(st)
        participation = float(qty / prev_vol) if prev_vol > 0 else 1.0
        raw = price * (
            m.k_vol * Decimal(repr(sigma)) + m.k_impact * Decimal(repr(math.sqrt(participation)))
        )
        tick = st.spec.tick_size
        ticks = max(ONE, (raw / tick).to_integral_value(rounding=ROUND_CEILING))
        return ticks * tick * self._cfg.stress.slippage

    def _fee(self, notional: Decimal, maker: bool) -> Decimal:
        rate = self._cfg.fees.maker if maker else self._cfg.fees.taker
        return notional * rate * self._cfg.stress.fees

    def _fill_pending_at_open(self, st: _Inst) -> None:
        s, i = st.series, st.idx
        t_open, o = s.ts_open_ms[i], s.open[i]
        keep: list[_Pending] = []
        for order in sorted(st.pending, key=lambda p: p.kind != "exit"):
            contiguous = order.expected_open_ms == t_open
            if order.kind == "exit":
                if st.position is None:
                    continue
                qty = min(order.qty, st.position.qty)
                filled = self._market_exit(st, qty, o, t_open, order.reason, cap=True)
                if filled < qty and st.position is not None:
                    next_open = s.ts_open_ms[i] + s.timeframe_ms
                    keep.append(_Pending("exit", order.side, qty - filled, next_open, order.reason))
                continue
            if not contiguous:
                self._reject(t_open, s.inst_id, "entry_cancelled_data_gap")
                continue
            filled = self._market_entry(st, order, t_open)
            remaining = order.qty - filled
            if remaining > 0 and order.ttl > 1:
                order.qty, order.ttl = remaining, order.ttl - 1
                order.expected_open_ms = t_open + s.timeframe_ms
                keep.append(order)
            elif remaining > 0:
                self._reject(t_open, s.inst_id, "entry_remainder_expired")
        st.pending = keep

    def _market_entry(self, st: _Inst, order: _Pending, ts: int) -> Decimal:
        p = st.position
        if p is not None and (order.acc is None or p.acc is not order.acc):
            # Only the order that opened this position may add to it (its TTL remainder).
            self._reject(ts, st.series.inst_id, "entry_blocked_by_open_position")
            return ZERO
        qty = round_down_to_lot(min(order.qty, self._cap(st)), st.spec.lot_size_base)
        if qty <= 0:
            self._reject(ts, st.series.inst_id, "entry_no_liquidity")
            return ZERO
        o = st.series.open[st.idx]
        slip = self._slip(st, o, qty)
        price = o + order.side.sign * slip
        margin = qty * price / order.leverage
        used = sum((x.position.margin for x in self._inst.values() if x.position), ZERO)
        if used + margin > self._equity_at_open():
            self._reject(ts, st.series.inst_id, "insufficient_margin")
            return ZERO
        fee = self._fee(qty * price, maker=False)
        self._cash -= fee
        st.cap_used += qty
        if p is None:
            acc = _TradeAccumulator(side=order.side, entry_ts_ms=ts)
            p = _Position(
                order.side, ZERO, ZERO, ZERO, order.stop, order.take_profit, st.spec.mmr, acc
            )
            st.position = p
            order.acc = acc
        p.avg_entry = (p.avg_entry * p.qty + price * qty) / (p.qty + qty)
        p.qty += qty
        p.margin += margin
        acc = p.acc
        acc.entry_qty += qty
        acc.entry_value += qty * price
        acc.max_qty = max(acc.max_qty, p.qty)
        acc.fees += fee
        acc.slippage_cost += qty * slip
        self._fills.append(
            FillRecord(
                ts,
                st.series.inst_id,
                order.side,
                qty,
                price,
                fee,
                qty * slip,
                "taker",
                f"entry:{order.reason}",
            )
        )
        return qty

    def _equity_at_open(self) -> Decimal:
        """Equity at T_open. Valid only during the open-fill phase: publication of the bars
        closing at T happens after every instrument's execution, so every mark here is a
        close at or before T_open, and no intrabar event at T has touched cash yet."""
        return self._equity()

    def _reduce(
        self,
        st: _Inst,
        qty: Decimal,
        price: Decimal,
        ts: int,
        *,
        liquidity: str,
        reason: str,
        slip: Decimal = ZERO,
    ) -> None:
        p = st.position
        assert p is not None  # noqa: S101 - internal invariant, callers check
        if liquidity == "liquidation":
            gross, fee = -p.margin, ZERO
        else:
            st.cap_used += qty
            gross = p.side.sign * qty * (price - p.avg_entry)
            fee = self._fee(qty * price, maker=liquidity == "maker")
        self._cash += gross - fee
        acc = p.acc
        acc.exit_qty += qty
        acc.exit_value += qty * price
        acc.gross_pnl += gross
        acc.fees += fee
        acc.slippage_cost += qty * slip
        acc.reasons.append(reason)
        self._fills.append(
            FillRecord(
                ts, st.series.inst_id, p.side, -qty, price, fee, qty * slip, liquidity, reason
            )
        )
        if liquidity == "liquidation" or qty >= p.qty:
            st.position = None
            st.pending = [o for o in st.pending if o.kind != "exit"]
            self._close_trade(st.series.inst_id, acc, ts)
        else:
            p.margin = p.margin * (p.qty - qty) / p.qty
            p.qty -= qty

    def _market_exit(
        self, st: _Inst, qty: Decimal, ref: Decimal, ts: int, reason: str, *, cap: bool
    ) -> Decimal:
        """Taker exit of up to ``qty`` at ``ref`` less slippage. Returns the quantity filled;
        with ``cap`` the fill is limited by the participation cap and the rest stays open."""
        p = st.position
        assert p is not None  # noqa: S101
        if cap:
            qty = min(qty, max(self._cap(st), ZERO))
            if qty <= 0:
                return ZERO
        slip = self._slip(st, ref, qty)
        price = ref - p.side.sign * slip
        self._reduce(st, qty, price, ts, liquidity="taker", reason=reason, slip=slip)
        return qty

    def _intrabar(self, st: _Inst) -> None:
        """Resolve stop / take-profit / liquidation inside bar ``st.idx`` from O/H/L only.

        * gap: an open already beyond the stop (or liquidation price) fills AT THE OPEN;
        * stop and target both inside the range: the stop is assumed first (pessimistic);
        * stop vs liquidation: along a continuous path from the open, the nearer level is
          crossed first; a bar cannot show an intrabar jump, recorded as an assumption;
        * take-profit is a resting maker order: it needs a strict trade-through (A-9) and
          fills at the target, never at a better gapped price.
        """
        p = st.position
        if p is None:
            return
        s, i = st.series, st.idx
        ts = s.ts_open_ms[i]
        o, h, low = s.open[i], s.high[i], s.low[i]
        sign = p.side.sign
        liq = p.liq_price
        # Distance from the open toward the adverse side; negative = already through.
        adverse_extreme = low if p.side is Side.LONG else h

        def through(level: Decimal) -> bool:
            return (adverse_extreme - level) * sign <= 0

        def gapped(level: Decimal) -> bool:
            return (o - level) * sign <= 0

        if gapped(liq) and (liq - p.stop) * sign >= 0:
            self._reduce(st, p.qty, o, ts, liquidity="liquidation", reason="liquidation_gap")
            return
        if gapped(p.stop):
            if gapped(liq):
                self._reduce(st, p.qty, o, ts, liquidity="liquidation", reason="liquidation_gap")
                return
            self._stop_exit(st, o, ts, "stop_gap")
            return
        stop_hit, liq_hit = through(p.stop), through(liq)
        if stop_hit or liq_hit:
            liq_nearer = (liq - p.stop) * sign > 0
            if liq_hit and (liq_nearer or not stop_hit):
                self._reduce(st, p.qty, liq, ts, liquidity="liquidation", reason="liquidation")
            else:
                self._stop_exit(st, p.stop, ts, "stop")
            return
        tp = p.take_profit
        if tp is not None:
            favourable = h if p.side is Side.LONG else low
            if (favourable - tp) * sign > 0:
                qty = min(p.qty, self._cap(st))
                if qty > 0:
                    self._reduce(st, qty, tp, ts, liquidity="maker", reason="take_profit")

    def _stop_exit(self, st: _Inst, ref: Decimal, ts: int, reason: str) -> None:
        p = st.position
        assert p is not None  # noqa: S101
        want = p.qty
        filled = self._market_exit(st, want, ref, ts, reason, cap=True)
        if filled < want and st.position is not None:
            # Liquidity ran out: the residual must still leave, at the next open.
            s = st.series
            st.pending = [o for o in st.pending if o.kind != "entry"]
            st.pending.append(
                _Pending(
                    "exit",
                    p.side,
                    want - filled,
                    s.ts_open_ms[st.idx] + s.timeframe_ms,
                    f"{reason}_residual",
                )
            )
        if st.position is None:
            st.pending = [o for o in st.pending if o.kind != "entry"]

    # -- funding --------------------------------------------------------------------------

    def _funding(self, st: _Inst, before: _Snap, after_open: _Snap, at_close: _Snap) -> None:
        """Accrue every settlement ``F`` with ``F < T_close`` not yet accrued.

        Which position a settlement applies to at an entry/exit boundary is NOT measured
        (advisor finding A-5), so it is resolved adversely: of the candidate positions either
        side of the boundary, the one with the worse cash flow is charged.

        * ``F < T_open`` (inside a data gap): the position held across the gap;
        * ``F == T_open``: worse of before / after the open fills;
        * ``T_open < F < T_close``: worse of after-open / at-close.
        """
        s, i = st.series, st.idx
        t_open, t_close = s.ts_open_ms[i], s.ts_open_ms[i] + s.timeframe_ms
        prev_close = st.last_close if st.last_close is not None else s.open[i]
        while st.funding_ptr < len(st.funding) and st.funding[st.funding_ptr].ts_ms < t_close:
            rate = st.funding[st.funding_ptr]
            st.funding_ptr += 1
            if rate.ts_ms < self._start:
                continue
            if rate.ts_ms < t_open:
                candidates, price = [before], prev_close
            elif rate.ts_ms == t_open:
                candidates, price = [before, after_open], s.open[i]
            else:
                candidates, price = [after_open, at_close], s.open[i]
            flows = [(self._funding_flow(c, price, rate), c) for c in candidates]
            flow, snap = min(flows, key=lambda x: x[0])
            if snap.acc is None:
                continue
            self._cash += flow
            snap.acc.funding += flow
            self._funding_events.append(
                FundingEvent(rate.ts_ms, s.inst_id, rate.rate, rate.modelled, flow, rate.is_bound)
            )

    def _funding_flow(self, snap: _Snap, price: Decimal, rate: FundingRate) -> Decimal:
        """Signed cash flow: longs pay a positive rate, shorts receive it. Stressed adversely.

        A bound settlement is a cost to either side and never a receipt."""
        if snap.side is None:
            return ZERO
        if rate.is_bound:
            cost = rate.bound_long if snap.side is Side.LONG else rate.bound_short
            flow = -snap.qty * price * (cost or ZERO)
        else:
            flow = -snap.side.sign * snap.qty * price * rate.rate
        stress = self._cfg.stress
        return flow * (stress.funding_paid if flow < 0 else stress.funding_received)

    # -- trade lifecycle ------------------------------------------------------------------

    def _close_trade(self, inst_id: str, acc: _TradeAccumulator, ts: int) -> None:
        # Not frozen yet: funding for the bar in which the position closed is accrued after
        # the exit fill (phase c) and still lands on this accumulator.
        self._closed.append((inst_id, acc, ts))

    def _materialise(self) -> tuple[ClosedTrade, ...]:
        trades = []
        for inst_id, acc, ts in self._closed:
            trades.append(
                ClosedTrade(
                    inst_id=inst_id,
                    side=acc.side,
                    entry_ts_ms=acc.entry_ts_ms,
                    exit_ts_ms=ts,
                    max_qty=acc.max_qty,
                    avg_entry=acc.entry_value / acc.entry_qty,
                    avg_exit=acc.exit_value / acc.exit_qty,
                    gross_pnl=acc.gross_pnl,
                    fees=acc.fees,
                    funding=acc.funding,
                    slippage_cost=acc.slippage_cost,
                    exit_reason=acc.reasons[-1],
                )
            )
        return tuple(trades)
