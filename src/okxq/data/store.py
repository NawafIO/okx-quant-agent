"""Parquet store with atomic writes, plus DuckDB registration (architecture §12, §20.1).

Partitioning is ``dataset/inst_id/timeframe/year/month``, which lets a multi-year scan
prune to the months it needs and keeps individual files small enough to rewrite.

**Writes are atomic.** Each file is written to a temporary name in the same directory,
fsynced, then ``os.replace``d into place - atomic on NTFS for a same-directory rename. A
process killed mid-write leaves an orphaned ``.tmp`` file, never a half-written Parquet
file that reads as valid but short. Orphans are swept on the next run.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from okxq.data.manifest import PartitionKey
from okxq.data.okx_public import Bar, FundingPoint
from okxq.data.schema import FUNDING_SCHEMA, OHLCV_SCHEMA

TMP_SUFFIX = ".tmp"


@dataclass(frozen=True, slots=True)
class WriteResult:
    path: Path
    relative_path: str
    rows: int
    first_ts_ms: int | None
    last_ts_ms: int | None


class ParquetStore:
    """Partitioned Parquet store rooted at one environment's data directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # --- paths -------------------------------------------------------------------------

    def partition_dir(self, key: PartitionKey) -> Path:
        return (
            self.root
            / key.dataset
            / f"inst_id={key.inst_id}"
            / f"timeframe={key.timeframe}"
            / f"price_type={key.price_type}"
            / f"year={key.year}"
            / f"month={key.month:02d}"
        )

    def partition_path(self, key: PartitionKey) -> Path:
        return self.partition_dir(key) / "part.parquet"

    def sweep_orphans(self) -> int:
        """Delete temporary files left behind by an interrupted write."""
        removed = 0
        for tmp in self.root.rglob(f"*{TMP_SUFFIX}"):
            try:
                tmp.unlink()
                removed += 1
            except OSError:  # pragma: no cover - racing sweep
                pass
        return removed

    # --- writing -----------------------------------------------------------------------

    def _write_atomic(self, table: pa.Table, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name so two processes cannot collide on the same scratch file.
        tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}{TMP_SUFFIX}")
        try:
            # Written through a handle we own so it can be fsynced before the rename: the
            # rename must not publish a file whose contents are still only in the OS
            # cache, or a power loss leaves a valid-looking but truncated Parquet file.
            # (fsync needs the *write* handle - on Windows, fsync of a read-only handle
            # raises EBADF.)
            with open(tmp, "wb") as fh:
                pq.write_table(table, fh, compression="zstd", version="2.6")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            # Includes KeyboardInterrupt/SystemExit: a killed write leaves no partial file.
            tmp.unlink(missing_ok=True)
            raise

    def write_ohlcv(
        self,
        key: PartitionKey,
        bars: list[Bar],
        *,
        symbol: str,
        source: str,
    ) -> WriteResult:
        """Write one month of bars for one instrument, timeframe and price type."""
        now = datetime.now(tz=UTC)
        columns: dict[str, Any] = {
            "inst_id": [key.inst_id] * len(bars),
            "symbol": [symbol] * len(bars),
            "timeframe": [key.timeframe] * len(bars),
            "ts_open_ms": [b.ts_open_ms for b in bars],
            "ts_open": [datetime.fromtimestamp(b.ts_open_ms / 1000, tz=UTC) for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume_contracts": [b.volume_contracts for b in bars],
            "volume_base": [b.volume_base for b in bars],
            "volume_quote": [b.volume_quote for b in bars],
            "is_closed": [b.is_closed for b in bars],
            "price_type": [key.price_type] * len(bars),
            "source": [source] * len(bars),
            "ingested_at": [now] * len(bars),
        }
        table = pa.table(columns, schema=OHLCV_SCHEMA)
        target = self.partition_path(key)
        self._write_atomic(table, target)
        return WriteResult(
            path=target,
            relative_path=target.relative_to(self.root).as_posix(),
            rows=len(bars),
            first_ts_ms=bars[0].ts_open_ms if bars else None,
            last_ts_ms=bars[-1].ts_open_ms if bars else None,
        )

    def write_funding(
        self, key: PartitionKey, points: list[FundingPoint], *, symbol: str, source: str
    ) -> WriteResult:
        now = datetime.now(tz=UTC)
        columns: dict[str, Any] = {
            "inst_id": [key.inst_id] * len(points),
            "symbol": [symbol] * len(points),
            "funding_time_ms": [p.funding_time_ms for p in points],
            "funding_time": [
                datetime.fromtimestamp(p.funding_time_ms / 1000, tz=UTC) for p in points
            ],
            "funding_rate": [p.funding_rate for p in points],
            "realized_rate": [p.realized_rate for p in points],
            "source": [source] * len(points),
            "ingested_at": [now] * len(points),
        }
        table = pa.table(columns, schema=FUNDING_SCHEMA)
        target = self.partition_path(key)
        self._write_atomic(table, target)
        return WriteResult(
            path=target,
            relative_path=target.relative_to(self.root).as_posix(),
            rows=len(points),
            first_ts_ms=points[0].funding_time_ms if points else None,
            last_ts_ms=points[-1].funding_time_ms if points else None,
        )

    # --- reading -----------------------------------------------------------------------

    def glob(self, dataset: str) -> str:
        """Hive-partitioned glob for a whole dataset, for DuckDB to read.

        Convenient for exploration and totals, but **not the right handle for reading one
        series** - see :meth:`series_glob`.
        """
        return (self.root / dataset / "**" / "*.parquet").as_posix()

    def series_glob(
        self,
        dataset: str,
        inst_id: str,
        timeframe: str,
        price_type: str = "last",
    ) -> str:
        """Targeted glob for a single series - the research access pattern.

        Measured at M1: reading one symbol's hourly history through this path costs
        **0.031 s**, against **0.538 s** for the same rows via the whole-dataset glob with
        equivalent `WHERE` filters - a ~17x difference. The gap is file *listing* and
        footer metadata, not data volume: a bare ``COUNT(*)`` shows the same ratio, and the
        store holds ~2,400 files averaging 43 KB, far below Parquet's efficient size.

        Hive filters still prune correctly through :meth:`glob`; they simply cannot avoid
        enumerating every file first. So anything that reads a known series - above all
        M2's backtester walking one symbol's history - should use this.
        """
        return (
            self.root
            / dataset
            / f"inst_id={inst_id}"
            / f"timeframe={timeframe}"
            / f"price_type={price_type}"
            / "**"
            / "*.parquet"
        ).as_posix()

    def series_exists(
        self, dataset: str, inst_id: str, timeframe: str, price_type: str = "last"
    ) -> bool:
        base = (
            self.root
            / dataset
            / f"inst_id={inst_id}"
            / f"timeframe={timeframe}"
            / f"price_type={price_type}"
        )
        return base.exists() and any(base.rglob("*.parquet"))

    def row_count(self, dataset: str) -> int:
        pattern = self.glob(dataset)
        if not list(self.root.joinpath(dataset).rglob("*.parquet")):
            return 0
        with duckdb.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM read_parquet('{pattern}', hive_partitioning=true)"
            ).fetchone()
        return int(row[0]) if row else 0


def register_views(db_path: Path, store: ParquetStore, datasets: tuple[str, ...]) -> list[str]:
    """Create or replace a DuckDB view per dataset over the Parquet store.

    Views rather than tables: the Parquet files stay the single copy of the data, so there
    is no second copy to drift out of sync.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    with duckdb.connect(str(db_path)) as conn:
        for dataset in datasets:
            files = list(store.root.joinpath(dataset).rglob("*.parquet"))
            if not files:
                continue
            conn.execute(
                f"CREATE OR REPLACE VIEW {dataset} AS "
                f"SELECT * FROM read_parquet('{store.glob(dataset)}', hive_partitioning=true)"
            )
            created.append(dataset)
    return created
