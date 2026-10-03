"""The random-entry cost-model check, built so that it cannot pass vacuously.

Chief Advisor rulings (2026-10-03). The first real-data run "passed" on a ruined account;
the rebuilt check then failed with power on a pre-cost mean of -3.6 bps, which an A/B run
traced to the 3% STOP RULE meeting real price dynamics (stops never hit: +0.17 +/- 0.87).
Engine bias is therefore tested only where the truth is known - the martingale invariant in
:mod:`okxq.backtest.martingale` - and on real data this check tests COSTS:

1. **No entry degraded by sizing**: zero refusals for margin or minimum size (fixed notional;
   starting equity sized so expected cumulative cost stays below 25% of it). LIQUIDITY
   outcomes - an entry that found no volume, or a partial fill whose remainder expired - are
   the engine behaving correctly, and are REPORTED on their own line, never dropped
   (ruling 2026-10-03, after a venue halt on 2022-12-18 tripped the original definition).
2. **Power**: >= ``min_trades`` pooled closed trades and a net standard error <=
   ``max_stderr_bps``.
3. **Costs reconcile**: per-trade net = pre-cost - modelled cost, exactly.
4. **Every instrument and side loses** - a mis-signed funding bound or a hole in the spread
   term shows up as a cell with net >= 0.
5. **Impact is live**: slippage per fill rises strictly with order size.
6. **Net expectancy is negative.**

The real-data PRE-COST mean is reported, not gated: it measures the reference strategy's
interaction with the market (a design fact for M4), not the engine.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from okxq.backtest.engine import BacktestResult


@dataclass(frozen=True)
class SanityPolicy:
    min_trades: int = 10_000
    max_stderr_bps: float = 1.0


POLICY = SanityPolicy()


#: Rejections that mean an entry did not trade at its intended size.
#: Refusals that mean SIZING degraded - what the gate exists to catch.
SIZING_REASONS = frozenset({"insufficient_margin"})
#: Liquidity outcomes - correct engine behaviour, reported but not gated.
LIQUIDITY_REASONS = frozenset({"entry_remainder_expired", "entry_no_liquidity"})


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
    #: Reported only; does not decide the verdict.
    gated: bool = True


@dataclass(frozen=True)
class SanityReport:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.gated)


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
    degraded = [
        x
        for r in results
        for x in r.rejections
        if x.reason in SIZING_REASONS or x.reason.startswith("sizer:")
    ]
    liquidity = [x for r in results for x in r.rejections if x.reason in LIQUIDITY_REASONS]
    finals = [r.equity_curve[-1].equity for r in results if r.equity_curve]
    checks.append(
        Check(
            "no entry degraded by sizing",
            not degraded and bool(finals),
            f"{len(degraded)} margin/min-size refusals; final equity "
            f"{min(finals) if finals else 'n/a'} .. {max(finals) if finals else 'n/a'} "
            f"(start {initial_equity})",
        )
    )
    days = sorted(
        {datetime.fromtimestamp(x.ts_ms / 1000, tz=UTC).date().isoformat() for x in liquidity}
    )
    checks.append(
        Check(
            "liquidity outcomes (reported, not gated)",
            True,
            f"{len(liquidity)} entries found no volume or expired partly filled; "
            f"instruments {sorted({x.inst_id for x in liquidity})}; days {days}",
            gated=False,
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
            len(rows) >= policy.min_trades and net_se <= policy.max_stderr_bps,
            f"{len(rows)} trades (>= {policy.min_trades}); net stderr {net_se:.3f} bps "
            f"(<= {policy.max_stderr_bps})",
        )
    )
    checks.append(
        Check(
            "pre-cost mean (reported, not gated)",
            True,
            f"{pre_mu:+.3f} +/- {pre_se:.3f} bps - the reference strategy meeting the market; "
            "engine bias is gated by the martingale invariant instead",
            gated=False,
        )
    )
    gap = net_mu - (pre_mu - cost_mu)
    checks.append(
        Check(
            "costs reconcile",
            abs(gap) < 1e-6,
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
