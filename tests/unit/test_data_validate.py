"""Validation battery: quarantine-not-repair, and gap detection (architecture §12)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.data.okx_public import TIMEFRAME_MS, Bar
from okxq.data.validate import detect_gaps, validate_bars, validate_funding

HOUR = TIMEFRAME_MS["1h"]
BASE = 1_700_000_000_000 // HOUR * HOUR  # grid-aligned


def bar(offset_bars: int, **over: object) -> Bar:
    defaults: dict[str, object] = {
        "ts_open_ms": BASE + offset_bars * HOUR,
        "open": Decimal(100),
        "high": Decimal(110),
        "low": Decimal(95),
        "close": Decimal(105),
        "volume_contracts": Decimal(10),
        "volume_base": Decimal(1),
        "volume_quote": Decimal(100),
        "is_closed": True,
    }
    return Bar(**{**defaults, **over})  # type: ignore[arg-type]


NOW = BASE + 1000 * HOUR


def test_clean_batch_accepted() -> None:
    report = validate_bars([bar(i) for i in range(10)], "1h", now_ms=NOW)
    assert report.ok
    assert len(report.accepted) == 10
    assert not report.gaps


def test_unclosed_bar_rejected_not_repaired() -> None:
    """A partial bar must never enter the store."""
    report = validate_bars([bar(0), bar(1, is_closed=False)], "1h", now_ms=NOW)
    assert len(report.accepted) == 1
    assert [r.rule for r in report.rejected] == ["unclosed_bar"]


def test_bar_that_has_not_closed_yet_is_rejected() -> None:
    """A bar whose close time is in the future cannot be trusted as final."""
    report = validate_bars([bar(0)], "1h", now_ms=BASE + HOUR // 2)
    assert not report.accepted
    assert report.rejected[0].rule == "future_or_incomplete"


def test_off_grid_bar_rejected() -> None:
    """An off-grid open means we have misunderstood the venue's bucketing."""
    report = validate_bars([bar(0, ts_open_ms=BASE + 137)], "1h", now_ms=NOW)
    assert report.rejected[0].rule == "off_grid"


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"high": Decimal(90)}, "high below close"),
        ({"low": Decimal(120)}, "low above open"),
        ({"open": Decimal(0)}, "non-positive"),
        ({"close": Decimal(-5)}, "negative"),
        ({"volume_base": Decimal(-1)}, "negative volume"),
        ({"high": Decimal(50), "low": Decimal(60)}, "high < low"),
    ],
)
def test_invalid_ohlc_quarantined(over: dict[str, object], why: str) -> None:
    report = validate_bars([bar(0, **over)], "1h", now_ms=NOW)
    assert not report.accepted, f"{why} should be quarantined"
    assert report.rejected[0].rule == "ohlc_invalid"


def test_missing_price_quarantined() -> None:
    report = validate_bars([bar(0, close=None)], "1h", now_ms=NOW)
    assert report.rejected[0].rule == "ohlc_invalid"


def test_duplicates_dropped_not_duplicated() -> None:
    report = validate_bars([bar(0), bar(0), bar(1)], "1h", now_ms=NOW)
    assert len(report.accepted) == 2
    assert report.duplicates_dropped == 1


def test_output_is_sorted_chronologically() -> None:
    report = validate_bars([bar(5), bar(1), bar(3)], "1h", now_ms=NOW)
    timestamps = [b.ts_open_ms for b in report.accepted]
    assert timestamps == sorted(timestamps)


# --- gaps: recorded, never filled -------------------------------------------------------


def test_gap_detected_and_not_filled() -> None:
    """A missing bar is information, not something to interpolate."""
    report = validate_bars([bar(0), bar(1), bar(5), bar(6)], "1h", now_ms=NOW)
    assert len(report.accepted) == 4, "gaps must not be back-filled with invented bars"
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.missing_bars == 3
    assert gap.start_ms == BASE + 2 * HOUR
    assert gap.end_ms == BASE + 4 * HOUR
    assert report.missing_bars == 3


def test_contiguous_series_has_no_gaps() -> None:
    assert detect_gaps([BASE + i * HOUR for i in range(50)], "1h") == []


def test_multiple_gaps() -> None:
    stamps = [BASE, BASE + HOUR, BASE + 4 * HOUR, BASE + 10 * HOUR]
    gaps = detect_gaps(stamps, "1h")
    assert [g.missing_bars for g in gaps] == [2, 5]


def test_gaps_on_daily_grid() -> None:
    day = TIMEFRAME_MS["1d"]
    base = 1_700_000_000_000 // day * day
    gaps = detect_gaps([base, base + 3 * day], "1d")
    assert gaps[0].missing_bars == 2


def test_gaps_do_not_make_report_not_ok() -> None:
    """Gaps are reported; only rejections mean the batch failed validation."""
    report = validate_bars([bar(0), bar(9)], "1h", now_ms=NOW)
    assert report.gaps
    assert report.ok


# --- outliers: flagged, kept ------------------------------------------------------------


def test_outlier_flagged_but_retained() -> None:
    """A flash crash is exactly the event a backtest most needs - flag, do not drop."""
    bars = [bar(i) for i in range(40)]
    bars[20] = bar(20, low=Decimal(1), high=Decimal(100_000))
    report = validate_bars(bars, "1h", now_ms=NOW)
    assert len(report.accepted) == 40
    assert bars[20].ts_open_ms in report.outliers


def test_no_outliers_flagged_on_short_series() -> None:
    report = validate_bars([bar(i) for i in range(5)], "1h", now_ms=NOW)
    assert report.outliers == []


# --- funding ----------------------------------------------------------------------------


def test_funding_implausible_rate_rejected() -> None:
    report = validate_funding([(BASE, Decimal("0.5"), Decimal("0.5"))])
    assert report.rejected[0].rule == "rate_implausible"


def test_funding_empty_rate_rejected() -> None:
    report = validate_funding([(BASE, None, None)])
    assert report.rejected[0].rule == "no_rate"


def test_funding_normal_rate_accepted() -> None:
    report = validate_funding([(BASE, Decimal("0.0001"), Decimal("0.0001"))])
    assert not report.rejected
