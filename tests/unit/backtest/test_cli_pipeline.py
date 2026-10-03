"""M2 data-dependent steps end to end against a real (synthetic) Parquet store.

Exercises the exact read paths the real run will use - calibration door, modelled-funding
write, research door, coverage refusal - so the first run on real data is not their first run.
"""

from __future__ import annotations

import itertools
import json
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from okxq.audit.chain import AuditChain, read_chain
from okxq.backtest import cli
from okxq.backtest.holdout import load_research_funding
from okxq.data.backfill import group_by_month
from okxq.data.manifest import PartitionKey
from okxq.data.okx_public import Bar, FundingPoint
from okxq.data.store import ParquetStore

H = 3_600_000
INST = "BTC-USDT-SWAP"


def utc(y: int, mo: int, d: int = 1) -> int:
    return int(datetime(y, mo, d, tzinfo=UTC).timestamp()) * 1000


MARK_START, REALISED_START, END = utc(2025, 8), utc(2026, 6, 29), utc(2026, 10)


def _write(store: ParquetStore, price_type: str, rows: list[tuple[int, Decimal, Decimal]]) -> None:
    bars = [
        Bar(t, c, hi, c - (hi - c), c, Decimal(500), Decimal(5), Decimal(500) * c, True)
        for t, c, hi in rows
    ]
    for (y, mo), chunk in group_by_month(bars).items():
        store.write_ohlcv(
            PartitionKey("ohlcv", INST, "1h", y, mo, price_type),
            chunk,
            symbol=INST,
            source="synthetic",
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ParquetStore, Path]:
    store = ParquetStore(tmp_path / "parquet")
    chain = AuditChain(tmp_path / "audit.jsonl")
    monkeypatch.setattr(cli, "_store", lambda env: (store, tmp_path, chain))
    return store, tmp_path


def populate(store: ParquetStore) -> None:
    rng = random.Random(9)  # noqa: S311 - deterministic fixture
    hours = list(range(MARK_START, END, H))
    prem, p, price = {}, 0.0, 100.0
    mark, index, last = [], [], []
    for t in hours:
        p = 0.95 * p + rng.gauss(0, 0.0002)
        price *= 1 + rng.gauss(0, 0.004)
        idx = Decimal(repr(round(price, 4)))
        mk = Decimal(repr(round(price * (1 + p), 4)))
        prem[t] = float((mk - idx) / idx)
        index.append((t, idx, idx + Decimal("0.05")))
        mark.append((t, mk, mk + Decimal("0.05")))
        last.append((t, mk, mk + Decimal("0.3")))
    _write(store, "index", index)
    _write(store, "mark", mark)
    _write(store, "last", last)
    pts = []
    for t in range(REALISED_START, END, 8 * H):
        window = [prem[h] for h in range(t - 8 * H, t, H)]
        rate = Decimal(repr(round(0.5 * sum(window) / 8 + 0.0001, 8)))
        pts.append(FundingPoint(t, rate, rate))
    by_month: dict[tuple[int, int], list[FundingPoint]] = {}
    for x in pts:
        d = datetime.fromtimestamp(x.funding_time_ms / 1000, tz=UTC)
        by_month.setdefault((d.year, d.month), []).append(x)
    for (y, mo), chunk in by_month.items():
        store.write_funding(
            PartitionKey("funding", INST, "funding", y, mo), chunk, symbol=INST, source="synthetic"
        )


def test_validate_on_an_empty_store_is_insufficient_not_a_pass(
    env: tuple[ParquetStore, Path],
) -> None:
    assert cli.main(["funding-validate", "--env", "PAPER"]) == 4


def test_funding_pipeline_end_to_end(env: tuple[ParquetStore, Path], tmp_path: Path) -> None:
    store, state = env
    populate(store)

    assert cli.main(["funding-validate", "--env", "PAPER"]) == 0
    report = json.loads((state / "funding_validation.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["instruments"][0]["interval_ms"] == 8 * H
    # Every calibration read went through the logged door.
    assert {r.kind for r in read_chain(state / "audit.jsonl")} == {"calibration_read"}

    assert cli.main(["funding-model", "--env", "PAPER"]) == 0
    research = load_research_funding(store, INST, utc(2025, 8, 10), utc(2025, 10))
    assert research and all(r.modelled for r in research)
    gaps = {b.ts_ms - a.ts_ms for a, b in itertools.pairwise(research)}
    assert gaps == {8 * H}

    specs = tmp_path / "specs.json"
    specs.write_text(
        json.dumps(
            {
                "instruments": {
                    INST: {
                        "tick_size": "0.0001",
                        "lot_size_base": "0.01",
                        "min_size_base": "0.01",
                        "mmr": "0.005",
                    }
                }
            }
        )
    )
    code = cli.main(
        [
            "sanity-random",
            "--env",
            "PAPER",
            "--specs",
            str(specs),
            "--maker",
            "0.0002",
            "--taker",
            "0.0005",
            "--fee-evidence",
            "synthetic test",
            "--years",
            "0.1",
            "--seeds",
            "3",
        ]
    )
    # Synthetic driftless walk + real costs: random entry must lose.
    assert code == 0
