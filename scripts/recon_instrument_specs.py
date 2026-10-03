"""Measure the instrument specs the backtest engine refuses to run without.

    python scripts/recon_instrument_specs.py --out docs/instrument_specs.json

Public endpoints only. Writes tick size, lot and minimum size in BASE units, and the first
isolated-margin tier's maintenance margin ratio, for every instrument with stored 1h bars.

UNVERIFIED FIELD NAMES. Written 2026-10-03 without network access, so the response fields
used below (``tickSz``, ``lotSz``, ``minSz``, ``ctVal``, ``ctValCcy``, tier ``mmr``) are from
OKX's documented API, not from a measured response - exactly what VENUE_FACTS warns against.
The script therefore saves the RAW responses next to the output and fails loudly on any
missing field or a ``ctValCcy`` that is not the base currency. Check the raw file before
trusting the output, then record the facts and the date in docs/VENUE_FACTS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parents[1]


def stored_instruments(env: str) -> list[str]:
    base = ROOT / "data" / env.lower() / "parquet" / "ohlcv"
    return sorted(
        p.name.removeprefix("inst_id=")
        for p in base.glob("inst_id=*")
        if (p / "timeframe=1h" / "price_type=last").exists()
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="PAPER")
    ap.add_argument("--out", default=str(ROOT / "docs" / "instrument_specs.json"))
    args = ap.parse_args()

    wanted = stored_instruments(args.env)
    if not wanted:
        print("no stored instruments found - run the M1 backfill first")
        return 4
    ex = ccxt.okx({"enableRateLimit": True})
    raw_instruments = ex.publicGetPublicInstruments({"instType": "SWAP"})
    by_id = {row["instId"]: row for row in raw_instruments["data"]}
    raw_tiers: dict[str, object] = {}
    specs: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for inst in wanted:
        row = by_id.get(inst)
        if row is None:
            problems.append(f"{inst}: not in the live instrument list (delisted?)")
            continue
        base_ccy = inst.split("-")[0]
        if row.get("ctValCcy") != base_ccy:
            problems.append(f"{inst}: ctValCcy={row.get('ctValCcy')!r}, expected {base_ccy!r}")
            continue
        tiers = ex.publicGetPublicPositionTiers(
            {"instType": "SWAP", "tdMode": "isolated", "instFamily": row["instFamily"]}
        )
        raw_tiers[inst] = tiers
        mine = [t for t in tiers["data"] if t.get("instId", inst) in (inst, "")]
        tier1 = min(mine or tiers["data"], key=lambda t: int(t["tier"]))
        ct_val = Decimal(row["ctVal"])
        specs[inst] = {
            "tick_size": row["tickSz"],
            "lot_size_base": str(Decimal(row["lotSz"]) * ct_val),
            "min_size_base": str(Decimal(row["minSz"]) * ct_val),
            "mmr": tier1["mmr"],
        }
    measured = datetime.now(tz=UTC).isoformat()
    out = Path(args.out)
    out.write_text(
        json.dumps(
            {"measured_utc": measured, "instruments": specs, "problems": problems}, indent=2
        ),
        encoding="utf-8",
    )
    out.with_suffix(".raw.json").write_text(
        json.dumps({"instruments": raw_instruments, "position_tiers": raw_tiers}, indent=1),
        encoding="utf-8",
    )
    print(f"{len(specs)} specs -> {out}")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
