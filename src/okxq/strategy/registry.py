"""The M4 strategy registry."""

from okxq.strategy.base import Registry
from okxq.strategy.candidates import keltner_reversion, trend_breakout, vol_compression_breakout

REGISTRY = Registry()
for _spec in (trend_breakout.SPEC, keltner_reversion.SPEC, vol_compression_breakout.SPEC):
    REGISTRY.register(_spec)
