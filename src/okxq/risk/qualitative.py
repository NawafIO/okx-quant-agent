"""Qualitative inputs may only REDUCE risk (architecture §13.4; M5_DESIGN §6).

The only outputs are a multiplier in {1, 0.5} that ``engine`` applies as
``min(base, base x m)``, and a block reason. There is no path by which a qualitative input
raises a size, loosens a stop or a limit. The monotonicity property test is the guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from okxq.contracts import Signal
from okxq.risk.inputs import PortfolioSnapshot, QualInputs
from okxq.risk.policy import RiskPolicy


@dataclass(frozen=True)
class Gate:
    multiplier: Decimal
    #: Empty when nothing blocks the entry.
    block: str


def gate(signal: Signal, snap: PortfolioSnapshot, q: QualInputs, p: RiskPolicy) -> Gate:
    """Default deny: a missing, mismatched or stale deterministic regime label blocks."""
    r = q.regime
    if r is None:
        return Gate(Decimal(1), "no regime label")
    if r.symbol != signal.symbol or r.env != snap.env:
        return Gate(Decimal(1), "regime label for another symbol or env")
    age_s = snap.cycle_ts_ms / 1000 - r.ts.timestamp()
    if not 0 <= age_s <= p.regime_label_max_age_s:
        return Gate(Decimal(1), f"regime label age {age_s:.0f}s outside [0, max]")
    if r.regime == "CRISIS":
        return Gate(Decimal(1), "CRISIS blocks new entries (label validation FAILED, M3)")
    mult = p.qual_disagree_multiplier if r.determined_by == "RULE_LLM_DISAGREE" else Decimal(1)
    s = q.sentiment
    if s is None:
        return Gate(mult, "")
    if s.symbol != signal.symbol or s.env != snap.env:
        return Gate(mult, "sentiment for another symbol or env")
    if s.confidence > p.sentiment_veto_confidence:
        if signal.side == "LONG" and s.score < -p.sentiment_veto_score:
            return Gate(mult, "sentiment veto: LONG")
        if signal.side == "SHORT" and s.score > p.sentiment_veto_score:
            return Gate(mult, "sentiment veto: SHORT")
    return Gate(mult, "")
