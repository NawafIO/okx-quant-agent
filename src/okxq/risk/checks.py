"""RC-01..RC-16: one pure function per check (architecture §13.3; M5_DESIGN §4).

Each receives the whole cycle context and returns its own RiskCheckResult with the observed
value and the limit. Missing or malformed input FAILS the check (default deny). The engine
runs every check; none short-circuits another.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from okxq.contracts import RiskCheckResult, Signal
from okxq.phase import live_trading_unlocked
from okxq.risk.inputs import MarketFacts, OpenPosition, PortfolioSnapshot
from okxq.risk.num import count, finite, positive
from okxq.risk.policy import RiskPolicy
from okxq.risk.qualitative import Gate
from okxq.risk.sizing import ZERO, Sized, result


@dataclass(frozen=True)
class Ctx:
    signal: Signal
    snap: PortfolioSnapshot
    facts: MarketFacts
    gate: Gate
    sized: Sized | None
    p: RiskPolicy


def open_risk(positions: object, p: RiskPolicy, cluster: str | None = None) -> Decimal | None:
    """Mark-to-stop risk of open positions, each floored at 0; None if any is malformed or
    (when a cluster is asked for) unmapped."""
    if not isinstance(positions, tuple):
        return None
    total = ZERO
    for pos in positions:
        if not isinstance(pos, OpenPosition) or pos.side not in ("LONG", "SHORT"):
            return None
        q, st, mk = positive(pos.qty_base), positive(pos.stop), positive(pos.mark)
        if q is None or st is None or mk is None:
            return None
        mapped = p.cluster_of(pos.symbol)
        if mapped is None:
            return None
        if cluster is not None and mapped != cluster:
            continue
        move = (mk - st) if pos.side == "LONG" else (st - mk)
        total += max(ZERO, q * move)
    return total


def rc01(c: Ctx) -> RiskCheckResult:
    engaged = c.snap.kill_switch_engaged
    return result("RC-01", engaged is False, detail=f"kill switch engaged={engaged!r}")


def rc02(c: Ctx) -> RiskCheckResult:
    envs = {c.signal.env, c.snap.env, c.facts.env}
    same = len(envs) == 1 and c.facts.symbol == c.signal.symbol
    allowed = c.signal.env != "LIVE" or live_trading_unlocked()
    return result("RC-02", same and allowed, detail=f"envs={sorted(envs)}; phase allows={allowed}")


def rc03(c: Ctx) -> RiskCheckResult:
    now, last = count(c.snap.cycle_ts_ms), count(c.facts.last_bar_close_ts_ms)
    tf = count(c.facts.timeframe_ms)
    if now is None or last is None or not tf:
        return result("RC-03", False, detail="missing cycle time, bar time or timeframe")
    age = Decimal(now - last)
    budget = Decimal(tf + c.p.staleness_grace_s * 1000)
    return result("RC-03", ZERO <= age <= budget, age, budget, "last closed bar age (ms)")


def rc04(c: Ctx) -> RiskCheckResult:
    return result("RC-04", c.facts.tradable is True, detail=f"spec {c.facts.spec_sha}")


def rc05(c: Ctx) -> RiskCheckResult:
    s, equity = c.sized, positive(c.snap.equity)
    if s is None or equity is None:
        return result("RC-05", False, detail="no valid size or equity")
    pct = s.actual_risk / equity
    return result("RC-05", pct <= c.p.max_risk_per_trade, pct, c.p.max_risk_per_trade)


def rc06(c: Ctx) -> RiskCheckResult:
    s, equity, held = c.sized, positive(c.snap.equity), open_risk(c.snap.positions, c.p)
    if s is None or equity is None or held is None:
        return result("RC-06", False, detail="no valid size, or malformed positions/equity")
    heat = (held + s.actual_risk) / equity
    return result("RC-06", heat <= c.p.max_portfolio_heat, heat, c.p.max_portfolio_heat)


def rc07(c: Ctx) -> RiskCheckResult:
    cluster = c.p.cluster_of(c.signal.symbol)
    s, equity = c.sized, positive(c.snap.equity)
    held = open_risk(c.snap.positions, c.p, cluster) if cluster is not None else None
    if s is None or equity is None or held is None:
        return result(
            "RC-07", False, detail=f"no valid size, unmapped symbol or malformed input ({cluster})"
        )
    heat = (held + s.actual_risk) / equity
    return result("RC-07", heat <= c.p.cluster_cap, heat, c.p.cluster_cap, f"cluster {cluster}")


def rc08(c: Ctx) -> RiskCheckResult:
    pos: object = c.snap.positions  # typed as a tuple; garbage must still REJECT
    n = len(pos) + 1 if isinstance(pos, tuple) else None
    if n is None:
        return result("RC-08", False, detail="malformed positions")
    lim = Decimal(c.p.max_positions)
    return result("RC-08", n <= lim, Decimal(n), lim)


def rc09(c: Ctx) -> RiskCheckResult:
    pos: object = c.snap.positions  # typed as tuples; garbage must still REJECT
    seen: object = c.snap.signals_this_bar
    if not isinstance(pos, tuple) or not isinstance(seen, tuple):
        return result("RC-09", False, detail="malformed positions or bar signals")
    held = any(getattr(x, "symbol", None) == c.signal.symbol for x in pos)
    dup = c.signal.symbol in seen
    return result("RC-09", not held and not dup, detail=f"held={held}; duplicate={dup}")


def rc10(c: Ctx) -> RiskCheckResult:
    start, eq = positive(c.snap.day_open_equity), finite(c.snap.equity)
    if start is None or eq is None:
        return result("RC-10", False, detail="day-open equity missing: never reset (REJECT)")
    loss = (start - eq) / start
    lim = c.p.daily_loss_limit
    return result("RC-10", loss < lim, loss, lim, "UTC-day loss; >= limit HALTS")


def rc11(c: Ctx) -> RiskCheckResult:
    hwm, eq = positive(c.snap.high_water_mark), finite(c.snap.equity)
    if hwm is None or eq is None or eq > hwm:
        return result("RC-11", False, detail="high-water mark missing or below equity")
    dd = (hwm - eq) / hwm
    lim = c.p.max_dd_halt
    return result("RC-11", dd < lim, dd, lim, "drawdown from HWM; >= limit HALTS")


def rc12a(c: Ctx) -> RiskCheckResult:
    if c.sized is None:
        return result("RC-12a", False, detail="no valid size")
    lim = Decimal(c.p.max_leverage)
    lev = c.sized.leverage
    return result("RC-12a", 1 <= lev <= lim, lev, lim)


def rc12b(c: Ctx) -> RiskCheckResult:
    equity = positive(c.snap.equity)
    if c.sized is None or equity is None:
        return result("RC-12b", False, detail="no valid size or equity")
    lim = equity * c.p.max_leverage
    return result("RC-12b", c.sized.notional <= lim, c.sized.notional, lim)


def rc12c(c: Ctx) -> RiskCheckResult:
    free = finite(c.snap.free_margin)
    if c.sized is None or free is None:
        return result("RC-12c", False, detail="no valid size or free margin")
    margin = c.sized.notional / c.sized.leverage
    lim = free * (1 - c.p.margin_headroom)
    return result("RC-12c", margin <= lim, margin, lim, "margin <= free x (1 - headroom)")


def rc12d(c: Ctx) -> RiskCheckResult:
    mn, lot = positive(c.facts.min_size_base), positive(c.facts.lot_size_base)
    if c.sized is None or mn is None or lot is None:
        return result("RC-12d", False, detail="no valid size or min/lot")
    q = c.sized.qty
    return result("RC-12d", q >= mn and q % lot == 0, q, mn, "qty >= min and on the lot step")


def rc12e(c: Ctx) -> RiskCheckResult:
    mx = positive(c.facts.max_size_base)
    if c.sized is None or mx is None:
        return result("RC-12e", False, detail="no valid size, or max size UNMEASURED")
    return result("RC-12e", c.sized.qty <= mx, c.sized.qty, mx)


def rc12f(c: Ctx) -> RiskCheckResult:
    s = c.sized
    if s is None or s.liquidation is None:
        return result("RC-12f", False, detail="no valid size or liquidation estimate")
    gap = abs(s.entry - s.liquidation)
    need = c.p.liq_buffer_stop_multiple * s.stop_distance
    side_ok = s.liquidation < s.entry if c.signal.side == "LONG" else s.liquidation > s.entry
    return result(
        "RC-12f", side_ok and gap >= need, gap, need, "liquidation distance >= buffer x stop"
    )


def rc13(c: Ctx) -> RiskCheckResult:
    n, now = count(c.snap.consecutive_losses), count(c.snap.cycle_ts_ms)
    if n is None or now is None:
        return result("RC-13", False, detail="malformed loss count or cycle time")
    lim = Decimal(c.p.loss_cooldown_losses)
    if n < lim:
        return result("RC-13", True, Decimal(n), lim)
    last = count(c.snap.last_loss_ts_ms)
    cooling = last is None or now - last < c.p.loss_cooldown_s * 1000
    return result("RC-13", not cooling, Decimal(n), lim, f"cooldown active={cooling}")


def rc14(c: Ctx) -> RiskCheckResult:
    n = count(c.snap.entries_last_hour)
    if n is None:
        return result("RC-14", False, detail="malformed entry count")
    lim = Decimal(c.p.max_entries_per_hour)
    return result("RC-14", n < lim, Decimal(n), lim, "entries in the trailing hour")


def rc15(c: Ctx) -> RiskCheckResult:
    b = c.snap.api_error_breach
    return result("RC-15", b is False, detail=f"API error-rate breach={b!r}")


def rc16(c: Ctx) -> RiskCheckResult:
    return result("RC-16", c.gate.block == "", detail=c.gate.block or "no veto")


#: Check id -> function. Read-only; the engine iterates the PINNED required-check ids, so a
#: missing entry fails rather than disappearing.
BATTERY: Mapping[str, Callable[[Ctx], RiskCheckResult]] = MappingProxyType(
    {
        "RC-01": rc01,
        "RC-02": rc02,
        "RC-03": rc03,
        "RC-04": rc04,
        "RC-05": rc05,
        "RC-06": rc06,
        "RC-07": rc07,
        "RC-08": rc08,
        "RC-09": rc09,
        "RC-10": rc10,
        "RC-11": rc11,
        "RC-12a": rc12a,
        "RC-12b": rc12b,
        "RC-12c": rc12c,
        "RC-12d": rc12d,
        "RC-12e": rc12e,
        "RC-12f": rc12f,
        "RC-13": rc13,
        "RC-14": rc14,
        "RC-15": rc15,
        "RC-16": rc16,
    }
)
