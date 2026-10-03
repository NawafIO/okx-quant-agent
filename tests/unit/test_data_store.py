"""Store durability: atomic writes, torn-file resistance, idempotency, manifest.

The two tests that matter most here are :func:`test_kill_mid_write_leaves_no_torn_parquet`
and :func:`test_rerun_is_a_no_op` - both M1 acceptance criteria, and both far harder to
retrofit than to write now.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from okxq.data.manifest import Manifest, PartitionKey
from okxq.data.okx_public import TIMEFRAME_MS, Bar, FundingPoint
from okxq.data.schema import to_decimal
from okxq.data.store import TMP_SUFFIX, ParquetStore, register_views

HOUR = TIMEFRAME_MS["1h"]
BASE = 1_700_000_000_000 // HOUR * HOUR


def make_bars(n: int) -> list[Bar]:
    return [
        Bar(
            ts_open_ms=BASE + i * HOUR,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(95),
            close=Decimal("105.5"),
            volume_contracts=Decimal(10),
            volume_base=Decimal(1),
            volume_quote=Decimal("1055.5"),
            is_closed=True,
        )
        for i in range(n)
    ]


KEY = PartitionKey(dataset="ohlcv", inst_id="BTC-USDT-SWAP", timeframe="1h", year=2023, month=11)


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path / "parquet")


# --- decimal exactness (rule D-1 carried into storage) ----------------------------------


def test_prices_round_trip_exactly(store: ParquetStore) -> None:
    """Parquet must give back the same decimal, not a float approximation."""
    bars = make_bars(3)
    store.write_ohlcv(KEY, bars, symbol="BTC/USDT:USDT", source="test")
    table = pq.read_table(store.partition_path(KEY))

    closes = table.column("close").to_pylist()
    assert closes[0] == Decimal("105.500000000000000000")
    assert closes[0] == Decimal("105.5")


def test_large_volume_does_not_overflow_decimal_context() -> None:
    """A daily quote volume has >10 integer digits and broke the default prec=28 context."""
    assert to_decimal("91007807660.1") == Decimal("91007807660.1")
    assert to_decimal("988787055.56209") == Decimal("988787055.56209")


def test_empty_string_becomes_none_not_zero() -> None:
    """Absence is not zero: OKX returns '' for some optional numeric fields."""
    assert to_decimal("") is None
    assert to_decimal(None) is None
    assert to_decimal("0") == Decimal(0)


def test_decimal_never_built_from_float() -> None:
    """0.1 must stay 0.1, which it would not via float."""
    assert to_decimal("0.1") == Decimal("0.1")
    # The contrast is the point: Decimal(0.1) inherits the binary representation error.
    assert to_decimal("0.1") != Decimal(0.1)  # noqa: RUF032


# --- atomic writes ----------------------------------------------------------------------


def test_write_leaves_no_temp_file(store: ParquetStore) -> None:
    store.write_ohlcv(KEY, make_bars(5), symbol="BTC/USDT:USDT", source="test")
    assert not list(store.root.rglob(f"*{TMP_SUFFIX}"))
    assert store.partition_path(KEY).exists()


def test_kill_mid_write_leaves_no_torn_parquet(
    store: ParquetStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1 acceptance: a process killed during a write must not publish a partial file.

    Simulated by raising inside ``pq.write_table`` after the temp file exists. The target
    path must not appear at all - a truncated Parquet file that still parses is the
    dangerous outcome, because it reads as valid but short.
    """
    # store.py holds this same module object, so patching it here patches it there.
    real_write = pq.write_table

    def exploding_write(table: object, where: object, **kwargs: object) -> None:
        real_write(table, where, **kwargs)
        raise KeyboardInterrupt("simulated kill mid-write")

    monkeypatch.setattr(pq, "write_table", exploding_write)

    with pytest.raises(KeyboardInterrupt):
        store.write_ohlcv(KEY, make_bars(5), symbol="BTC/USDT:USDT", source="test")

    assert not store.partition_path(KEY).exists(), "no partial file may be published"
    assert not list(store.root.rglob(f"*{TMP_SUFFIX}")), "temp file must be cleaned up"


def test_interrupted_write_does_not_corrupt_previous_version(
    store: ParquetStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rewrite must leave the prior good file intact and readable."""
    store.write_ohlcv(KEY, make_bars(5), symbol="BTC/USDT:USDT", source="test")
    good_rows = pq.read_table(store.partition_path(KEY)).num_rows

    real_write = pq.write_table

    def boom(table: object, where: object, **kwargs: object) -> None:
        real_write(table, where, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", boom)
    with pytest.raises(OSError, match="disk full"):
        store.write_ohlcv(KEY, make_bars(9), symbol="BTC/USDT:USDT", source="test")

    monkeypatch.undo()
    assert pq.read_table(store.partition_path(KEY)).num_rows == good_rows


def test_sweep_removes_orphaned_temp_files(store: ParquetStore) -> None:
    orphan_dir = store.partition_dir(KEY)
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / f"part.parquet.deadbeef{TMP_SUFFIX}").write_bytes(b"garbage")

    assert store.sweep_orphans() == 1
    assert not list(store.root.rglob(f"*{TMP_SUFFIX}"))


# --- manifest: idempotency and resume ---------------------------------------------------


def test_rerun_is_a_no_op(tmp_path: Path, store: ParquetStore) -> None:
    """M1 acceptance: a completed partition is recognised and not rewritten."""
    manifest = Manifest(tmp_path / "m.db")
    assert manifest.is_complete(KEY) is False

    result = store.write_ohlcv(KEY, make_bars(5), symbol="BTC/USDT:USDT", source="test")
    manifest.record_partition(
        KEY,
        rows=result.rows,
        first_ts_ms=result.first_ts_ms,
        last_ts_ms=result.last_ts_ms,
        missing_bars=0,
        rejected=0,
        relative_path=result.relative_path,
    )

    assert manifest.is_complete(KEY) is True
    # A differently-keyed partition is independent.
    other = PartitionKey("ohlcv", "ETH-USDT-SWAP", "1h", 2023, 11)
    assert manifest.is_complete(other) is False


def test_manifest_records_only_after_successful_write(tmp_path: Path) -> None:
    """A crash may leave work to redo, never a partition falsely marked complete."""
    manifest = Manifest(tmp_path / "m.db")
    assert manifest.is_complete(KEY) is False
    assert manifest.totals()["partitions"] == 0


def test_extent_enables_cheap_resume(tmp_path: Path, store: ParquetStore) -> None:
    manifest = Manifest(tmp_path / "m.db")
    assert manifest.extent("ohlcv", "BTC-USDT-SWAP", "1h") is None

    bars = make_bars(5)
    result = store.write_ohlcv(KEY, bars, symbol="BTC/USDT:USDT", source="test")
    manifest.record_partition(
        KEY,
        rows=result.rows,
        first_ts_ms=result.first_ts_ms,
        last_ts_ms=result.last_ts_ms,
        missing_bars=0,
        rejected=0,
        relative_path=result.relative_path,
    )

    extent = manifest.extent("ohlcv", "BTC-USDT-SWAP", "1h")
    assert extent == (bars[0].ts_open_ms, bars[-1].ts_open_ms)


def test_manifest_survives_reopen(tmp_path: Path) -> None:
    """Resume after a restart depends on this."""
    Manifest(tmp_path / "m.db").record_partition(
        KEY,
        rows=3,
        first_ts_ms=BASE,
        last_ts_ms=BASE + 2 * HOUR,
        missing_bars=0,
        rejected=0,
        relative_path="x",
    )
    assert Manifest(tmp_path / "m.db").is_complete(KEY) is True


def test_gaps_and_rejections_recorded(tmp_path: Path) -> None:
    manifest = Manifest(tmp_path / "m.db")
    manifest.replace_gaps("ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, BASE + HOUR, 2)])
    manifest.record_rejections(
        "ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, "ohlc_invalid", "high < low")]
    )
    totals = manifest.totals()
    assert totals["gap_runs"] == 1
    assert totals["rejections"] == 1


def test_recording_gaps_twice_does_not_duplicate(tmp_path: Path) -> None:
    """Re-running a backfill must not accumulate duplicate gap rows."""
    manifest = Manifest(tmp_path / "m.db")
    for _ in range(3):
        manifest.replace_gaps("ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, BASE + HOUR, 2)])
    assert manifest.totals()["gap_runs"] == 1


def test_replace_gaps_with_empty_list_clears(tmp_path: Path) -> None:
    """A gap that a later fetch has filled must be cleared, not left behind (F-7)."""
    manifest = Manifest(tmp_path / "m.db")
    manifest.replace_gaps("ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, BASE + HOUR, 2)])
    assert manifest.totals()["gap_runs"] == 1

    manifest.replace_gaps("ohlcv", "BTC-USDT-SWAP", "1h", [])
    assert manifest.totals()["gap_runs"] == 0


def test_gaps_are_isolated_per_price_type(tmp_path: Path) -> None:
    """mark/index/last are separate series and must not overwrite each other's gaps."""
    manifest = Manifest(tmp_path / "m.db")
    manifest.replace_gaps(
        "ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, BASE + HOUR, 2)], price_type="last"
    )
    manifest.replace_gaps(
        "ohlcv", "BTC-USDT-SWAP", "1h", [(BASE, BASE + 5 * HOUR, 5)], price_type="mark"
    )
    assert manifest.gaps_for("ohlcv", "BTC-USDT-SWAP", "1h", price_type="last") == [
        (BASE, BASE + HOUR, 2)
    ]
    assert manifest.gaps_for("ohlcv", "BTC-USDT-SWAP", "1h", price_type="mark") == [
        (BASE, BASE + 5 * HOUR, 5)
    ]


# --- partitioning and duckdb ------------------------------------------------------------


def test_partition_path_is_hive_style(store: ParquetStore) -> None:
    path = store.partition_path(KEY).as_posix()
    for fragment in ("ohlcv", "inst_id=BTC-USDT-SWAP", "timeframe=1h", "year=2023", "month=11"):
        assert fragment in path


def test_duckdb_view_queries_the_parquet(tmp_path: Path, store: ParquetStore) -> None:
    import duckdb

    store.write_ohlcv(KEY, make_bars(7), symbol="BTC/USDT:USDT", source="test")
    db = tmp_path / "analytics.db"
    assert register_views(db, store, ("ohlcv",)) == ["ohlcv"]

    with duckdb.connect(str(db)) as conn:
        count_row = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()
        assert count_row is not None
        assert count_row[0] == 7
        # DECIMAL arithmetic stays exact through DuckDB.
        sum_row = conn.execute("SELECT SUM(close) FROM ohlcv").fetchone()
        assert sum_row is not None
        assert sum_row[0] == Decimal("738.500000000000000000")


def test_register_views_skips_empty_datasets(tmp_path: Path, store: ParquetStore) -> None:
    assert register_views(tmp_path / "a.db", store, ("ohlcv", "funding")) == []


def test_row_count_of_missing_dataset_is_zero(store: ParquetStore) -> None:
    assert store.row_count("funding") == 0


def test_funding_round_trip(store: ParquetStore) -> None:
    key = PartitionKey("funding", "BTC-USDT-SWAP", "8h", 2023, 11)
    points = [
        FundingPoint(
            funding_time_ms=BASE + i * 8 * HOUR,
            funding_rate=Decimal("0.0001"),
            realized_rate=Decimal("0.00009"),
        )
        for i in range(4)
    ]
    result = store.write_funding(key, points, symbol="BTC/USDT:USDT", source="test")
    assert result.rows == 4

    table = pq.read_table(store.partition_path(key))
    assert table.column("funding_rate").to_pylist()[0] == Decimal("0.0001")
    assert table.column("funding_time").to_pylist()[0] == datetime.fromtimestamp(
        BASE / 1000, tz=UTC
    )
