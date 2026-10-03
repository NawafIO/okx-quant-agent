"""Reference strategies that exist to test the ENGINE, not to trade.

:class:`RandomEntry` is M2's cost-model sanity check: on real data, random entries must show
NEGATIVE expectancy after costs. If random trading looks profitable, the cost model is wrong.
Its randomness comes only from an injected seed, so a run is reproducible.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from okxq.backtest.engine import StrategyContext
from okxq.backtest.types import Action, OrderIntent


@dataclass
class RandomEntry:
    """Flat -> enter long or short with probability ``p_entry`` per closed bar, stop at
    ``stop_frac`` from the close; exit after ``hold_bars`` bars. No take-profit."""

    seed: int
    p_entry: float = 0.05
    stop_frac: Decimal = Decimal("0.03")
    hold_bars: int = 12
    strategy_id: str = "reference-random-entry"
    _rng: random.Random = field(init=False, repr=False)
    _held: dict[str, int] = field(init=False, default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)  # noqa: S311 - simulation, not cryptography

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        out: list[OrderIntent] = []
        for inst_id in sorted(ctx.closed_now):
            # Draw unconditionally so the random stream does not depend on fills.
            roll, coin = self._rng.random(), self._rng.random()
            if inst_id in ctx.positions:
                self._held[inst_id] = self._held.get(inst_id, 0) + 1
                if self._held[inst_id] >= self.hold_bars:
                    out.append(OrderIntent(inst_id, Action.EXIT))
                continue
            self._held.pop(inst_id, None)
            if roll >= self.p_entry:
                continue
            close = Decimal(repr(ctx.views[inst_id].last_close))
            if coin < 0.5:
                stop = close * (1 - self.stop_frac)
                out.append(OrderIntent(inst_id, Action.ENTER_LONG, stop))
            else:
                stop = close * (1 + self.stop_frac)
                out.append(OrderIntent(inst_id, Action.ENTER_SHORT, stop))
        return out
