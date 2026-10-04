"""Fixed-buffer Wilder ATR(14) (okxq.risk.atr; SZ-2). Exact by construction: the value depends
on the last 257 closed bars only."""

from __future__ import annotations

import random
from decimal import Decimal

import numpy as np
import talib

from okxq.risk.atr import wilder_atr
from okxq.risk.policy import FROZEN_RISK_POLICY as P

D = Decimal


def bars(n: int, seed: int = 3) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    rng = random.Random(seed)  # noqa: S311 - synthetic test bars
    h, lo, c, px = [], [], [], 100.0
    for _ in range(n):
        px *= 1 + rng.gauss(0, 0.01)
        up, dn = abs(rng.gauss(0, 0.006)), abs(rng.gauss(0, 0.006))
        c.append(D(f"{px:.4f}"))
        h.append(D(f"{px * (1 + up):.4f}"))
        lo.append(D(f"{px * (1 - dn):.4f}"))
    return h, lo, c


def test_the_value_depends_on_the_last_257_bars_only() -> None:
    h, lo, c = bars(1000)
    full = wilder_atr(h, lo, c, P)
    tail = wilder_atr(h[-257:], lo[-257:], c[-257:], P)
    assert full is not None and full == tail  # exact, not approximately


def test_it_matches_talib_seeded_on_the_same_buffer() -> None:
    h, lo, c = bars(400, seed=9)
    ours = wilder_atr(h, lo, c, P)
    hh, ll, cc = (np.array([float(x) for x in s[-257:]]) for s in (h, lo, c))
    ref = talib.ATR(hh, ll, cc, timeperiod=14)[-1]
    assert ours is not None and abs(float(ours) / ref - 1) < 1e-9


def test_a_constant_true_range_gives_that_range() -> None:
    n = 257
    h, lo, c = [D(101)] * n, [D(99)] * n, [D(100)] * n
    assert wilder_atr(h, lo, c, P) == D(2)


def test_too_few_bars_or_mismatched_lengths_return_none() -> None:
    h, lo, c = bars(256)
    assert wilder_atr(h, lo, c, P) is None
    h, lo, c = bars(300)
    assert wilder_atr(h, lo[:-1], c, P) is None


def test_any_non_positive_or_non_finite_price_returns_none() -> None:
    for bad in (D(0), D(-1), D("NaN"), D("Infinity")):
        for which in range(3):
            cols = [list(x) for x in bars(260)]
            cols[which][-5] = bad
            assert wilder_atr(cols[0], cols[1], cols[2], P) is None


def test_the_policy_definition_names_the_buffer() -> None:
    assert (P.atr_period, P.atr_buffer_bars) == (14, 257)
    assert "257" in P.atr_definition and "wilder_atr" in P.atr_definition
