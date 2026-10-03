"""Strategy protocol, bounded history, engine adapter and versioned registry (§10, M4).

Rules enforced here rather than by convention (docs/M4_DESIGN.md §2-3):

* ``generate`` and ``exit`` are PURE functions of a :class:`MarketContext`. Any state a
  strategy needs (bars held) is supplied by the adapter, never kept by the strategy.
* A strategy sees only its last ``warmup_bars`` closed bars (:class:`BoundedView`). Research
  and live therefore feed it identical inputs, and an infinite-memory indicator cannot make
  a backtest disagree with a bounded live buffer (the M3 OBV lesson).
* Entries are :class:`okxq.contracts.Signal`, which cannot be built without a stop-loss and
  validates stop/target geometry against the side.
* Every numeric constant is a declared parameter: ``build`` refuses a params dict that does
  not name every declared parameter, so G-6 perturbs all of them.
* ``version`` hashes every source file of this package and of ``okxq.analysis``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from okxq.backtest.engine import PositionSnapshot, StrategyContext
from okxq.backtest.history import HistoryView
from okxq.backtest.types import Action, OrderIntent
from okxq.contracts import Env, Side, Signal

FloatArray = npt.NDArray[np.float64]
Params = dict[str, Any]

#: Unit conversions, not tunable constants (candidates import these instead of literals).
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS

_PKG = Path(__file__).resolve().parent
_VERSIONED = (_PKG, _PKG.parent / "analysis")


def code_version() -> str:
    """sha256 over every .py file of okxq.strategy and okxq.analysis, in path order."""
    h = hashlib.sha256()
    for root in _VERSIONED:
        for p in sorted(root.rglob("*.py")):
            h.update(str(p.relative_to(_PKG.parent)).encode())
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()


class BoundedView:
    """The last ``n`` closed bars of one instrument, as read-only float64 arrays."""

    __slots__ = ("_cols", "_ts", "inst_id")

    def __init__(self, view: HistoryView, n: int) -> None:
        self.inst_id = view.inst_id
        self._ts = view.ts_open_ms(n)
        self._cols = {
            "open": view.opens(n),
            "high": view.highs(n),
            "low": view.lows(n),
            "close": view.closes(n),
            "volume": view.volumes(n),
        }
        self._ts.setflags(write=False)
        for a in self._cols.values():
            a.setflags(write=False)

    def __len__(self) -> int:
        return len(self._ts)

    @property
    def ts_open_ms(self) -> npt.NDArray[np.int64]:
        return self._ts

    @property
    def open(self) -> FloatArray:
        return self._cols["open"]

    @property
    def high(self) -> FloatArray:
        return self._cols["high"]

    @property
    def low(self) -> FloatArray:
        return self._cols["low"]

    @property
    def close(self) -> FloatArray:
        return self._cols["close"]

    @property
    def volume(self) -> FloatArray:
        return self._cols["volume"]


@dataclass(frozen=True)
class MarketContext:
    """Everything a strategy may read when instrument ``inst_id``'s bar closes at ``ts_ms``."""

    env: Env
    ts_ms: int
    inst_id: str
    timeframe_ms: int
    view: BoundedView
    position: PositionSnapshot | None
    #: Closed bars since the position was first observed; None when flat.
    bars_held: int | None


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def warmup_bars(self) -> int: ...

    @property
    def allowed_regimes(self) -> tuple[str, ...]: ...

    def generate(self, ctx: MarketContext) -> Signal | None: ...

    def exit(self, ctx: MarketContext) -> bool: ...


def dec(x: float) -> Decimal:
    """Float analysis value -> exact Decimal of its shortest repr (contract D-1)."""
    return Decimal(repr(float(x)))


def make_signal(
    ctx: MarketContext,
    strategy: Strategy,
    *,
    side: Side,
    entry_ref: float,
    stop: float,
    target: float,
    features: Mapping[str, str] | None = None,
) -> Signal:
    return Signal(
        env=ctx.env,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        symbol=ctx.inst_id,
        ts=datetime.fromtimestamp(ctx.ts_ms / 1000, tz=UTC),
        side=side,
        entry_ref=dec(entry_ref),
        stop_loss=dec(stop),
        take_profit=(dec(target),),
        conviction=Decimal(1),
        features=dict(features or {}),
    )


@dataclass
class EngineAdapter:
    """Glue to the backtest engine's ``on_bar``. Holds the only state (bars held per
    position); the strategy itself stays pure. Exits are evaluated before entries, and a
    position is never entered and exited on the same bar."""

    strategy: Strategy
    env: Env
    _first_seen: dict[str, int] = field(default_factory=dict, repr=False)

    @property
    def strategy_id(self) -> str:
        return self.strategy.strategy_id

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        out: list[OrderIntent] = []
        n = self.strategy.warmup_bars
        for inst in sorted(ctx.closed_now):
            full = ctx.views[inst]
            if len(full) < n:
                continue
            pos = ctx.positions.get(inst)
            if pos is None:
                self._first_seen.pop(inst, None)
                held = None
            else:
                self._first_seen.setdefault(inst, len(full))
                held = len(full) - self._first_seen[inst]
            ts = full.ts_open_ms(1)
            tf = int(ts[0] - full.ts_open_ms(2)[0]) if len(full) > 1 else 0
            mc = MarketContext(self.env, ctx.ts_ms, inst, tf, BoundedView(full, n), pos, held)
            if pos is not None:
                if self.strategy.exit(mc):
                    out.append(OrderIntent(inst, Action.EXIT, tag="exit"))
                continue
            sig = self.strategy.generate(mc)
            if sig is None:
                continue
            action = Action.ENTER_LONG if sig.side == "LONG" else Action.ENTER_SHORT
            out.append(OrderIntent(inst, action, sig.stop_loss, sig.take_profit[0], tag="entry"))
        return out


@dataclass(frozen=True)
class StrategySpec:
    """A registered strategy family: its declared parameters (every numeric constant, with
    its default) and a constructor. Satisfies ``walkforward.StrategyFactory``."""

    strategy_id: str
    defaults: Mapping[str, int | float]
    construct: Callable[[Params, str], Strategy]
    env: Env = "PAPER"
    version: str = field(default_factory=code_version)

    def build(self, params: Params) -> EngineAdapter:
        if set(params) != set(self.defaults):
            missing = sorted(set(self.defaults) - set(params))
            extra = sorted(set(params) - set(self.defaults))
            raise ValueError(
                f"{self.strategy_id}: params must name every declared parameter "
                f"(missing {missing}, unknown {extra})"
            )
        strategy = self.construct(params, self.version)
        if strategy.allowed_regimes:
            raise ValueError(
                f"{self.strategy_id}: allowed_regimes must be empty until a regime dependency "
                "is declared and the M3 revision tripwire is spent (M3_DESIGN §2)"
            )
        return EngineAdapter(strategy, self.env)

    def grid(self, variations: Sequence[Mapping[str, int | float]]) -> list[Params]:
        """Full params dicts: defaults overlaid with each variation (constants rule)."""
        for v in variations:
            unknown = set(v) - set(self.defaults)
            if unknown:
                raise ValueError(f"{self.strategy_id}: unknown parameters {sorted(unknown)}")
        return [dict(self.defaults) | dict(v) for v in variations]


class Registry:
    """id -> spec. A second registration of an id under a different version is refused,
    so recorded performance cannot be attributed to modified code."""

    def __init__(self) -> None:
        self._specs: dict[str, StrategySpec] = {}

    def register(self, spec: StrategySpec) -> StrategySpec:
        prior = self._specs.get(spec.strategy_id)
        if prior is not None and prior.version != spec.version:
            raise ValueError(
                f"{spec.strategy_id} already registered at version {prior.version[:12]}"
            )
        self._specs[spec.strategy_id] = spec
        return spec

    def __getitem__(self, strategy_id: str) -> StrategySpec:
        return self._specs[strategy_id]

    def ids(self) -> list[str]:
        return sorted(self._specs)
