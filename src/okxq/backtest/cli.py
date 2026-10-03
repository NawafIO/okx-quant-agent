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
from okxq.backtest.sizing import ProvisionalFixedFractionalSizer
from okxq.backtest.types import (
    BacktestConfigError,
    BarSeries,
    FeeSchedule,
    FundingRate,
    InstrumentSpec,
    Provenance,
    SlippageModel,
)
from okxq.data.manifest import PartitionKey
from okxq.data.okx_public import FundingPoint
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs, parse_env
from okxq.errors import OkxqError
from okxq.obs.logging import configure_logging, get_logger

log = get_logger(__name__)
HOUR = 3_600_000

#: The provisional slippage assumption for M2 sanity runs, reconciled against PAPER at M7.
DEFAULT_SLIPPAGE = SlippageModel(
    k_vol=Decimal("0.25"), k_impact=Decimal("0.1"), vol_lookback=24, assumption_id="slip-v1"
)


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
    return 0 if report.verdict is fm.FundingVerdict.PASS else 4


def cmd_funding_model(args: argparse.Namespace) -> int:
    store, _, chain = _store(args.env)
    report = fm.validate(_calibration_inputs(store, chain))
    if report.verdict is not fm.FundingVerdict.PASS or report.model is None:
        print(f"refusing to write modelled funding: validation verdict is {report.verdict}")
        return 3 if report.verdict is fm.FundingVerdict.ESCALATE else 4
    written = 0
    for inst, (mark, index, realised) in _calibration_inputs(
        store, chain, full_history=True
    ).items():
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


def cmd_sanity_random(args: argparse.Namespace) -> int:
    store, _, _ = _store(args.env)
    specs = _load_specs(Path(args.specs))
    fees = FeeSchedule(Decimal(args.maker), Decimal(args.taker), Provenance.MEASURED)
    log.info("fee provenance", extra={"evidence": args.fee_evidence})
    end = FROZEN.holdout_start_ms
    start = end - int(args.years * 365 * 24 * HOUR)
    series: dict[str, BarSeries] = {}
    funding: dict[str, Sequence[FundingRate]] = {}
    for inst in sorted(specs):
        bars = load_research_bars(store, inst, "1h", start - 30 * 24 * HOUR, end)
        rates = load_research_funding(store, inst, start - 30 * 24 * HOUR, end)
        if len(bars) and len(rates) >= 2:
            series[inst], funding[inst] = bars, rates
    config = EngineConfig(fees=fees, slippage=DEFAULT_SLIPPAGE, initial_equity=Decimal(100_000))
    sizer = ProvisionalFixedFractionalSizer(Decimal("0.005"), Decimal(3))

    def build(insts: Sequence[str]) -> BacktestEngine:
        return BacktestEngine(
            series={k: series[k] for k in insts},
            specs=specs,
            funding={k: funding[k] for k in insts},
            config=config,
            sizer=sizer,
            trade_start_ms=start,
            end_ms=end,
        )

    # Instruments the funding series does not cover are dropped LOUDLY, never run at zero
    # funding (finding A-1).
    usable = []
    for inst in sorted(series):
        try:
            build([inst])
            usable.append(inst)
        except BacktestConfigError as exc:
            print(f"  dropped {inst}: {exc}")
    if not usable:
        print("no instrument has bars and covering funding over the window")
        return 4
    print(f"random entry over {len(usable)} instruments, {args.years}y, {args.seeds} seeds")
    expectancies = []
    for seed in range(args.seeds):
        res = build(usable).run(RandomEntry(seed=seed))
        pnls = res.net_pnls
        e = float(sum(pnls, Decimal(0)) / len(pnls)) if pnls else 0.0
        expectancies.append(e)
        print(f"  seed {seed:>3}: {len(pnls):>5} trades, expectancy {e:+.4f} USDT/trade")
    neg = sum(1 for e in expectancies if e < 0)
    print(f"mean expectancy {mean(expectancies):+.4f}; negative in {neg}/{len(expectancies)} seeds")
    return 0 if mean(expectancies) < 0 else 5


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="okxq.backtest", description="M2 data-dependent steps")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (
        ("funding-validate", cmd_funding_validate),
        ("funding-model", cmd_funding_model),
    ):
        p = sub.add_parser(name)
        p.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
        p.set_defaults(func=fn)
    s = sub.add_parser("sanity-random", help="random entry must lose after costs")
    s.add_argument("--env", required=True, choices=["DEMO", "PAPER", "LIVE"])
    s.add_argument("--specs", required=True, help="measured instrument specs JSON")
    s.add_argument("--maker", required=True)
    s.add_argument("--taker", required=True)
    s.add_argument("--fee-evidence", required=True, help="where and when the rates were read")
    s.add_argument("--years", type=float, default=3.0)
    s.add_argument("--seeds", type=int, default=20)
    s.set_defaults(func=cmd_sanity_random)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OkxqError as exc:
        log.error("refused", extra={"error": type(exc).__name__, "detail": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
