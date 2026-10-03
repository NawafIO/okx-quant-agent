"""Quant analysis engine (architecture §9.1) - deterministic statistics on closed bars.

Pure functions; outputs reproducible from stored inputs. Distributional statistics are floats
(their sampling error dwarfs float error). Undefined is NaN or ``None``, never a sentinel.

The correlation matrix and clustering feed the cluster-exposure check RC-07 (M5) and the
R-8 demonstration at M3. Correlations use PAIRWISE-COMPLETE observations on timestamps both
series actually have - nothing is forward-filled to make series line up.

**Everything here is BATCH** (Chief Advisor, M3 checkpoint 1): a descriptive statistic of a
whole sample handed in by the caller. None of it may be consumed by a strategy or by the
regime classifier unless a rolling form exists that is T-1 tested like the TA registry.
:data:`KIND` records this so a later consumer has to change it - and add the test - on purpose.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Arr = npt.NDArray[np.float64]


def log_returns(close: Arr) -> Arr:
    out: Arr = np.diff(np.log(close))
    return out


def realised_vol(returns: Arr, periods_per_year: float) -> float:
    """Annualised sample std of returns; NaN for fewer than 2 observations."""
    if len(returns) < 2:
        return math.nan
    return float(np.std(returns, ddof=1) * math.sqrt(periods_per_year))


def parkinson_vol(high: Arr, low: Arr, periods_per_year: float) -> float:
    """Parkinson (1980) range estimator: sqrt(mean(ln(H/L)^2) / (4 ln 2)), annualised."""
    if len(high) == 0:
        return math.nan
    hl = np.log(high / low)
    return float(math.sqrt(float(np.mean(hl * hl)) / (4 * math.log(2)) * periods_per_year))


def autocorrelation(returns: Arr, lag: int = 1) -> float:
    if len(returns) <= lag + 1:
        return math.nan
    a, b = returns[:-lag], returns[lag:]
    if np.std(a) == 0 or np.std(b) == 0:
        return math.nan
    return float(np.corrcoef(a, b)[0, 1])


def variance_ratio(returns: Arr, q: int) -> float:
    """Lo-MacKinlay VR(q) = Var(q-period overlapping sums) / (q Var(1-period)).

    1 for a random walk; < 1 mean reversion; > 1 trending. NaN when undefined.
    """
    n = len(returns)
    if q < 2 or n < q + 2:
        return math.nan
    var1 = float(np.var(returns, ddof=1))
    if var1 == 0:
        return math.nan
    sums = np.convolve(returns, np.ones(q), mode="valid")
    return float(np.var(sums, ddof=1) / (q * var1))


def hurst_aggregated_variance(returns: Arr, scales: Sequence[int] = (1, 2, 4, 8, 16, 32)) -> float:
    """Hurst exponent by the aggregated-variance method: Var(mean over blocks of m) ~ m^(2H-2).

    0.5 for independent increments; > 0.5 persistent; < 0.5 anti-persistent.
    """
    xs, ys = [], []
    for m in scales:
        k = len(returns) // m
        if k < 8:
            continue
        means = returns[: k * m].reshape(k, m).mean(axis=1)
        v = float(np.var(means, ddof=1))
        if v > 0:
            xs.append(math.log(m))
            ys.append(math.log(v))
    if len(xs) < 3:
        return math.nan
    slope = float(np.polyfit(xs, ys, 1)[0])
    return 1.0 + slope / 2.0


def beta(asset: Arr, market: Arr) -> float:
    if len(asset) != len(market) or len(asset) < 3:
        return math.nan
    var = float(np.var(market, ddof=1))
    if var == 0:
        return math.nan
    return float(np.cov(asset, market, ddof=1)[0, 1] / var)


# --- correlation and clustering ------------------------------------------------------------


@dataclass(frozen=True)
class CorrelationMatrix:
    names: tuple[str, ...]
    rho: Arr  # NaN where a pair has fewer than ``min_overlap`` common observations
    overlap: npt.NDArray[np.int64]


def correlation_matrix(
    returns: Mapping[str, Mapping[int, float]], *, min_overlap: int = 60
) -> CorrelationMatrix:
    """Pearson correlation on the timestamps BOTH series have (pairwise-complete)."""
    names = tuple(sorted(returns))
    k = len(names)
    rho = np.full((k, k), np.nan)
    overlap = np.zeros((k, k), dtype=np.int64)
    for i in range(k):
        for j in range(i, k):
            common = sorted(set(returns[names[i]]) & set(returns[names[j]]))
            overlap[i, j] = overlap[j, i] = len(common)
            if len(common) < min_overlap:
                continue
            a = np.array([returns[names[i]][t] for t in common])
            b = np.array([returns[names[j]][t] for t in common])
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            rho[i, j] = rho[j, i] = 1.0 if i == j else float(np.corrcoef(a, b)[0, 1])
    return CorrelationMatrix(names, rho, overlap)


@dataclass(frozen=True)
class Merge:
    left: frozenset[str]
    right: frozenset[str]
    distance: float


def average_linkage(cm: CorrelationMatrix) -> list[Merge]:
    """Agglomerative clustering, average linkage, distance ``1 - rho``. Deterministic: ties
    break on the sorted member names. Pairs with undefined rho never merge on that pair."""
    clusters: list[frozenset[str]] = [frozenset([n]) for n in cm.names]
    idx = {n: i for i, n in enumerate(cm.names)}

    def dist(a: frozenset[str], b: frozenset[str]) -> float:
        ds = [
            1.0 - cm.rho[idx[x], idx[y]]
            for x in a
            for y in b
            if not math.isnan(cm.rho[idx[x], idx[y]])
        ]
        return sum(ds) / len(ds) if ds else math.inf

    merges: list[Merge] = []
    while len(clusters) > 1:
        best: tuple[float, tuple[str, ...], int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = dist(clusters[i], clusters[j])
                key = (d, tuple(sorted(clusters[i] | clusters[j])), i, j)
                if best is None or key[:2] < best[:2]:
                    best = key
        assert best is not None  # noqa: S101 - at least one pair exists
        d, _, i, j = best
        if math.isinf(d):
            break
        merges.append(Merge(clusters[i], clusters[j], d))
        merged = clusters[i] | clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]
    return merges


def clusters_at(cm: CorrelationMatrix, cut: float) -> list[frozenset[str]]:
    """Clusters formed by every merge at distance <= ``cut``."""
    groups = {n: frozenset([n]) for n in cm.names}
    for m in average_linkage(cm):
        if m.distance > cut:
            break
        merged = m.left | m.right
        for n in merged:
            groups[n] = merged
    return sorted(set(groups.values()), key=lambda g: (-len(g), sorted(g)))


# --- funding --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingStats:
    n: int
    interval_hours: float
    mean_rate: float
    annualised: float
    share_positive: float


def funding_stats(times_ms: Sequence[int], rates: Sequence[float]) -> FundingStats | None:
    """Per-settlement mean, annualised on the MEASURED interval (fact V-13), share positive."""
    if len(rates) < 2:
        return None
    deltas = sorted(b - a for a, b in itertools.pairwise(times_ms))
    interval_h = deltas[len(deltas) // 2] / 3_600_000
    mean = math.fsum(rates) / len(rates)
    return FundingStats(
        n=len(rates),
        interval_hours=interval_h,
        mean_rate=mean,
        annualised=mean * (24 * 365 / interval_h),
        share_positive=sum(1 for r in rates if r > 0) / len(rates),
    )


#: batch = statistic of a caller-supplied sample; NOT a per-bar causal feature.
KIND: dict[str, str] = {
    name: "batch"
    for name in (
        "log_returns",
        "realised_vol",
        "parkinson_vol",
        "autocorrelation",
        "variance_ratio",
        "hurst_aggregated_variance",
        "beta",
        "correlation_matrix",
        "average_linkage",
        "clusters_at",
        "funding_stats",
    )
}
