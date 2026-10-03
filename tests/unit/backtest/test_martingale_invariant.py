"""Martingale invariant - smoke version in the unit suite (Chief Advisor ruling 2026-10-03).

The full-power gate (>= ~1M trades, mean net <= +0.5 bps, stderr <= 0.3 bps) runs as its own
CI step: ``scripts/verify_engine_martingale.py``. This smoke test keeps the invariant in every
local run at modest cost: it catches an engine that gives away more than ~4 bps per trade.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.backtest import martingale as mg
from okxq.backtest.costs import RESEARCH_SLIPPAGE


def test_generator_is_a_fat_tailed_price_martingale() -> None:
    b = mg.martingale_bars(20_000, 7)
    closes = [float(c) for c in b.close]
    rets = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False)]
    mean = sum(rets) / len(rets)
    sd = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5
    assert abs(mean) < 3 * sd / len(rets) ** 0.5  # E[p_next / p] = 1
    kurt = sum((r - mean) ** 4 for r in rets) / len(rets) / sd**4
    assert kurt > 3.5  # fatter than normal: jumps exist


def test_a_collapsing_path_is_refused() -> None:
    with pytest.raises(ValueError, match="collapsed"):
        mg.martingale_bars(2_000, 1, sigma_bar=0.2)


@pytest.mark.parametrize("stop", ["0.03", "0.5"])
def test_random_entry_cannot_profit_through_the_engine(stop: str) -> None:
    nets: list[float] = []
    for seed in range(12):
        nets += mg.net_bps(mg.run_once(20_000, seed, RESEARCH_SLIPPAGE, stop_frac=Decimal(stop)))
    r = mg.evaluate(f"stop {stop}", nets)
    assert r.n > 15_000
    assert r.mean_bps <= mg.POLICY.max_mean_bps + 3 * r.stderr_bps, r
