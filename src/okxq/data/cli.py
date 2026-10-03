"""M1 data-pipeline CLI.

    python -m okxq.data.cli backfill --env PAPER
    python -m okxq.data.cli report  --env PAPER

Public endpoints only; no credential is read. ``--env`` is required, as everywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from okxq.data.backfill import Backfiller, BackfillStats, horizon_ms_for_years, new_run_id
from okxq.data.manifest import Manifest
from okxq.data.okx_public import OkxPublic
from okxq.data.store import ParquetStore, register_views
from okxq.data.universe import as_instruments, select_universe
from okxq.env.profiles import EnvProfile, build_profile, ensure_dirs, parse_env
from okxq.errors import OkxqError
from okxq.obs.logging import configure_logging, get_logger

log = get_logger(__name__)

DATASETS = ("ohlcv", "funding")


def _manifest_path(profile: EnvProfile) -> Path:
    """Coverage manifest lives beside the operational state DB, per environment."""
    return profile.state_db.with_name("data_manifest.db")


def cmd_backfill(args: argparse.Namespace) -> int:
    profile = build_profile(parse_env(args.env))
    ensure_dirs(profile)

    store = ParquetStore(profile.parquet_root)
    swept = store.sweep_orphans()
    if swept:
        log.info("swept orphaned temp files from an interrupted run", extra={"count": swept})

    manifest = Manifest(_manifest_path(profile))
    source = OkxPublic()
    markets = source.load_markets()
    now = source.milliseconds()

    skew = source.clock_skew_ms()
    log.info(
        "clock skew measured",
        extra={
            "skew_ms": skew,
            # Harmless for M1's unsigned requests; a hard blocker before M6's signed ones.
            "note": "public endpoints are unsigned; see VENUE_FACTS.md pre-M6 prerequisite",
        },
    )

    universe = select_universe(source, markets, size=args.symbols, now_ms=now)
    instruments = as_instruments(universe)
    print(f"universe: {len(instruments)} instruments")
    for entry in universe[:10]:
        print(f"  {entry.symbol:<22} vol24h={entry.quote_volume_24h:>18,.0f}")
    if len(universe) > 10:
        print(f"  ... and {len(universe) - 10} more")

    run_id = new_run_id()
    manifest.start_run(run_id, profile.env, note=f"M1 backfill, {len(instruments)} instruments")
    backfiller = Backfiller(source, store, manifest, env=profile.env)
    stats = BackfillStats()

    horizon = horizon_ms_for_years(now, args.years)
    print(f"\nhorizon: {datetime.fromtimestamp(horizon / 1000, tz=UTC):%Y-%m-%d} ({args.years}y)")

    # --funding-only is how the scheduled archive runs (prerequisite P-11): OKX retains
    # ~95 days of realised funding on a rolling window, so this must run regularly and
    # cheaply without re-touching OHLCV.
    timeframes = [] if args.funding_only else args.timeframes
    if args.funding_only:
        args.mark_index = False

    for timeframe in timeframes:
        subset = instruments if timeframe != "5m" else instruments[: args.symbols_5m]
        if not subset:
            continue
        print(f"\n=== OHLCV {timeframe} ({len(subset)} instruments) ===")
        phase = backfiller.run_ohlcv(subset, timeframe, horizon_ms=horizon)
        print(f"  {phase.summary()}")
        stats.merge(phase)

    # Mark and index candles underpin the modelled-funding reconstruction for the span
    # where realised funding is not retained (advisor ruling, M1).
    if args.mark_index:
        for price_type in ("mark", "index"):
            subset = instruments[: args.symbols_mark_index]
            if not subset:
                continue
            print(f"\n=== {price_type} candles 1h ({len(subset)} instruments) ===")
            phase = backfiller.run_ohlcv(subset, "1h", horizon_ms=horizon, price_type=price_type)
            print(f"  {phase.summary()}")
            stats.merge(phase)

    if not args.skip_funding:
        print(f"\n=== funding history ({len(instruments)} instruments) ===")
        phase = backfiller.run_funding(instruments)
        print(f"  {phase.summary()}")
        stats.merge(phase)

    views = register_views(profile.parquet_root.parent / "analytics.db", store, DATASETS)
    manifest.finish_run(run_id)

    print("\n=== run totals ===")
    print(f"  {stats.summary()}")
    print(f"  manifest: {manifest.totals()}")
    print(f"  duckdb views: {views}")
    for dataset in DATASETS:
        print(f"  {dataset}: {store.row_count(dataset):,} rows in parquet")
    if stats.errors:
        print(f"\n  {len(stats.errors)} instrument-level errors (run continued):")
        for err in stats.errors[:10]:
            print(f"    {err}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    profile = build_profile(parse_env(args.env))
    manifest = Manifest(_manifest_path(profile))
    coverage = manifest.coverage(args.dataset)
    if not coverage:
        print(f"no coverage recorded for dataset {args.dataset!r}")
        return 0

    print(f"{'inst_id':<20} {'tf':>4} {'type':>6} {'rows':>9} {'from':>11} {'to':>11} {'miss':>6}")
    for row in coverage:
        first = int(row["first_ts_ms"] or 0)
        last = int(row["last_ts_ms"] or 0)
        fmt = "%Y-%m-%d"
        print(
            f"{row['inst_id']!s:<20} {row['timeframe']!s:>4} {row['price_type']!s:>6} "
            f"{int(row['rows'] or 0):>9,} "
            f"{datetime.fromtimestamp(first / 1000, tz=UTC):{fmt}} "
            f"{datetime.fromtimestamp(last / 1000, tz=UTC):{fmt}} "
            f"{int(row['missing_bars'] or 0):>6}"
        )
    if args.json:
        print(json.dumps(coverage, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="okxq.data", description="M1 data pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="fetch and store history")
    bf.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
    bf.add_argument("--symbols", type=int, default=20, help="universe size")
    bf.add_argument("--symbols-5m", type=int, default=5, help="how many also get 5m bars")
    bf.add_argument("--symbols-mark-index", type=int, default=5)
    bf.add_argument("--years", type=float, default=7.0, help="history horizon")
    bf.add_argument("--timeframes", nargs="+", default=["1d", "1h", "5m"])
    bf.add_argument("--no-mark-index", dest="mark_index", action="store_false")
    bf.add_argument("--skip-funding", action="store_true", help="skip the funding archive")
    bf.add_argument(
        "--funding-only",
        action="store_true",
        help="archive realised funding only - no OHLCV, no mark/index (see P-11)",
    )
    bf.set_defaults(func=cmd_backfill, mark_index=True)

    rp = sub.add_parser("report", help="coverage and gap report")
    rp.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
    rp.add_argument("--dataset", default="ohlcv", choices=list(DATASETS))
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OkxqError as exc:
        log.error("refused", extra={"error": type(exc).__name__, "detail": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
