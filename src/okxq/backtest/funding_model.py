"""Deliverable D1 - modelled funding from the mark/index premium, and its validation gate.

Realised funding exists from 2026-06-29 only (venue retention, ``docs/VENUE_FACTS.md`` §2),
yet the walk-forward spans ~6.8 years. This module builds a MODELLED series for the gap and
checks it against the realised overlap before anything may use it.

What is and is not assumed:

* OKX's funding formula is **not** asserted (handover §3.2). The model is a deliberately
  plain, empirically fitted family: ``rate = a * mean_premium + b``, clipped to the range of
  realised rates seen in the fit window. ``mean_premium`` is the mean hourly
  ``(mark_close - index_close) / index_close`` over the settlement's own interval. Whether
  this is good enough is decided by the validation gate below, not by argument.
* Missing hours are quarantined, never filled. A settlement whose interval has under
  ``min_coverage`` of its premium hours gets NO modelled rate, so the engine's funding
  coverage check (A-1) refuses windows that depend on it.
* Historical settlement times are extrapolated backward at the interval MEASURED over the
  realised overlap (fact V-13). That the interval held for years is an assumption, recorded
  in every model's ``assumptions`` (finding A-5).
* Fit and validation are time-ordered and disjoint: the earlier ``fit_fraction`` of the
  overlap fits, the later part validates. Fitting and scoring on the same days would be an
  in-sample score.

The gate (provisional, finalised from the measured error distribution - roadmap M2):
cumulative-drag relative error <= 20% per instrument (an absolute floor replaces the
relative test where realised drag is near zero, since 20% of almost nothing is noise), and
sign agreement on >= ``min_sign_agreement`` of intervals. Failure is a ruling trigger:
**escalate to the Chief Advisor**, do not tune until it passes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise

from okxq.backtest.types import BarSeries, FundingRate
from okxq.data.schema import to_decimal

MODEL_VERSION = "premium-linear-v1"


@dataclass(frozen=True)
class ValidationPolicy:
    fit_fraction: float = 0.6
    max_rel_error: float = 0.20
    #: Below this |cumulative realised drag| the relative test is replaced by an absolute
    #: one at the same magnitude (0.05% of notional over the validation window).
    abs_floor: float = 0.0005
    min_sign_agreement: float = 0.80
    min_coverage: float = 0.75


POLICY = ValidationPolicy()


class FundingVerdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    #: Every SCORED instrument passed, but some could not be scored. Only the scored-and-
    #: passed ones may receive a modelled series (checkpoint-3 finding 4).
    PARTIAL = "PARTIAL"
    ESCALATE = "ESCALATE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def measured_interval_ms(realised: Sequence[FundingRate]) -> int:
    """Median spacing of consecutive realised settlements (fact V-13: per instrument)."""
    if len(realised) < 2:
        raise ValueError("need at least two realised settlements to measure an interval")
    deltas = sorted(b.ts_ms - a.ts_ms for a, b in pairwise(realised))
    return deltas[len(deltas) // 2]


def hourly_premium(mark: BarSeries, index: BarSeries) -> dict[int, float]:
    """``(mark - index) / index`` at each hour both series have. Absent hours stay absent."""
    idx = dict(zip(index.ts_open_ms, index.close, strict=True))
    out: dict[int, float] = {}
    for ts, mc in zip(mark.ts_open_ms, mark.close, strict=True):
        ic = idx.get(ts)
        if ic is not None and ic > 0:
            out[ts] = float((mc - ic) / ic)
    return out


def interval_premium(
    premium: Mapping[int, float], settle_ms: int, interval_ms: int, hour_ms: int, min_cov: float
) -> float | None:
    """Mean premium over the hours whose bars lie inside ``(settle - interval, settle]``."""
    hours = range(settle_ms - interval_ms, settle_ms, hour_ms)
    vals = [premium[h] for h in hours if h in premium]
    if len(vals) < min_cov * len(hours) or not vals:
        return None
    return math.fsum(vals) / len(vals)


@dataclass(frozen=True)
class FundingModel:
    a: float
    b: float
    lo: float
    hi: float
    n_fit: int
    version: str = MODEL_VERSION
    assumptions: tuple[str, ...] = field(
        default=(
            "rate = a * mean interval premium + b, clipped to the fit window's realised range",
            "settlement schedule extrapolated backward at the interval measured on the overlap",
            "premium from mark/index 1h CLOSE; hours missing in either series are skipped",
        )
    )

    def rate(self, mean_premium: float) -> float:
        return min(self.hi, max(self.lo, self.a * mean_premium + self.b))


def fit(pairs: Sequence[tuple[float, float]]) -> FundingModel:
    """Ordinary least squares of realised rate on mean premium, pooled across instruments."""
    if len(pairs) < 3:
        raise ValueError("too few (premium, rate) pairs to fit")
    xs, ys = [p for p, _ in pairs], [r for _, r in pairs]
    mx, my = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    a = math.fsum((x - mx) * (y - my) for x, y in pairs) / sxx if sxx > 0 else 0.0
    return FundingModel(a=a, b=my - a * mx, lo=min(ys), hi=max(ys), n_fit=len(pairs))


def schedule(first_realised_ms: int, interval_ms: int, start_ms: int, end_ms: int) -> list[int]:
    """Settlement times in ``[start, end)`` on the grid anchored at the first realised one."""
    k0 = -((first_realised_ms - start_ms) // interval_ms)
    out, t = [], first_realised_ms + k0 * interval_ms
    while t < end_ms:
        if t >= start_ms:
            out.append(t)
        t += interval_ms
    return out


def model_series(
    model: FundingModel,
    premium: Mapping[int, float],
    settle_times: Sequence[int],
    interval_ms: int,
    *,
    hour_ms: int = 3_600_000,
    policy: ValidationPolicy = POLICY,
) -> list[FundingRate]:
    out = []
    for t in settle_times:
        p = interval_premium(premium, t, interval_ms, hour_ms, policy.min_coverage)
        if p is not None:
            # Quantised to the store's 18 fractional digits at birth: a float repr such as
            # 1.2345678901234567e-05 carries 21 and would be refused by the Parquet schema.
            rate = to_decimal(repr(model.rate(p)))
            if rate is not None:
                out.append(FundingRate(t, rate, modelled=True))
    return out


@dataclass(frozen=True)
class InstrumentValidation:
    inst_id: str
    interval_ms: int
    n_intervals: int
    cum_realised: float
    cum_modelled: float
    rel_error: float | None
    abs_error: float
    used_abs_floor: bool
    sign_agreement: float
    error_quantiles: tuple[float, float, float]  # p10, p50, p90 of (model - realised)
    passed: bool


@dataclass(frozen=True)
class ValidationReport:
    verdict: FundingVerdict
    model: FundingModel | None
    instruments: tuple[InstrumentValidation, ...]
    notes: tuple[str, ...]

    @property
    def validated(self) -> frozenset[str]:
        """Instruments that were scored AND passed - the only ones a model may be written for."""
        if self.verdict not in (FundingVerdict.PASS, FundingVerdict.PARTIAL):
            return frozenset()
        return frozenset(v.inst_id for v in self.instruments if v.passed)


def _quantiles(xs: Sequence[float]) -> tuple[float, float, float]:
    s = sorted(xs)

    def pick(q: float) -> float:
        return s[min(len(s) - 1, int(q * len(s)))]

    return (pick(0.1), pick(0.5), pick(0.9))


def validate(
    data: Mapping[str, tuple[BarSeries, BarSeries, Sequence[FundingRate]]],
    policy: ValidationPolicy = POLICY,
) -> ValidationReport:
    """Fit on the early part of each instrument's realised overlap, score on the late part.

    ``data`` maps inst_id -> (mark 1h, index 1h, realised funding).
    """
    notes: list[str] = []
    fit_pairs: list[tuple[float, float]] = []
    held_out: dict[str, tuple[int, list[tuple[int, float]], dict[int, float]]] = {}
    for inst_id, (mark, index, realised) in sorted(data.items()):
        rs = sorted(realised, key=lambda r: r.ts_ms)
        if len(rs) < 10:
            notes.append(f"{inst_id}: only {len(rs)} realised settlements - skipped")
            continue
        interval = measured_interval_ms(rs)
        prem = hourly_premium(mark, index)
        cut = rs[0].ts_ms + int((rs[-1].ts_ms - rs[0].ts_ms) * policy.fit_fraction)
        late: list[tuple[int, float]] = []
        for r in rs:
            p = interval_premium(prem, r.ts_ms, interval, mark.timeframe_ms, policy.min_coverage)
            if p is None:
                continue
            if r.ts_ms <= cut:
                fit_pairs.append((p, float(r.rate)))
            else:
                late.append((r.ts_ms, float(r.rate)))
        held_out[inst_id] = (interval, late, prem)
    if len(fit_pairs) < 3 or not held_out:
        return ValidationReport(FundingVerdict.INSUFFICIENT_DATA, None, (), tuple(notes))

    model = fit(fit_pairs)
    results = []
    for inst_id, (interval, late, prem) in held_out.items():
        if not late:
            notes.append(f"{inst_id}: no validation intervals with premium coverage")
            continue
        mark = data[inst_id][0]
        modelled = {
            r.ts_ms: float(r.rate)
            for r in model_series(
                model,
                prem,
                [t for t, _ in late],
                interval,
                hour_ms=mark.timeframe_ms,
                policy=policy,
            )
        }
        pairs = [(modelled[t], real) for t, real in late if t in modelled]
        cum_m = math.fsum(m_ for m_, _ in pairs)
        cum_r = math.fsum(r for _, r in pairs)
        abs_err = abs(cum_m - cum_r)
        use_floor = abs(cum_r) < policy.abs_floor
        rel = None if use_floor else abs_err / abs(cum_r)
        agree = sum(1 for m_, r in pairs if (m_ > 0) == (r > 0) or (m_ == 0 and r == 0))
        sign = agree / len(pairs)
        ok_drag = abs_err <= policy.abs_floor if use_floor else (rel or 0.0) <= policy.max_rel_error
        results.append(
            InstrumentValidation(
                inst_id=inst_id,
                interval_ms=interval,
                n_intervals=len(pairs),
                cum_realised=cum_r,
                cum_modelled=cum_m,
                rel_error=rel,
                abs_error=abs_err,
                used_abs_floor=use_floor,
                sign_agreement=sign,
                error_quantiles=_quantiles([m_ - r for m_, r in pairs]),
                passed=ok_drag and sign >= policy.min_sign_agreement,
            )
        )
    if not results:
        return ValidationReport(FundingVerdict.INSUFFICIENT_DATA, model, (), tuple(notes))
    scored = {r.inst_id for r in results}
    if not all(r.passed for r in results):
        verdict = FundingVerdict.ESCALATE
    elif scored != set(data):
        verdict = FundingVerdict.PARTIAL
    else:
        verdict = FundingVerdict.PASS
    return ValidationReport(verdict, model, tuple(results), tuple(notes))
