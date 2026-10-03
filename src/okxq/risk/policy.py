"""The risk policy: every threshold, frozen and pinned (architecture §13.3; M5_DESIGN §2).

Construction with any other value raises :class:`RiskPolicyTamperError`, and the engine
re-verifies the hash on every call, so no flag, config value or caller can loosen a limit.
Changing a value is a reviewed commit that moves :data:`PINNED_RISK_POLICY_SHA256` and the
guard test's copy of it - never a runtime act.

Values the architecture leaves open are set from first principles, never fitted to strategy
behaviour (Chief Advisor ruling Q1). ``staleness_grace_s`` is UNMEASURED: the venue's bar
publication delay has not been measured; M6 measures it and re-pins before RC-03 ever runs
against a live feed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from okxq.errors import SafetyError

#: Every check a proposal must record, in order. Pinned separately from the engine's battery,
#: so rebinding the battery cannot shrink the set (checkpoint 1, finding 5).
REQUIRED_CHECKS: tuple[str, ...] = (
    "SZ-1",  # stop non-zero and on the correct side
    "SZ-2",  # stop distance >= min_stop_atr x ATR
    "SZ-3",  # stop distance >= min_stop_ticks x tick
    "SZ-4",  # quantity >= one lot after rounding DOWN; actual risk <= risk capital
    "RC-01",  # kill switch disengaged
    "RC-02",  # environment match and phase
    "RC-03",  # market-data freshness
    "RC-04",  # symbol tradable
    "RC-05",  # per-trade risk
    "RC-06",  # portfolio heat
    "RC-07",  # cluster exposure
    "RC-08",  # max concurrent positions
    "RC-09",  # one per symbol, no opposing, no duplicate per bar
    "RC-10",  # daily loss halt
    "RC-11",  # drawdown halt
    "RC-12a",  # leverage <= cap
    "RC-12b",  # notional <= equity x leverage cap
    "RC-12c",  # margin headroom
    "RC-12d",  # min size and lot step
    "RC-12e",  # max size (unmeasured -> REJECT)
    "RC-12f",  # liquidation beyond the stop by the buffer
    "RC-13",  # consecutive-loss cooldown
    "RC-14",  # entries per hour
    "RC-15",  # venue API error-rate breaker
    "RC-16",  # qualitative vetoes and CRISIS block
)

#: Correlation clusters for RC-07 (R-8 E4: crypto co-moves MORE under stress, so every
#: crypto perpetual is one cluster). Inside the pinned policy hash. Unmapped -> REJECT.
CLUSTERS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            (f"{s}-USDT-SWAP", "CRYPTO")
            for s in (
                "BTC",
                "DOGE",
                "ETH",
                "HYPE",
                "NEAR",
                "NIGHT",
                "PEPE",
                "PUMP",
                "SAND",
                "SOL",
                "SUI",
                "TRUMP",
                "UNI",
                "WLD",
                "XRP",
                "ZEC",
            )
        ]
        + [
            ("XAU-USDT-SWAP", "METAL"),
            ("CL-USDT-SWAP", "ENERGY"),
            ("MU-USDT-SWAP", "EQUITY"),
            ("SNDK-USDT-SWAP", "EQUITY"),
        ]
    )
)


class RiskPolicyTamperError(SafetyError):
    """Risk policy differs from the pin."""


@dataclass(frozen=True)
class RiskPolicy:
    # §13.3 defaults
    max_risk_per_trade: Decimal = Decimal("0.005")  # RC-05
    max_portfolio_heat: Decimal = Decimal("0.03")  # RC-06
    cluster_cap: Decimal = Decimal("0.015")  # RC-07
    max_positions: int = 5  # RC-08
    daily_loss_limit: Decimal = Decimal("0.02")  # RC-10: halt when loss >= limit
    max_dd_halt: Decimal = Decimal("0.10")  # RC-11: halt when drawdown >= limit
    max_leverage: int = 3  # RC-12
    # §13.2 / §13.3 values the architecture leaves open (first principles; M5_DESIGN §2)
    min_stop_atr: Decimal = Decimal("0.5")  # stop >= half an hourly ATR(14, Wilder)
    atr_definition: str = "Wilder ATR(14) on closed 1h bars"
    min_stop_ticks: int = 10  # stop >= 10 ticks (tick size itself is measured)
    liq_buffer_stop_multiple: Decimal = Decimal("1.5")  # liq distance >= 1.5 x stop distance
    margin_headroom: Decimal = Decimal("0.25")  # >= 25% of free margin left after the order
    staleness_grace_s: int = 120  # UNMEASURED - M6 measures publication delay and re-pins
    max_entries_per_hour: int = 3
    loss_cooldown_losses: int = 3
    loss_cooldown_s: int = 86_400
    proposal_ttl_s: int = 60
    entry_price_tolerance: Decimal = Decimal("0.002")  # §19.2 control 5 re-validation
    regime_label_max_age_s: int = 90_000  # one daily bar plus one hour
    # §13.4
    qual_disagree_multiplier: Decimal = Decimal("0.5")
    sentiment_veto_score: Decimal = Decimal("0.6")
    sentiment_veto_confidence: Decimal = Decimal("0.7")
    day_boundary: str = "UTC 00:00"
    clusters: tuple[tuple[str, str], ...] = CLUSTERS
    required_checks: tuple[str, ...] = REQUIRED_CHECKS

    def __post_init__(self) -> None:
        if self.sha256() != PINNED_RISK_POLICY_SHA256:
            raise RiskPolicyTamperError(f"risk policy differs from the pin (got {self.sha256()})")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def cluster_of(self, symbol: str) -> str | None:
        return dict(self.clusters).get(symbol)


#: SHA-256 of RiskPolicy().canonical_json().
PINNED_RISK_POLICY_SHA256 = "b7bcb28b382b82d2f24d39a4871ede355d759907ab0a079729ff83f739a5a376"


FROZEN_RISK_POLICY = RiskPolicy()
