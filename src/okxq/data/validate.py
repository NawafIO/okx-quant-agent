"""Market-data validation and gap detection (architecture §12).

**Quarantine, never repair.** Every rule here either accepts a row or sets it aside for
review. Nothing is forward-filled, interpolated, clipped or silently corrected, because a
repaired bar is indistinguishable from a real one downstream - and a backtest that trades
on invented prices produces confident, wrong answers.

Gaps are recorded explicitly rather than filled. A missing bar is information: it means the
venue had an outage, the instrument was halted, or our backfill is incomplete, and the
backtester needs to know which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise

from okxq.data.okx_public import TIMEFRAME_MS, Bar

#: Rows whose high/low spans more than this multiple of the median span are flagged for
#: review. Flagged rows are kept - an outlier is often a real flash crash, which is exactly
#: the event a backtest most needs - but they are reported so a human can look.
OUTLIER_SPAN_MULTIPLE = Decimal(50)

#: Floor for how far beyond "now" a bar's open may sit before it is treated as impossible.
#: Must comfortably exceed plausible host clock error - this machine measured ~199 s of
#: skew against OKX - so that our own clock can never quarantine valid venue data.
MIN_FUTURE_TOLERANCE_MS = 900_000  # 15 minutes


def _future_tolerance_ms(step_ms: int) -> int:
    """Clock-error tolerance for the future-bar check: one bar or the floor, whichever is larger."""
    return max(step_ms, MIN_FUTURE_TOLERANCE_MS)


@dataclass(frozen=True, slots=True)
class Gap:
    """A contiguous run of missing bars on the expected grid."""

    start_ms: int
    end_ms: int
    missing_bars: int


@dataclass(frozen=True, slots=True)
class Rejection:
    """A row set aside, with the reason."""

    ts_open_ms: int
    rule: str
    detail: str


@dataclass(slots=True)
class ValidationReport:
    """Outcome of validating one (instrument, timeframe) batch."""

    accepted: list[Bar] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    outliers: list[int] = field(default_factory=list)
    duplicates_dropped: int = 0

    @property
    def ok(self) -> bool:
        """Whether the batch is free of rejections. Gaps alone do not make it not-ok."""
        return not self.rejected

    @property
    def missing_bars(self) -> int:
        return sum(g.missing_bars for g in self.gaps)

    def summary(self) -> str:
        return (
            f"accepted={len(self.accepted)} rejected={len(self.rejected)} "
            f"gaps={len(self.gaps)} missing_bars={self.missing_bars} "
            f"dups={self.duplicates_dropped} outliers={len(self.outliers)}"
        )


def _ohlc_violation(bar: Bar) -> str | None:
    """Return a reason string if the bar's OHLC is internally inconsistent."""
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(p is None for p in prices):
        return "missing price field"
    if any(p <= 0 for p in prices):
        return f"non-positive price o={bar.open} h={bar.high} l={bar.low} c={bar.close}"
    if bar.high < bar.low:
        return f"high {bar.high} < low {bar.low}"
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        return (
            f"open/close outside high/low range o={bar.open} h={bar.high} l={bar.low} c={bar.close}"
        )
    for name, vol in (
        ("volume_contracts", bar.volume_contracts),
        ("volume_base", bar.volume_base),
        ("volume_quote", bar.volume_quote),
    ):
        if vol is not None and vol < 0:
            return f"negative {name}={vol}"
    return None


def detect_gaps(timestamps: list[int], timeframe: str) -> list[Gap]:
    """Find runs of missing bars against the expected grid.

    Assumes ``timestamps`` is sorted and deduplicated.
    """
    step = TIMEFRAME_MS[timeframe]
    gaps: list[Gap] = []
    for earlier, later in pairwise(timestamps):
        delta = later - earlier
        if delta > step:
            missing = (delta // step) - 1
            if missing > 0:
                gaps.append(
                    Gap(start_ms=earlier + step, end_ms=later - step, missing_bars=int(missing))
                )
    return gaps


def _flag_outliers(bars: list[Bar]) -> list[int]:
    """Flag bars whose range dwarfs the median range. Flagged, not removed."""
    spans = sorted((b.high - b.low) for b in bars if b.high is not None and b.low is not None)
    if len(spans) < 20:
        return []
    median = spans[len(spans) // 2]
    if median <= 0:
        return []
    threshold = median * OUTLIER_SPAN_MULTIPLE
    return [b.ts_open_ms for b in bars if (b.high - b.low) > threshold]


def validate_bars(
    bars: list[Bar],
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> ValidationReport:
    """Validate a batch of bars for one instrument and timeframe.

    Applies, in order: drop unclosed bars; drop duplicate timestamps; enforce the expected
    grid alignment; check OHLC consistency; detect gaps; flag outliers.
    """
    report = ValidationReport()
    step = TIMEFRAME_MS[timeframe]

    seen: dict[int, Bar] = {}
    for bar in bars:
        # Partial bars must never enter the store (architecture §12).
        if not bar.is_closed:
            report.rejected.append(
                Rejection(bar.ts_open_ms, "unclosed_bar", "confirm flag was not 1")
            )
            continue
        # Secondary guard only: it catches a venue returning an unclosed bar without
        # flagging it, or a misparse. The venue's `confirm` flag (checked at ingest) is
        # the authoritative closed-bar signal.
        #
        # The test is on the bar's *open* against a generous tolerance, not on its close
        # against `now`. A strict close-time test loses real data whenever our clock is
        # behind or the reading is stale - which is exactly what happened on the first 5m
        # run (9 valid bars quarantined), and this host measures ~199 s of skew against
        # the venue (VENUE_FACTS §5). A bar that opens well beyond now is unambiguously
        # wrong; one that merely looks recent is not.
        if now_ms is not None and bar.ts_open_ms > now_ms + _future_tolerance_ms(step):
            report.rejected.append(
                Rejection(
                    bar.ts_open_ms,
                    "future_or_incomplete",
                    f"bar opens at {bar.ts_open_ms}, beyond now ({now_ms}) plus "
                    f"{_future_tolerance_ms(step)} ms of clock tolerance",
                )
            )
            continue
        # Bar opens must land on the timeframe grid; an off-grid bar means we have
        # misunderstood the venue's bucketing, which is worth stopping for.
        if bar.ts_open_ms % step != 0:
            report.rejected.append(
                Rejection(bar.ts_open_ms, "off_grid", f"not aligned to {timeframe} grid")
            )
            continue
        reason = _ohlc_violation(bar)
        if reason is not None:
            report.rejected.append(Rejection(bar.ts_open_ms, "ohlc_invalid", reason))
            continue
        if bar.ts_open_ms in seen:
            report.duplicates_dropped += 1
            continue
        seen[bar.ts_open_ms] = bar

    report.accepted = [seen[k] for k in sorted(seen)]
    report.gaps = detect_gaps([b.ts_open_ms for b in report.accepted], timeframe)
    report.outliers = _flag_outliers(report.accepted)
    return report


def validate_funding(points: list[tuple[int, object, object]]) -> ValidationReport:
    """Validate funding observations: grid-free, but deduplicated and sanity-checked.

    **No interval grid is enforced, deliberately.** The funding cadence is a per-instrument
    venue property, not a global constant: of the 20-instrument M1 universe, 17 fund at 8h
    and 3 (CL, PUMP, TRUMP) fund at 4h (venue fact V-13). Enforcing an 8h grid would
    quarantine every genuine observation from the 4h instruments.
    """
    report = ValidationReport()
    seen: dict[int, tuple[int, object, object]] = {}
    for ms, rate, realized in points:
        if rate is None and realized is None:
            report.rejected.append(Rejection(ms, "no_rate", "both rate fields empty"))
            continue
        # OKX caps funding at +/-0.375% per period for most instruments; anything beyond
        # 5% signals a parsing error rather than a real rate.
        for name, value in (("funding_rate", rate), ("realized_rate", realized)):
            if value is not None and abs(Decimal(str(value))) > Decimal("0.05"):
                report.rejected.append(
                    Rejection(ms, "rate_implausible", f"{name}={value} exceeds 5%")
                )
                break
        else:
            if ms in seen:
                report.duplicates_dropped += 1
                continue
            seen[ms] = (ms, rate, realized)
    report.accepted = []  # funding rows are returned by the caller, not carried here
    report.outliers = []
    return report
