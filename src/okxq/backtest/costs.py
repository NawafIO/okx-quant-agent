"""The research cost configuration - one source of truth (Chief Advisor ruling 2026-10-03).

Research runs, the sanity check and M4 all build their ``EngineConfig`` here, and every
result records :func:`cost_config_sha` so two numbers can only be compared when they were
priced the same way.

Fees: the user supplied 0.08% / 0.10% as "VIP0". Measured 2026-10-03, those are the
REGULAR-USER **SPOT** rates on OKX's en-gb fee page; perpetual-swap rates were not
measurable from the page. They are therefore a stated assumption: research may use them
(failing a strategy that would have passed is the safe direction), but **no promotion
decision may** - that needs ``Provenance.MEASURED`` perp rates, read from the account's fee
page with a date or from the demo key's trade-fee endpoint at M6.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal

from okxq.backtest.engine import EngineConfig
from okxq.backtest.types import CostStress, FeeSchedule, Provenance, SlippageModel

RESEARCH_SLIPPAGE = SlippageModel(
    y_impact=Decimal(1),
    vol_lookback=24,
    spread_lookback=168,
    # k = 0.2: measured overshoot ~14.9 bps per stop exit on a fat-tailed martingale at 48
    # sub-steps, against sigma_1h = 80 bps -> 0.19, rounded UP. Chosen from that measurement
    # and frozen; verified by scripts/verify_engine_martingale.py, never tuned on real data.
    stop_overshoot_k=Decimal("0.2"),
    assumption_id=(
        "slip-v2: impact Y=1 UNCALIBRATED (square-root law on previous-bar volume); "
        "half-spread Abdi-Ranaldo 2017 over 168 closed 1h bars, adverse-only, >= 1 tick; "
        "intrabar stops fill max(1 tick, 0.2 sigma_1h) beyond the level"
    ),
)

#: User-provided; equals OKX's en-gb SPOT regular-user schedule (read 2026-10-03).
FEES_USER_SPOT_SCHEDULE = FeeSchedule(
    maker=Decimal("0.0008"), taker=Decimal("0.001"), provenance=Provenance.UNMEASURED_ASSUMPTION
)
#: MEASURED perpetual-swap fees: read by the owner from the OKX account fee & tier page
#: (Regular user) on 2026-10-03; PAPER audit record 1867e2a2c00c. The same page lists
#: spot 0.08% / 0.10%, confirming FEES_USER_SPOT_SCHEDULE is the SPOT schedule. M4 cycle 1
#: ran on FEES_USER_SPOT_SCHEDULE; adopting this set in research is a Phase-2 cost-model
#: change (all candidates, new trials, new cost sha), not a retroactive correction.
FEES_MEASURED_PERP = FeeSchedule(
    maker=Decimal("0.0002"), taker=Decimal("0.0005"), provenance=Provenance.MEASURED
)
#: Low sensitivity: a check that only passes at inflated fees is a vacuous pass.
FEES_LOW_SENSITIVITY = FeeSchedule(
    maker=Decimal("0.0002"), taker=Decimal("0.0005"), provenance=Provenance.UNMEASURED_ASSUMPTION
)

RESEARCH_PARTICIPATION_CAP = Decimal("0.1")


def research_config(
    fees: FeeSchedule,
    *,
    initial_equity: Decimal = Decimal(100_000),
    stress: CostStress | None = None,
    participation_cap: Decimal = RESEARCH_PARTICIPATION_CAP,
) -> EngineConfig:
    return EngineConfig(
        fees=fees,
        slippage=RESEARCH_SLIPPAGE,
        initial_equity=initial_equity,
        participation_cap=participation_cap,
        stress=stress or CostStress(),
    )


def cost_config_sha(cfg: EngineConfig) -> str:
    payload = {
        "fees": asdict(cfg.fees),
        "slippage": asdict(cfg.slippage),
        "participation_cap": cfg.participation_cap,
        "stress": asdict(cfg.stress),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
