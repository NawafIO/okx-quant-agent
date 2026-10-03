"""TA engine: T-1 no-look-ahead for EVERY registered indicator, T-2 reference checks."""

from __future__ import annotations

import math

import numpy as np
import pytest
import talib
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from okxq.analysis import ta
from okxq.analysis.ta import Bars

step = st.tuples(
    st.floats(min_value=-0.05, max_value=0.05),
    st.floats(min_value=0.0, max_value=0.03),
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1e6),
)


def build(raw: list[tuple[float, float, float, float]]) -> Bars:
    o, h, lo, c, v = [], [], [], [], []
    price = 100.0
    for ret, rng, pos, vol in raw:
        op = price
        cl = max(1e-3, op * (1 + ret))
        hi = max(op, cl) * (1 + rng)
        low = min(op, cl) * (1 - rng * pos)
        o.append(op)
        h.append(hi)
        lo.append(low)
        c.append(cl)
        v.append(vol)
        price = cl
    return Bars.of(o, h, lo, c, v)


def same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


@pytest.mark.parametrize("name", sorted(ta.REGISTRY))
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(raw=st.lists(step, min_size=2, max_size=120), cut=st.integers(min_value=1, max_value=120))
def test_t1_no_look_ahead(
    name: str, raw: list[tuple[float, float, float, float]], cut: int
) -> None:
    """T-1: the value at bar n never changes when later bars arrive."""
    bars = build(raw)
    n = min(cut, len(raw))
    f = ta.REGISTRY[name]
    full = f(bars)
    assert len(full) == len(raw)
    assert same(float(f(bars.head(n))[-1]), float(full[n - 1])), name


def long_bars(seed: int, n: int = 1500) -> Bars:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.005, n)))
    lo = c * (1 - np.abs(rng.normal(0, 0.005, n)))
    return Bars.of(np.concatenate(([100.0], c[:-1])), h, lo, c, rng.uniform(1, 100, n))


@pytest.mark.parametrize("name", sorted(ta.REGISTRY))
def test_t1_holds_on_finite_values(name: str) -> None:
    """Guards against a vacuous T-1 pass: past the warm-up, every comparison is on FINITE
    values, at many cut points of a long series (Chief Advisor, M3 checkpoint 1)."""
    ind = ta.REGISTRY[name]
    b = long_bars(11, 600)
    full = ind(b)
    finite = 0
    for n in range(2, 600, 7):
        head = ind(b.head(n))[-1]
        assert same(float(head), float(full[n - 1])), (name, n)
        finite += int(np.isfinite(head))
    assert finite >= 40, f"{name}: only {finite} finite comparisons"


@pytest.mark.parametrize("name", sorted(ta.REGISTRY))
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_declared_warmup_suffices(name: str, seed: int) -> None:
    """A bounded buffer of min_warmup bars reproduces the full-history value (1e-6 rel)."""
    ind = ta.REGISTRY[name]
    b = long_bars(seed)
    full = float(ind(b)[-1])
    m = ind.min_warmup
    tail = Bars.of(b.open[-m:], b.high[-m:], b.low[-m:], b.close[-m:], b.volume[-m:])
    assert abs(float(ind(tail)[-1]) - full) <= 1e-6 * max(1.0, abs(full)), name


def test_talib_unstable_period_is_pinned() -> None:
    import talib as tl

    assert tl.get_unstable_period("EMA") == ta.TALIB_UNSTABLE_PERIOD  # type: ignore[attr-defined]
    tl.set_unstable_period("EMA", 5)  # type: ignore[attr-defined]
    try:
        with pytest.raises(RuntimeError, match="unstable period"):
            ta.REGISTRY["ema_20"](long_bars(1, 100))
    finally:
        tl.set_unstable_period("EMA", ta.TALIB_UNSTABLE_PERIOD)  # type: ignore[attr-defined]


def test_obv_is_not_registered() -> None:
    """Removed, not patched: its level depends on the series start (warm-up test)."""
    assert "obv" not in ta.REGISTRY


# --- T-2: references -----------------------------------------------------------------------


def ramp(n: int) -> Bars:
    c = np.arange(1.0, n + 1)
    return Bars.of(c, c + 0.5, c - 0.5, c, np.full(n, 10.0))


def test_talib_sma_and_ema_by_hand() -> None:
    b = ramp(10)
    sma = talib.SMA(b.close, timeperiod=5)
    assert np.isnan(sma[:4]).all()
    assert sma[4] == 3.0 and sma[9] == 8.0
    ema = talib.EMA(b.close, timeperiod=3)
    # TA-Lib seeds EMA with the SMA of the first 3 values, then alpha = 2 / (3 + 1).
    expected = [2.0]
    for x in b.close[3:]:
        expected.append(expected[-1] + 0.5 * (x - expected[-1]))
    assert np.allclose(ema[2:], expected, rtol=0, atol=1e-12)


def test_rolling_vwap_matches_a_naive_loop() -> None:
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(size=200))
    b = Bars.of(c, c + 1, c - 1, c, rng.uniform(0, 50, size=200))
    out = ta.rolling_vwap(b, 24)
    for i in range(23, 200):
        tp = (b.high[i - 23 : i + 1] + b.low[i - 23 : i + 1] + b.close[i - 23 : i + 1]) / 3
        w = b.volume[i - 23 : i + 1]
        assert out[i] == pytest.approx(float((tp * w).sum() / w.sum()), rel=1e-9)
    assert np.isnan(out[:23]).all()


def test_rolling_vwap_is_nan_without_volume() -> None:
    b = ramp(30)
    b = Bars.of(b.open, b.high, b.low, b.close, np.zeros(30))
    assert np.isnan(ta.rolling_vwap(b, 5)).all()


def test_keltner_is_ema_plus_minus_k_atr() -> None:
    rng = np.random.default_rng(4)
    c = 100 + np.cumsum(rng.normal(size=120))
    b = Bars.of(c, c + 1, c - 1, c, np.ones(120))
    lo, mid, up = ta.keltner(b, 20, 10, 2.0)
    atr = talib.ATR(b.high, b.low, b.close, timeperiod=10)
    ok = ~np.isnan(mid) & ~np.isnan(atr)  # EMA-20 warms up later than ATR-10
    assert ok.sum() > 90
    assert np.allclose((up - mid)[ok], 2 * atr[ok], rtol=1e-12)
    assert np.allclose((mid - lo)[ok], 2 * atr[ok], rtol=1e-12)
    assert np.isnan(up[~ok]).all()


def test_supertrend_flips_with_the_trend() -> None:
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 100, 60)
    c = np.concatenate([up, down])
    b = Bars.of(c, c + 0.5, c - 0.5, c, np.ones(120))
    line, d = ta.supertrend(b, 10, 3.0)
    assert d[50] == 1.0 and line[50] < c[50]  # rising: line below price
    assert d[115] == -1.0 and line[115] > c[115]  # falling: line above price


# --- T-2: own implementations vs INDEPENDENT pure-Python references (M3 closing audit) -----
# The references below re-derive EMA, Wilder ATR, Keltner and Supertrend from their textbook
# definitions without TA-Lib, so a Keltner or Supertrend bug cannot hide behind the library.


def _ohlc(seed: int, n: int = 300) -> Bars:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    return Bars.of(o, h, lo, c, rng.uniform(1, 10, n))


def _ref_ema(x: list[float], n: int) -> list[float]:
    """Seeded with the simple mean of the first n values (TA-Lib's convention)."""
    out = [math.nan] * len(x)
    out[n - 1] = sum(x[:n]) / n
    a = 2 / (n + 1)
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _ref_atr(h: list[float], lo: list[float], c: list[float], n: int) -> list[float]:
    """Wilder: TR from bar 1; first ATR at bar n is the mean of TR[1..n], then smoothed."""
    tr = [math.nan] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))
    ]
    out = [math.nan] * len(c)
    out[n] = sum(tr[1 : n + 1]) / n
    for i in range(n + 1, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def _ref_supertrend(b: Bars, n: int, k: float) -> tuple[list[float], list[float]]:
    """Final-band formulation; starts in an up-trend (the convention ta.supertrend uses)."""
    h, lo, c = list(b.high), list(b.low), list(b.close)
    atr = _ref_atr(h, lo, c, n)
    fu = fl = math.nan
    st_line, st_dir = [math.nan] * len(c), [math.nan] * len(c)
    up = True
    for i in range(n, len(c)):
        mid = (h[i] + lo[i]) / 2
        bu, bl = mid + k * atr[i], mid - k * atr[i]
        fu = bu if math.isnan(fu) or bu < fu or c[i - 1] > fu else fu
        fl = bl if math.isnan(fl) or bl > fl or c[i - 1] < fl else fl
        up = c[i] >= fl if up else c[i] > fu
        st_dir[i], st_line[i] = (1.0, fl) if up else (-1.0, fu)
    return st_line, st_dir


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_keltner_matches_an_independent_reference(seed: int) -> None:
    b = _ohlc(seed)
    lo, mid, up = ta.keltner(b, 20, 10, 2.0)
    m = np.array(_ref_ema(list(b.close), 20))
    a = np.array(_ref_atr(list(b.high), list(b.low), list(b.close), 10))
    ok = ~np.isnan(m) & ~np.isnan(a)
    np.testing.assert_allclose(mid[ok], m[ok], rtol=1e-9)
    np.testing.assert_allclose(up[ok], (m + 2 * a)[ok], rtol=1e-9)
    np.testing.assert_allclose(lo[ok], (m - 2 * a)[ok], rtol=1e-9)
    assert np.isnan(up[~ok]).all()


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_supertrend_matches_an_independent_reference(seed: int) -> None:
    b = _ohlc(seed)
    line, d = ta.supertrend(b, 10, 3.0)
    ref_line, ref_d = (np.array(x) for x in _ref_supertrend(b, 10, 3.0))
    ok = ~np.isnan(ref_line)
    assert (np.isnan(line) == ~ok).all()
    assert (d[ok] == ref_d[ok]).all()
    assert (np.diff(d[ok]) != 0).sum() >= 3  # the fixture actually flips
    np.testing.assert_allclose(line[ok], ref_line[ok], rtol=1e-9)
