"""Backfill orchestration - idempotent and resumable (architecture §12).

Flow per instrument and timeframe: fetch backward from now to the target horizon ->
validate -> group into calendar months -> write each month atomically -> record in the
manifest. Months already recorded complete are skipped without a network call, so
**re-running a finished backfill is a no-op**.

The month currently in progress is deliberately *not* marked complete, because more bars
will arrive in it. It is rewritten on each run.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from typing import Any, Protocol

import duckdb

from okxq.data.manifest import Manifest, PartitionKey
from okxq.data.okx_public import TIMEFRAME_MS, Bar, FundingPoint
from okxq.data.store import ParquetStore
from okxq.data.validate import detect_gaps, validate_bars, validate_funding
from okxq.obs.logging import get_logger

log = get_logger(__name__)

SOURCE_OHLCV = "okx.rest.history-candles"
SOURCE_FUNDING = "okx.rest.funding-rate-history"

#: Partition label for funding, deliberately *not* "8h". The cadence is a per-instrument
#: venue property - 17 of the M1 universe fund at 8h, three at 4h (venue fact V-13) - so
#: the store must not assert an interval it cannot guarantee. M2 derives the actual interval
#: from consecutive fundingTime values.
FUNDING_TIMEFRAME = "funding"


@dataclass
class BackfillStats:
    """Totals for one backfill run."""

    instruments: int = 0
    partitions_written: int = 0
    partitions_skipped: int = 0
    rows_written: int = 0
    rejections: int = 0
    missing_bars: int = 0
    gap_runs: int = 0
    requests: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"instruments={self.instruments} written={self.partitions_written} "
            f"skipped={self.partitions_skipped} rows={self.rows_written:,} "
            f"missing_bars={self.missing_bars} gap_runs={self.gap_runs} "
            f"rejected={self.rejections} requests={self.requests} errors={len(self.errors)}"
        )

    def merge(self, other: BackfillStats) -> None:
        """Fold another phase's totals into this one.

        Phases report their own stats and the run keeps a separate total, so a per-phase
        line cannot be mistaken for a cumulative one.
        """
        self.instruments += other.instruments
        self.partitions_written += other.partitions_written
        self.partitions_skipped += other.partitions_skipped
        self.rows_written += other.rows_written
        self.rejections += other.rejections
        self.missing_bars += other.missing_bars
        self.gap_runs += other.gap_runs
        self.requests = max(self.requests, other.requests)  # monotonic counter, not a sum
        self.errors.extend(other.errors)


def _month_of(ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return dt.year, dt.month


def group_by_month(bars: list[Bar]) -> dict[tuple[int, int], list[Bar]]:
    """Group bars into calendar months by their open timestamp."""
    grouped: dict[tuple[int, int], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[_month_of(bar.ts_open_ms)].append(bar)
    return dict(grouped)


class CandleSource(Protocol):
    """What the backfiller needs from a market-data source.

    A Protocol rather than the concrete :class:`~okxq.data.okx_public.OkxPublic`, for the
    same reason the adapter exists at all (architecture §16): the orchestration layer should
    depend on a contract, not on a venue client. It also makes the backfill loop testable
    without a network, which is how the orchestration got unit coverage at all.
    """

    # Declares exactly what the backfiller calls, and no more. An implementation may add
    # further defaulted parameters (OkxPublic has `max_pages`) and still satisfy this.
    @property
    def request_count(self) -> int: ...

    def milliseconds(self) -> int: ...

    def fetch_candles_back_to(
        self, inst_id: str, timeframe: str, *, stop_at_ms: int, price_type: str = ...
    ) -> list[Bar]: ...

    def fetch_funding_all(self, inst_id: str) -> list[FundingPoint]: ...


class Backfiller:
    """Drives OHLCV and funding backfill for a set of instruments."""

    def __init__(
        self,
        source: CandleSource,
        store: ParquetStore,
        manifest: Manifest,
        *,
        env: str,
    ) -> None:
        self.source = source
        self.store = store
        self.manifest = manifest
        self.env = env

    def run_ohlcv(
        self,
        instruments: list[dict[str, object]],
        timeframe: str,
        *,
        horizon_ms: int,
        price_type: str = "last",
        stats: BackfillStats | None = None,
    ) -> BackfillStats:
        """Backfill one timeframe for every instrument, back to ``horizon_ms``."""
        stats = stats or BackfillStats()
        current_month = _month_of(self.source.milliseconds())

        for inst in instruments:
            inst_id = str(inst["inst_id"])
            symbol = str(inst["symbol"])
            # Index candles are published per instrument family, so they are *fetched* by
            # family but still *stored* under inst_id - otherwise index rows would not
            # join to the swap's own OHLCV.
            family = inst.get("inst_family")
            fetch_id = str(family) if price_type == "index" and family else inst_id

            # Resume: if stored history already reaches the horizon, only top up the
            # recent end instead of re-walking years of pages. Without this a re-run or a
            # restart costs the same as a cold start, which makes a long backfill
            # effectively non-resumable.
            step = TIMEFRAME_MS[timeframe]
            effective_stop = horizon_ms
            extent = self.manifest.extent("ohlcv", inst_id, timeframe, price_type)
            if extent is not None and extent[0] <= horizon_ms + step:
                # One bar of overlap, so a boundary bar cannot be skipped.
                effective_stop = max(horizon_ms, extent[1] - step)

            try:
                bars = self.source.fetch_candles_back_to(
                    fetch_id, timeframe, stop_at_ms=effective_stop, price_type=price_type
                )
            except Exception as exc:  # noqa: BLE001 - one bad instrument must not end the run
                msg = f"{fetch_id} {timeframe} {price_type}: {type(exc).__name__}: {exc}"
                log.warning("fetch failed", extra={"inst_id": inst_id, "error": msg})
                stats.errors.append(msg)
                continue

            # Sampled per instrument, immediately after the fetch, never once per phase.
            # A phase-level reading goes stale: the 5m backfill ran ~50 minutes, so by the
            # time it reached later instruments, bars the venue had already marked closed
            # (confirm="1") looked like future bars against the old timestamp and were
            # falsely quarantined - 9 valid bars lost on the first run. The venue's
            # confirm flag is the authoritative closed-bar signal; this timestamp check is
            # a secondary guard and so must read a fresh clock.
            report = validate_bars(bars, timeframe, now_ms=self.source.milliseconds())
            stats.rejections += len(report.rejected)

            if report.rejected:
                self.manifest.record_rejections(
                    "ohlcv",
                    inst_id,
                    timeframe,
                    [(r.ts_open_ms, r.rule, r.detail) for r in report.rejected],
                )

            for (year, month), month_bars in sorted(group_by_month(report.accepted).items()):
                key = PartitionKey(
                    dataset="ohlcv",
                    inst_id=inst_id,
                    timeframe=timeframe,
                    year=year,
                    month=month,
                    price_type=price_type,
                )
                is_current = (year, month) == current_month
                # Skip only sealed months. The in-progress month is always rewritten
                # because it will keep gaining bars.
                #
                # INVARIANT, load-bearing for resume: the current month is written but
                # NEVER recorded in the manifest. `extent()` must therefore describe only
                # sealed partitions. If the current month were ever recorded, the resume
                # calculation above would set `effective_stop` inside it, the next run
                # would fetch only that tail, and the always-rewritten current-month
                # partition would be truncated to it - silently losing earlier bars of the
                # month. Asserted by test_current_month_is_never_sealed.
                if not is_current and self.manifest.is_complete(key):
                    stats.partitions_skipped += 1
                    continue

                result = self.store.write_ohlcv(
                    key, month_bars, symbol=symbol, source=SOURCE_OHLCV
                )
                # Recorded only after the atomic rename succeeded.
                if not is_current:
                    self.manifest.record_partition(
                        key,
                        rows=result.rows,
                        first_ts_ms=result.first_ts_ms,
                        last_ts_ms=result.last_ts_ms,
                        missing_bars=0,
                        rejected=0,
                        relative_path=result.relative_path,
                    )
                stats.partitions_written += 1
                stats.rows_written += result.rows

            # Gap accounting is derived from the COMPLETE stored series, after the write,
            # not from the slice just fetched (finding F-7). On a resume the slice is only
            # the recent tail, so slice-derived gaps would describe a fraction of the
            # series while replacing the record for all of it - erasing genuine historical
            # gaps. Replacing with the full recomputation also clears a gap that a later
            # fetch has since filled.
            series_gaps = recompute_series_gaps(
                self.store, inst_id, timeframe, price_type=price_type
            )
            self.manifest.replace_gaps(
                "ohlcv", inst_id, timeframe, series_gaps, price_type=price_type
            )
            stats.gap_runs += len(series_gaps)
            stats.missing_bars += sum(g[2] for g in series_gaps)

            stats.instruments += 1
            log.info(
                "backfilled",
                extra={
                    "inst_id": inst_id,
                    "timeframe": timeframe,
                    "price_type": price_type,
                    "bars": len(report.accepted),
                    "validation": report.summary(),
                    "series_gap_runs": len(series_gaps),
                },
            )

        stats.requests = self.source.request_count
        return stats

    def run_funding(
        self, instruments: list[dict[str, object]], *, stats: BackfillStats | None = None
    ) -> BackfillStats:
        """Archive the venue's entire retained funding history for each instrument.

        Only ~3 months is retained, so this must run on an ongoing schedule: each week of
        delay permanently loses a week of realised funding that cannot be recovered later.
        """
        stats = stats or BackfillStats()
        for inst in instruments:
            inst_id = str(inst["inst_id"])
            symbol = str(inst["symbol"])
            try:
                points = self.source.fetch_funding_all(inst_id)
            except Exception as exc:  # noqa: BLE001
                msg = f"{inst_id} funding: {type(exc).__name__}: {exc}"
                log.warning("funding fetch failed", extra={"inst_id": inst_id, "error": msg})
                stats.errors.append(msg)
                continue

            report = validate_funding(
                [(p.funding_time_ms, p.funding_rate, p.realized_rate) for p in points]
            )
            stats.rejections += len(report.rejected)
            if report.rejected:
                self.manifest.record_rejections(
                    "funding",
                    inst_id,
                    FUNDING_TIMEFRAME,  # not a cadence - see V-13
                    [(r.ts_open_ms, r.rule, r.detail) for r in report.rejected],
                )
            rejected_ts = {r.ts_open_ms for r in report.rejected}
            clean = [p for p in points if p.funding_time_ms not in rejected_ts]

            grouped: dict[tuple[int, int], list[FundingPoint]] = defaultdict(list)
            for point in clean:
                grouped[_month_of(point.funding_time_ms)].append(point)

            for (year, month), month_points in sorted(grouped.items()):
                key = PartitionKey(
                    dataset="funding",
                    inst_id=inst_id,
                    timeframe=FUNDING_TIMEFRAME,
                    year=year,
                    month=month,
                )
                # Funding partitions are always rewritten: the retention window rolls, so
                # a month that looked complete may still gain late-arriving corrections.
                result = self.store.write_funding(
                    key, month_points, symbol=symbol, source=SOURCE_FUNDING
                )
                self.manifest.record_partition(
                    key,
                    rows=result.rows,
                    first_ts_ms=result.first_ts_ms,
                    last_ts_ms=result.last_ts_ms,
                    missing_bars=0,
                    rejected=0,
                    relative_path=result.relative_path,
                )
                stats.partitions_written += 1
                stats.rows_written += result.rows

            stats.instruments += 1
            log.info("funding archived", extra={"inst_id": inst_id, "rows": len(clean)})

        stats.requests = self.source.request_count
        return stats


def recompute_series_gaps(
    store: ParquetStore,
    inst_id: str,
    timeframe: str,
    *,
    price_type: str = "last",
) -> list[tuple[int, int, int]]:
    """Detect gaps across a series' **complete stored history**, read back from Parquet.

    Computed from the store rather than from the batch just fetched, which is what makes
    gap accounting survive a resume (finding F-7). A resumed run fetches only the recent
    tail, so gaps derived from that tail would describe a fraction of the series while
    replacing the record for all of it.

    Reads through :meth:`ParquetStore.series_glob`, so the cost is a targeted scan
    (~0.03 s for a 60k-bar series) rather than a whole-store listing.
    """
    if not store.series_exists("ohlcv", inst_id, timeframe, price_type):
        return []
    pattern = store.series_glob("ohlcv", inst_id, timeframe, price_type)
    with duckdb.connect() as conn:
        rows = conn.execute(
            f"SELECT ts_open_ms FROM read_parquet('{pattern}', hive_partitioning=true) "  # noqa: S608
            "ORDER BY ts_open_ms"
        ).fetchall()
    return [(g.start_ms, g.end_ms, g.missing_bars) for g in detect_gaps([int(r[0]) for r in rows], timeframe)]


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def horizon_ms_for_years(now_ms: int, years: float) -> int:
    return now_ms - int(years * 365.25 * 24 * 3600 * 1000)


__all__ = [
    "BackfillStats",
    "Backfiller",
    "TIMEFRAME_MS",
    "group_by_month",
    "horizon_ms_for_years",
    "new_run_id",
]
