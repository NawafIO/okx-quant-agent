"""okxq - OKX quantitative multi-agent trading system.

Phase 2 / Milestone M0: scaffold, data contracts, environment decoupling, audit chain.

**ZERO-LIVE-CAPITAL POLICY.** No module in this package may place an order with real
capital. The LIVE environment is locked by four independent layers (architecture §6.4), the
strongest of which is that no trade-permitted LIVE credential is ever provisioned. See
:mod:`okxq.phase`.
"""

from okxq.phase import LIVE_UNLOCK_PHASE, PHASE

__all__ = ["LIVE_UNLOCK_PHASE", "PHASE", "__version__"]

__version__ = "0.1.0"
