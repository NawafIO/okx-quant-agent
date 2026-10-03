"""Coverage manifest (architecture §12, §20.1).

SQLite, because this is the state a backfill resumes from after a crash and ACID matters
more than throughput. The manifest answers three questions:

* **Idempotency** - is this partition already complete, so a re-run is a no-op?
* **Resume** - which partitions were in flight when the process died?
* **Honesty** - how many bars are missing, and where, so gaps are never mistaken for
  "no trading happened".

A partition is marked complete only *after* its Parquet file has been atomically renamed
into place, so a crash can leave work to redo but never a partition recorded as complete
that is not.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS partitions (
    dataset       TEXT NOT NULL,
    inst_id       TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    price_type    TEXT NOT NULL DEFAULT 'last',
    year          INTEGER NOT NULL,
    month         INTEGER NOT NULL,
    rows          INTEGER NOT NULL,
    first_ts_ms   INTEGER,
    last_ts_ms    INTEGER,
    missing_bars  INTEGER NOT NULL DEFAULT 0,
    rejected      INTEGER NOT NULL DEFAULT 0,
    relative_path TEXT NOT NULL,
    completed_at  TEXT NOT NULL,
    PRIMARY KEY (dataset, inst_id, timeframe, price_type, year, month)
);

CREATE TABLE IF NOT EXISTS gaps (
    dataset    TEXT NOT NULL,
    inst_id    TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    price_type TEXT NOT NULL DEFAULT 'last',
    start_ms   INTEGER NOT NULL,
    end_ms     INTEGER NOT NULL,
    missing    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gaps ON gaps (dataset, inst_id, timeframe, price_type);

CREATE TABLE IF NOT EXISTS rejections (
    dataset    TEXT NOT NULL,
    inst_id    TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    ts_open_ms INTEGER NOT NULL,
    rule       TEXT NOT NULL,
    detail     TEXT NOT NULL,
    seen_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    env         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    note        TEXT
);
"""


@dataclass(frozen=True, slots=True)
class PartitionKey:
    """Identifies one Parquet partition."""

    dataset: str
    inst_id: str
    timeframe: str
    year: int
    month: int
    price_type: str = "last"


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


class Manifest:
    """Coverage metadata for the Parquet store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Apply additive schema changes to a manifest created by an earlier version.

        ``CREATE TABLE IF NOT EXISTS`` leaves an existing table untouched, so a column
        added to :data:`SCHEMA` never reaches a database that already exists - the next
        query just fails with "no such column". Additive migrations are applied here
        instead. Idempotent: an already-migrated database is left alone.
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(gaps)")}
        if existing and "price_type" not in existing:
            # gaps predates per-price-type separation; everything recorded then was the
            # last-price series, which is what the default records.
            conn.execute("ALTER TABLE gaps ADD COLUMN price_type TEXT NOT NULL DEFAULT 'last'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        # WAL for crash safety; a torn write must not corrupt the manifest.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    # --- idempotency -------------------------------------------------------------------

    def is_complete(self, key: PartitionKey) -> bool:
        """Whether this partition is already recorded complete."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM partitions WHERE dataset=? AND inst_id=? AND timeframe=? "
                "AND price_type=? AND year=? AND month=?",
                (key.dataset, key.inst_id, key.timeframe, key.price_type, key.year, key.month),
            ).fetchone()
        return row is not None

    def extent(
        self, dataset: str, inst_id: str, timeframe: str, price_type: str = "last"
    ) -> tuple[int, int] | None:
        """Oldest and newest stored timestamp for one series, or None if nothing stored.

        Lets a resumed backfill fetch only what it is missing instead of re-downloading
        the whole history, which is what makes a long run restartable.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT MIN(first_ts_ms), MAX(last_ts_ms) FROM partitions "
                "WHERE dataset=? AND inst_id=? AND timeframe=? AND price_type=?",
                (dataset, inst_id, timeframe, price_type),
            ).fetchone()
        if not row or row[0] is None or row[1] is None:
            return None
        return int(row[0]), int(row[1])

    def completed_keys(self, dataset: str) -> set[tuple[str, str, str, int, int]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT inst_id, timeframe, price_type, year, month FROM partitions "
                "WHERE dataset=?",
                (dataset,),
            ).fetchall()
        return {(r[0], r[1], r[2], r[3], r[4]) for r in rows}

    # --- recording ---------------------------------------------------------------------

    def record_partition(
        self,
        key: PartitionKey,
        *,
        rows: int,
        first_ts_ms: int | None,
        last_ts_ms: int | None,
        missing_bars: int,
        rejected: int,
        relative_path: str,
    ) -> None:
        """Mark a partition complete. Call only after the Parquet rename has succeeded."""
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO partitions (dataset, inst_id, timeframe, price_type,"
                " year, month, rows, first_ts_ms, last_ts_ms, missing_bars, rejected,"
                " relative_path, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key.dataset,
                    key.inst_id,
                    key.timeframe,
                    key.price_type,
                    key.year,
                    key.month,
                    rows,
                    first_ts_ms,
                    last_ts_ms,
                    missing_bars,
                    rejected,
                    relative_path,
                    _now(),
                ),
            )
            conn.commit()

    def replace_gaps(
        self,
        dataset: str,
        inst_id: str,
        timeframe: str,
        gaps: list[tuple[int, int, int]],
        *,
        price_type: str = "last",
    ) -> None:
        """Replace the recorded gaps for one series.

        **The caller must pass gaps for the COMPLETE stored series, not for a freshly
        fetched slice.** This replaces unconditionally - including with an empty list,
        which is how a gap that has since been filled gets cleared.

        An earlier version took the fetched slice and bailed out on an empty list. Both
        halves of that were wrong (finding F-7): on a resumed run the slice is only the
        recent tail, so a genuine historical gap recorded by the original backfill was
        silently deleted and replaced by the tail's gaps; and a gap later filled by a
        re-fetch was never removed. Either way the manifest stopped being an honest
        account of coverage, which is an M1 acceptance property. See
        :func:`okxq.data.backfill.recompute_series_gaps`.
        """
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM gaps WHERE dataset=? AND inst_id=? AND timeframe=? AND price_type=?",
                (dataset, inst_id, timeframe, price_type),
            )
            if gaps:
                conn.executemany(
                    "INSERT INTO gaps (dataset, inst_id, timeframe, price_type, start_ms,"
                    " end_ms, missing) VALUES (?,?,?,?,?,?,?)",
                    [(dataset, inst_id, timeframe, price_type, *g) for g in gaps],
                )
            conn.commit()

    def gaps_for(
        self, dataset: str, inst_id: str, timeframe: str, *, price_type: str = "last"
    ) -> list[tuple[int, int, int]]:
        """Recorded gaps for one series, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT start_ms, end_ms, missing FROM gaps WHERE dataset=? AND inst_id=?"
                " AND timeframe=? AND price_type=? ORDER BY start_ms",
                (dataset, inst_id, timeframe, price_type),
            ).fetchall()
        return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    def record_rejections(
        self,
        dataset: str,
        inst_id: str,
        timeframe: str,
        rejections: list[tuple[int, str, str]],
    ) -> None:
        if not rejections:
            return
        now = _now()
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO rejections (dataset, inst_id, timeframe, ts_open_ms, rule,"
                " detail, seen_at) VALUES (?,?,?,?,?,?,?)",
                [(dataset, inst_id, timeframe, *r, now) for r in rejections],
            )
            conn.commit()

    def start_run(self, run_id: str, env: str, note: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, env, started_at, note) VALUES (?,?,?,?)",
                (run_id, env, _now(), note),
            )
            conn.commit()

    def finish_run(self, run_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE runs SET finished_at=? WHERE run_id=?", (_now(), run_id))
            conn.commit()

    # --- reporting ---------------------------------------------------------------------

    def coverage(self, dataset: str = "ohlcv") -> list[dict[str, Any]]:
        """Per (instrument, timeframe) coverage summary, for the gap report."""
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT inst_id, timeframe, price_type, SUM(rows) AS rows,"
                " MIN(first_ts_ms) AS first_ts_ms, MAX(last_ts_ms) AS last_ts_ms,"
                " SUM(missing_bars) AS missing_bars, SUM(rejected) AS rejected,"
                " COUNT(*) AS partitions"
                " FROM partitions WHERE dataset=?"
                " GROUP BY inst_id, timeframe, price_type"
                " ORDER BY inst_id, timeframe",
                (dataset,),
            ).fetchall()
        return [dict(r) for r in rows]

    def totals(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            parts, rows = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(rows), 0) FROM partitions"
            ).fetchone()
            gaps = conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
            rejects = conn.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
        return {"partitions": parts, "rows": rows, "gap_runs": gaps, "rejections": rejects}
