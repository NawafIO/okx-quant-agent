"""The Risk Engine: Signal + cycle snapshot -> TradeProposal (architecture §13).

``evaluate`` takes no policy, no flag and no option: it binds the pinned policy and
re-verifies its hash on every call. It evaluates EVERY pinned check - iterating the pinned
id set, so a check missing from the battery fails instead of vanishing - records them all,
and approves only when all pass. Any exception inside a check fails that check. The
proposal id is a uuid5 of the canonical inputs and the expiry is cycle time + TTL, so the
engine reads no clock and no randomness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from okxq.contracts import RiskCheckResult, Signal, TradeProposal
from okxq.risk import policy as pol
from okxq.risk.checks import BATTERY, Ctx
from okxq.risk.inputs import MarketFacts, PortfolioSnapshot, QualInputs
from okxq.risk.num import count, positive, risk_context
from okxq.risk.qualitative import Gate, gate
from okxq.risk.sizing import ZERO, Sized, result, size

#: Fixed namespace for proposal ids (uuid5 over the canonical inputs).
PROPOSAL_NAMESPACE = uuid.UUID("6f0b6c8e-2b1a-5d4e-9c3f-0a7e5b2d1c40")


def _proposal_id(signal: Signal, snap: PortfolioSnapshot, facts: MarketFacts) -> uuid.UUID:
    key = "|".join(
        (
            signal.model_dump_json(),
            repr(snap.cycle_ts_ms),
            pol.PINNED_RISK_POLICY_SHA256,
            repr(facts.spec_sha),
        )
    )
    return uuid.uuid5(PROPOSAL_NAMESPACE, key)


def _expiry(signal: Signal, snap: PortfolioSnapshot, ttl_s: int) -> datetime:
    """Cycle time + TTL; an invalid cycle time yields an already-expired proposal."""
    now = count(snap.cycle_ts_ms)
    if now is None:
        return signal.ts
    try:
        return datetime.fromtimestamp(now / 1000, tz=UTC) + timedelta(seconds=ttl_s)
    except (OverflowError, ValueError, OSError):  # out of datetime's range
        return signal.ts


def evaluate(
    signal: Signal, snap: PortfolioSnapshot, facts: MarketFacts, qual: QualInputs
) -> TradeProposal:
    p = pol.FROZEN_RISK_POLICY
    intact = p.sha256() == pol.PINNED_RISK_POLICY_SHA256
    with localcontext(risk_context()):
        try:
            g = gate(signal, snap, qual, p)
        except Exception as exc:  # malformed qualitative input -> RC-16 fails
            g = Gate(Decimal(1), f"qualitative input error: {type(exc).__name__}")
        sized: Sized | None = None
        try:
            sized, sz_results = size(signal, snap, facts, g.multiplier, p)
        except Exception as exc:
            sz_results = tuple(
                result(cid, False, detail=f"sizing error: {type(exc).__name__}")
                for cid in p.required_checks
                if cid.startswith("SZ-")
            )
        done = {r.check_id: r for r in sz_results}
        ctx = Ctx(signal, snap, facts, g, sized, p)
        checks: list[RiskCheckResult] = []
        for cid in p.required_checks:
            r = done.get(cid)
            if r is None:
                fn = BATTERY.get(cid)
                try:
                    r = fn(ctx) if fn is not None else result(cid, False, detail="no check")
                except Exception as exc:
                    r = result(cid, False, detail=f"check error: {type(exc).__name__}")
            if not intact:
                r = result(cid, False, detail="risk policy hash mismatch")
            checks.append(r)
        approved = all(r.passed for r in checks) and sized is not None
        equity = positive(snap.equity)
        common = {
            "proposal_id": _proposal_id(signal, snap, facts),
            "env": signal.env,
            "signal": signal,
            "risk_checks": tuple(checks),
            "expires_at": _expiry(signal, snap, p.proposal_ttl_s),
        }
        if approved and sized is not None and equity is not None:
            return TradeProposal(
                qty_base=sized.qty,
                notional_quote=sized.notional,
                risk_amount=sized.actual_risk,
                risk_pct_of_equity=sized.actual_risk / equity,
                leverage=sized.leverage,
                liquidation_estimate=sized.liquidation,
                verdict="APPROVED",
                **common,
            )
        return TradeProposal(
            qty_base=ZERO,
            notional_quote=ZERO,
            risk_amount=ZERO,
            risk_pct_of_equity=ZERO,
            leverage=ZERO,
            verdict="REJECTED",
            **common,
        )
