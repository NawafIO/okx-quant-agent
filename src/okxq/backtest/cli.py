"""M2 CLI - the steps that need real market data, each one command.

    python -m okxq.backtest.cli funding-validate --env PAPER
    python -m okxq.backtest.cli funding-model    --env PAPER
    python -m okxq.backtest.cli sanity-random    --env PAPER --specs docs/instrument_specs.json \\
        --maker 0.0002 --taker 0.0005 --fee-evidence "OKX fee page, VIP0, read 2026-10-xx"

Exit codes: 0 pass; 2 refused (typed error); 3 funding model must be ESCALATED to the Chief
Advisor; 4 insufficient data; 5 random entry was NOT negative after costs (the cost model
is suspect - stop and investigate, do not proceed to M4).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean

from okxq.audit.chain import AuditChain
from okxq.backtest import costs, funding_bound, sanity
from okxq.backtest import funding_model as fm
from okxq.backtest.engine import BacktestEngine, EngineConfig
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import (
    MODELLED_FUNDING,
    load_calibration_bars,
    load_calibration_funding,
    load_research_bars,
    load_research_funding,
)
from okxq.backtest.reference_strategies import RandomEntry
from okxq.backtest.sizing import ProvisionalFixedNotionalSizer
from okxq.backtest.types import (
    BacktestConfigError,
    BarSeries,
    FeeSchedule,
    FundingRate,
    InstrumentSpec,
    Provenance,
)
from okxq.data.manifest import PartitionKey
from okxq.data.okx_public import FundingPoint
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs, parse_env
from okxq.errors import OkxqError
from okxq.obs.logging import configure_logging, get_logger

log = get_logger(__name__)
HOUR = 3_600_000


def _store(env: str) -> tuple[ParquetStore, Path, AuditChain]:
    profile = build_profile(parse_env(env))
    ensure_dirs(profile)
    return (
        ParquetStore(profile.parquet_root),
        profile.state_db.parent,
        AuditChain(profile.audit_log),
    )


def _instruments_with(store: ParquetStore, price_type: str) -> list[str]:
    base = store.root / "ohlcv"
    if not base.exists():
        return []
    return sorted(
        p.name.removeprefix("inst_id=")
        for p in base.glob("inst_id=*")
        if (p / "timeframe=1h" / f"price_type={price_type}").exists()
    )


def _calibration_inputs(
    store: ParquetStore, chain: AuditChain, *, full_history: bool = False
) -> dict[str, tuple[BarSeries, BarSeries, list[FundingRate]]]:
    """Mark, index and realised funding per instrument. Validation needs only the realised
    overlap; reconstruction needs the whole mark/index history (``full_history``)."""
    now = int(datetime.now(tz=UTC).timestamp() * 1000)
    out = {}
    with_mark = set(_instruments_with(store, "mark")) & set(_instruments_with(store, "index"))
    for inst in sorted(with_mark):
        realised = load_calibration_funding(store, chain, inst, 0, now)
        if len(realised) < 2:
            continue
        lo = 0 if full_history else realised[0].ts_ms - 24 * HOUR
        mark = load_calibration_bars(store, chain, inst, "mark", lo, now)
        index = load_calibration_bars(store, chain, inst, "index", lo, now)
        out[inst] = (mark, index, realised)
    return out


def cmd_funding_validate(args: argparse.Namespace) -> int:
    store, state_dir, chain = _store(args.env)
    data = _calibration_inputs(store, chain)
    every = _instruments_with(store, "last")
    missing = sorted(set(every) - set(data))
    report = fm.validate(data)
    payload = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "verdict": report.verdict,
        "policy": asdict(fm.POLICY),
        "model": asdict(report.model) if report.model else None,
        "instruments": [asdict(v) for v in report.instruments],
        "notes": list(report.notes),
        "instruments_without_mark_index": missing,
    }
    out = state_dir / "funding_validation.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    # Every look at the validation window is a D1 trial (Chief Advisor ruling 2026-10-03):
    # the record is what stops a failed model from being quietly re-fitted until it passes.
    chain.append(
        "d1_validation",
        {
            "model": fm.MODEL_VERSION,
            "verdict": str(report.verdict),
            "passed": sorted(v.inst_id for v in report.instruments if v.passed),
            "failed": sorted(v.inst_id for v in report.instruments if not v.passed),
        },
    )
    print(f"funding model {fm.MODEL_VERSION}: {report.verdict}   (report: {out})")
    for v in report.instruments:
        rel = "abs-floor" if v.rel_error is None else f"{v.rel_error:7.1%}"
        print(
            f"  {v.inst_id:<18} {v.interval_ms // HOUR}h n={v.n_intervals:<4} "
            f"cum real {v.cum_realised:+.6f} model {v.cum_modelled:+.6f} "
            f"err {rel} sign {v.sign_agreement:.0%} {'PASS' if v.passed else 'FAIL'}"
        )
    if missing:
        print(f"  NO mark/index (no funding before the realised window): {', '.join(missing)}")
    if report.verdict is fm.FundingVerdict.ESCALATE:
        print("  -> tolerance not met: ESCALATE to the Chief Advisor. Do not tune to pass.")
        return 3
    if report.verdict is fm.FundingVerdict.PARTIAL:
        print(f"  PARTIAL: only {sorted(report.validated)} may receive a modelled series")
        return 0
    return 0 if report.verdict is fm.FundingVerdict.PASS else 4


def cmd_funding_bound(args: argparse.Namespace) -> int:
    """Derive funding-bound-v1 from realised funding (calibration door, logged) and freeze
    it in docs/funding_bound_v1.json for the research door to use."""
    store, _, chain = _store(args.env)
    now = int(datetime.now(tz=UTC).timestamp() * 1000)
    base = store.root / "funding"
    insts = sorted(p.name.removeprefix("inst_id=") for p in base.glob("inst_id=*"))
    bounds = []
    for inst in insts:
        realised = load_calibration_funding(store, chain, inst, 0, now)
        if len(realised) < 2:
            print(f"  {inst}: too little realised funding - no bound, engine will refuse it")
            continue
        bounds.append(funding_bound.derive(inst, realised))
    funding_bound.write_bounds(bounds)
    chain.append(
        "funding_bound_derived",
        {
            "version": funding_bound.BOUND_VERSION,
            "instruments": {b.inst_id: [str(b.cost_long), str(b.cost_short)] for b in bounds},
        },
    )
    print(f"{funding_bound.BOUND_VERSION}: {len(bounds)} instruments -> {funding_bound.BOUND_FILE}")
    for b in bounds:
        print(
            f"  {b.inst_id:<18} {b.interval_ms // HOUR}h  long pays {b.cost_long:.8f}  "
            f"short pays {b.cost_short:.8f}  (n={b.n_realised})"
        )
    return 0


def cmd_funding_model(args: argparse.Namespace) -> int:
    store, _, chain = _store(args.env)
    report = fm.validate(_calibration_inputs(store, chain))
    if not report.validated or report.model is None:
        print(f"refusing to write modelled funding: validation verdict is {report.verdict}")
        return 3 if report.verdict is fm.FundingVerdict.ESCALATE else 4
    written = 0
    for inst, (mark, index, realised) in _calibration_inputs(
        store, chain, full_history=True
    ).items():
        if inst not in report.validated:
            print(f"  {inst}: NOT validated - no modelled series written")
            continue
        interval = fm.measured_interval_ms(realised)
        prem = fm.hourly_premium(mark, index)
        times = fm.schedule(
            realised[0].ts_ms, interval, mark.ts_open_ms[0] + interval, realised[0].ts_ms
        )
        rates = fm.model_series(report.model, prem, times, interval)
        by_month: dict[tuple[int, int], list[FundingPoint]] = {}
        for r in rates:
            d = datetime.fromtimestamp(r.ts_ms / 1000, tz=UTC)
            by_month.setdefault((d.year, d.month), []).append(FundingPoint(r.ts_ms, r.rate, r.rate))
        for (y, mo), pts in sorted(by_month.items()):
            store.write_funding(
                PartitionKey(MODELLED_FUNDING, inst, "funding", y, mo),
                pts,
                symbol=inst,
                source=f"{fm.MODEL_VERSION}",
            )
            written += len(pts)
        print(
            f"  {inst}: {len(rates)} modelled settlements, {len(times) - len(rates)} "
            "quarantined (premium coverage too thin)"
        )
    print(f"wrote {written} modelled settlements to dataset '{MODELLED_FUNDING}'")
    return 0


def _load_specs(path: Path) -> dict[str, InstrumentSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        k: InstrumentSpec(
            k,
            Decimal(v["tick_size"]),
            Decimal(v["lot_size_base"]),
            Decimal(v["min_size_base"]),
            Decimal(v["mmr"]),
            Provenance.MEASURED,
        )
        for k, v in raw["instruments"].items()
    }


def _research_window(
    store: ParquetStore, specs: dict[str, InstrumentSpec], years: float
) -> tuple[int, int, dict[str, BarSeries], dict[str, list[FundingRate]]]:
    end = FROZEN.holdout_start_ms
    start = end - int(years * 365 * 24 * HOUR)
    warm = start - 30 * 24 * HOUR
    series: dict[str, BarSeries] = {}
    funding: dict[str, list[FundingRate]] = {}
    for inst in sorted(specs):
        bars = load_research_bars(store, inst, "1h", warm, end)
        rates = load_research_funding(store, inst, warm, end)
        if len(bars) and len(rates) >= 2:
            series[inst], funding[inst] = bars, rates
    return start, end, series, funding


def cmd_sanity_random(args: argparse.Namespace) -> int:
    """Random entry must lose, by exactly the modelled costs (see okxq.backtest.sanity)."""
    store, _, _ = _store(args.env)
    specs = _load_specs(Path(args.specs))
    start, end, series, funding = _research_window(store, specs, args.years)
    notional = Decimal(args.notional)
    sizer = ProvisionalFixedNotionalSizer(notional)
    fee_sets: list[tuple[str, FeeSchedule]] = []
    if args.maker is not None:
        if not args.fee_evidence:
            print("--maker/--taker need --fee-evidence (where and when they were read)")
            return 2
        fee_sets.append(
            ("measured", FeeSchedule(Decimal(args.maker), Decimal(args.taker), Provenance.MEASURED))
        )
    fee_sets += [
        ("user spot-schedule", costs.FEES_USER_SPOT_SCHEDULE),
        ("low sensitivity", costs.FEES_LOW_SENSITIVITY),
    ]

    def build(insts: Sequence[str], cfg: EngineConfig, sz: object = sizer) -> BacktestEngine:
        return BacktestEngine(
            series={k: series[k] for k in insts},
            specs=specs,
            funding={k: funding[k] for k in insts},
            config=cfg,
            sizer=sz,  # type: ignore[arg-type]
            trade_start_ms=start,
            end_ms=end,
        )

    # Instruments the funding series does not cover are dropped LOUDLY (finding A-1).
    probe = costs.research_config(costs.FEES_LOW_SENSITIVITY)
    usable = []
    for inst in sorted(series):
        try:
            build([inst], probe)
            usable.append(inst)
        except BacktestConfigError as exc:
            print(f"  dropped {inst}: {exc}")
    if not usable:
        print("no instrument has bars and covering funding over the window")
        return 4

    # Size ladder (check 6), at low fees and with the participation cap effectively OFF:
    # a binding cap fills every rung at the same quantity, so slippage cannot rise with
    # size and the check would measure the cap, not the impact term.
    ladder = []
    for n in (Decimal(1_000), Decimal(100_000), Decimal(1_000_000), Decimal(10_000_000)):
        cfg = costs.research_config(
            costs.FEES_LOW_SENSITIVITY, initial_equity=n * 1000, participation_cap=Decimal(10**9)
        )
        res = build(usable, cfg, ProvisionalFixedNotionalSizer(n)).run(RandomEntry(seed=0))
        bps = [
            float(f.slippage_cost / (abs(f.qty_delta) * f.price)) * 1e4
            for f in res.fills
            if f.liquidity == "taker" and f.qty_delta
        ]
        ladder.append((n, mean(bps) if bps else 0.0))

    ok = True
    for label, fees in fee_sets:
        cfg = costs.research_config(fees)
        print(f"\n== fees: {label} (maker {fees.maker}, taker {fees.taker}, {fees.provenance}) ==")
        print(f"   cost config sha256 {costs.cost_config_sha(cfg)}")
        print(
            f"   {len(usable)} instruments, {args.years}y to the holdout, {args.seeds} seeds, "
            f"${notional:,} per trade"
        )
        results = [build(usable, cfg).run(RandomEntry(seed=s)) for s in range(args.seeds)]
        report = sanity.evaluate(results, cfg.initial_equity, ladder)
        for c in report.checks:
            print(f"   [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        ok = ok and report.passed
    print("\nRANDOM-ENTRY CHECK:", "PASS" if ok else "FAIL - cost model suspect; do not proceed")
    return 0 if ok else 5


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="okxq.backtest", description="M2 data-dependent steps")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (
        ("funding-validate", cmd_funding_validate),
        ("funding-model", cmd_funding_model),
        ("funding-bound", cmd_funding_bound),
    ):
        p = sub.add_parser(name)
        p.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
        p.set_defaults(func=fn)
    s = sub.add_parser("sanity-random", help="random entry must lose, by exactly the costs")
    s.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
    s.add_argument("--specs", required=True, help="measured instrument specs JSON")
    s.add_argument("--maker", default=None, help="MEASURED perp maker rate (optional)")
    s.add_argument("--taker", default=None, help="MEASURED perp taker rate (optional)")
    s.add_argument("--fee-evidence", default="", help="where and when the rates were read")
    s.add_argument("--years", type=float, default=3.0)
    s.add_argument("--seeds", type=int, default=20)
    s.add_argument("--notional", default="1000", help="fixed USDT notional per trade")
    s.set_defaults(func=cmd_sanity_random)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OkxqError as exc:
        log.error("refused", extra={"error": type(exc).__name__, "detail": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
