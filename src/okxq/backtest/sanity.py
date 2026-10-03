"""The random-entry cost-model check, built so that it cannot pass vacuously.

Chief Advisor ruling (2026-10-03), after the first real-data run "passed" on a ruined
account. Random entries must lose, and the loss must be the modelled costs and nothing else:

1. **No ruin**: every run ends above ``ruin_floor`` x starting equity (fixed notional makes
   ruin near-impossible; the tripwire stays).
2. **Power**: pooled across seeds, >= ``min_trades`` closed trades and a per-trade standard
   error <= ``max_stderr_bps``.
3. **No engine bias**: the PRE-COST per-trade return (gross plus slippage, in bps of
   entry notional) is within ``max_abs_precost_bps`` of zero.
4. **Costs reconcile**: mean net = mean pre-cost - mean modelled cost, so with check 3 the
   loss is the costs within the same tolerance (reported explicitly).
5. **Every instrument and side loses**: a cell with net >= 0 is how a mis-signed funding
   bound or a hole in the spread term would show up.
6. **Impact is live**: mean slippage per fill rises strictly with order size.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from okxq.backtest.engine import BacktestResult


@dataclass(frozen=True)
class SanityPolicy:
    min_trades: int = 10_000
    max_stderr_bps: float = 1.0
    max_abs_precost_bps: float = 2.0
    ruin_floor: float = 0.5


POLICY = SanityPolicy()


@dataclass(frozen=True)
class TradeBps:
    inst_id: str
    side: str
    pre_cost: float
    net: float
    cost: float


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SanityReport:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def trade_bps(result: BacktestResult) -> list[TradeBps]:
    out = []
    for t in result.trades:
        notional = t.max_qty * t.avg_entry
        if notional <= 0:
            continue
        scale = Decimal(10_000) / notional
        out.append(
            TradeBps(
                inst_id=t.inst_id,
                side=str(t.side),
                pre_cost=float((t.gross_pnl + t.slippage_cost) * scale),
                net=float(t.net_pnl * scale),
                cost=float((t.fees + t.slippage_cost - t.funding) * scale),
            )
        )
    return out


def _mean_se(xs: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    mu = math.fsum(xs) / n
    sd = math.sqrt(math.fsum((x - mu) ** 2 for x in xs) / (n - 1)) if n > 1 else math.inf
    return mu, sd / math.sqrt(n)


def evaluate(
    results: Sequence[BacktestResult],
    initial_equity: Decimal,
    size_ladder: Sequence[tuple[Decimal, float]],
    policy: SanityPolicy = POLICY,
) -> SanityReport:
    """``size_ladder``: (notional per trade, mean taker slippage bps per fill), ascending."""
    checks: list[Check] = []
    floor = initial_equity * Decimal(repr(policy.ruin_floor))
    worst = min((r.equity_curve[-1].equity for r in results if r.equity_curve), default=None)
    checks.append(
        Check(
            "no ruin",
            worst is not None and worst >= floor,
            f"lowest final equity {worst} vs floor {floor}",
        )
    )

    rows = [row for r in results for row in trade_bps(r)]
    if len(rows) < 2:
        checks.append(Check("power", False, f"only {len(rows)} trades"))
        return SanityReport(tuple(checks))
    pre_mu, pre_se = _mean_se([x.pre_cost for x in rows])
    net_mu, net_se = _mean_se([x.net for x in rows])
    cost_mu, _ = _mean_se([x.cost for x in rows])
    checks.append(
        Check(
            "power",
            len(rows) >= policy.min_trades and pre_se <= policy.max_stderr_bps,
            f"{len(rows)} trades (>= {policy.min_trades}); pre-cost stderr {pre_se:.3f} bps "
            f"(<= {policy.max_stderr_bps})",
        )
    )
    checks.append(
        Check(
            "no engine bias",
            abs(pre_mu) <= policy.max_abs_precost_bps,
            f"pre-cost mean {pre_mu:+.3f} bps (|.| <= {policy.max_abs_precost_bps})",
        )
    )
    gap = net_mu - (pre_mu - cost_mu)
    checks.append(
        Check(
            "costs reconcile",
            abs(gap) < 1e-6 and abs(net_mu + cost_mu) <= policy.max_abs_precost_bps,
            f"net {net_mu:+.3f} = pre-cost {pre_mu:+.3f} - cost {cost_mu:.3f} bps (gap {gap:.2e})",
        )
    )
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for x in rows:
        cells[(x.inst_id, x.side)].append(x.net)
    bad = {k: math.fsum(v) / len(v) for k, v in cells.items() if math.fsum(v) / len(v) >= 0}
    checks.append(
        Check(
            "every instrument and side loses",
            not bad,
            f"{len(cells)} cells; non-negative: "
            + (", ".join(f"{i} {s} {m:+.2f}bps" for (i, s), m in sorted(bad.items())) or "none"),
        )
    )
    slips = [s for _, s in size_ladder]
    rising = len(slips) >= 2 and all(b > a for a, b in itertools.pairwise(slips))
    checks.append(
        Check(
            "impact is live",
            rising,
            "; ".join(f"${n:,.0f}: {s:.3f} bps" for n, s in size_ladder),
        )
    )
    checks.append(
        Check(
            "net expectancy negative",
            net_mu < 0,
            f"net {net_mu:+.3f} +/- {net_se:.3f} bps per trade",
        )
    )
    return SanityReport(tuple(checks))
