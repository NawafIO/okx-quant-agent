"""Fixed-fractional sizing and its post-sizing validations (architecture §13.2).

Every input is validated here (default deny). Quantity is floored to the lot - never rounded
up - and an "unreachable" overshoot of the risk budget is an explicit REJECT, not an assert.
Sizing is in BASE units: lot_size_base = lotSz x ctVal, so flooring to it equals flooring in
contracts (M5_DESIGN §11).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from okxq.contracts import RiskCheckResult, Signal
from okxq.risk.inputs import MarketFacts, PortfolioSnapshot
from okxq.risk.num import finite, positive
from okxq.risk.policy import RiskPolicy

ZERO = Decimal(0)


@dataclass(frozen=True)
class Sized:
    entry: Decimal
    stop_distance: Decimal
    risk_capital: Decimal
    qty: Decimal
    actual_risk: Decimal
    notional: Decimal
    leverage: Decimal
    #: None when the maintenance-margin rate is invalid -> RC-12f fails.
    liquidation: Decimal | None


def result(
    cid: str,
    passed: bool,
    observed: Decimal | None = None,
    limit: Decimal | None = None,
    detail: str = "",
) -> RiskCheckResult:
    return RiskCheckResult(
        check_id=cid, passed=passed, observed=observed, limit=limit, detail=detail
    )


def liquidation(entry: Decimal, lev: Decimal, mmr: Decimal, long: bool) -> Decimal:
    """The more conservative (closer to entry) of the exact isolated-margin formula
    (backtest engine) and the linear estimate entry x (1 -+ 1/lev +- mmr)."""
    if long:
        exact = (entry - entry / lev) / (1 - mmr)
        return max(exact, entry * (1 - 1 / lev + mmr))
    exact = (entry + entry / lev) / (1 + mmr)
    return min(exact, entry * (1 + 1 / lev - mmr))


def size(
    signal: Signal,
    snap: PortfolioSnapshot,
    facts: MarketFacts,
    multiplier: Decimal,
    p: RiskPolicy,
) -> tuple[Sized | None, tuple[RiskCheckResult, ...]]:
    entry, stop = positive(signal.entry_ref), positive(signal.stop_loss)
    long = signal.side == "LONG"
    dist = None
    if entry is not None and stop is not None:
        dist = (entry - stop) if long else (stop - entry)
    sz1 = dist is not None and dist > 0
    out = [result("SZ-1", sz1, dist, ZERO, "stop distance on the correct side, > 0")]

    atr = positive(facts.atr_1h)
    atr_floor = p.min_stop_atr * atr if atr is not None else None
    sz2 = dist is not None and sz1 and atr_floor is not None and dist >= atr_floor
    out.append(result("SZ-2", sz2, dist, atr_floor, p.atr_definition))

    tick = positive(facts.tick_size)
    tick_floor = p.min_stop_ticks * tick if tick is not None else None
    sz3 = dist is not None and sz1 and tick_floor is not None and dist >= tick_floor
    out.append(result("SZ-3", sz3, dist, tick_floor, "stop distance >= min ticks"))

    equity, lot, mult = positive(snap.equity), positive(facts.lot_size_base), positive(multiplier)
    capital = qty = actual = None
    if equity is not None and mult is not None:
        capital = equity * min(p.max_risk_per_trade, p.max_risk_per_trade * mult)
    if capital is not None and lot is not None and dist is not None and sz1:
        qty = (capital / dist / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        actual = qty * dist
    sz4 = (
        qty is not None
        and actual is not None
        and capital is not None
        and (qty >= (lot or ZERO) > 0 and actual <= capital)
    )
    out.append(
        result("SZ-4", sz4, actual, capital, "qty >= one lot after flooring; risk <= budget")
    )
    if not (sz1 and sz2 and sz3 and sz4) or None in (entry, dist, capital, qty, actual):
        return None, tuple(out)
    e, d, c, q, a = (Decimal(str(v)) for v in (entry, dist, capital, qty, actual))
    notional = q * e
    free = finite(snap.free_margin)
    budget = free * (1 - p.margin_headroom) if free is not None else ZERO
    lev = Decimal(p.max_leverage)
    for cand in range(1, p.max_leverage + 1):
        if notional / cand <= budget:
            lev = Decimal(cand)
            break
    mmr = finite(facts.mmr)
    liq = liquidation(e, lev, mmr, long) if mmr is not None and 0 <= mmr < 1 else None
    return Sized(e, d, c, q, a, notional, lev, liq), tuple(out)
