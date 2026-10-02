"""Universe selection and backfill orchestration, against a fake venue.

These modules had no unit coverage after M1's first pass, and that is precisely where the
most expensive bug of the milestone lived: `fetch_tickers()` returns spot on OKX, so the
universe came back **silently empty** with no error and the whole backfill did nothing. A
test with a fake source would have caught it in seconds. Hence the fakes.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from okxq.data.backfill import Backfiller
from okxq.data.manifest import Manifest, PartitionKey
from okxq.data.okx_public import TIMEFRAME_MS, Bar
from okxq.data.store import ParquetStore
from okxq.data.universe import as_instruments, select_universe

HOUR = TIMEFRAME_MS["1h"]
# Grid-aligned: the validator rejects off-grid bar opens, correctly, so an unaligned
# fixture would quarantine every bar and the test would be measuring nothing.
NOW = 1_790_000_000_000 // HOUR * HOUR
YEAR_MS = 365 * 86_400_000


def market(base: str, *, list_days_ago: int = 1000) -> dict[str, Any]:
    return {
        "symbol": f"{base}/USDT:USDT",
        "inst_id": f"{base}-USDT-SWAP",
        "inst_family": f"{base}-USDT",
        "base": base,
        "list_time_ms": NOW - list_days_ago * 86_400_000,
    }


def ticker(base_volume: str, last: str) -> dict[str, Any]:
    """A ticker shaped like OKX's: quoteVolume absent, volCcy24h in `info`."""
    return {"last": float(last), "info": {"volCcy24h": base_volume, "last": last}}


class FakeSource:
    """Minimal stand-in for OkxPublic - no network."""

    def __init__(
        self,
        markets: list[dict[str, Any]],
        tickers: dict[str, Any],
        bars: dict[tuple[str, str, str], list[Bar]] | None = None,
    ) -> None:
        self._markets = markets
        self._tickers = tickers
        self._bars = bars or {}
        self.request_count = 0
        self.fetch_calls: list[tuple[str, str, str]] = []

    def usdt_perp_universe(self, _markets: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._markets)

    def fetch_swap_tickers(self) -> dict[str, Any]:
        return self._tickers

    @staticmethod
    def quote_volume_24h(tk: dict[str, Any]) -> float | None:
        info = tk.get("info", {})
        vol, last = info.get("volCcy24h"), tk.get("last")
        if not vol or not last:
            return None
        return float(vol) * float(last)

    def milliseconds(self) -> int:
        return NOW

    def fetch_candles_back_to(
        self, inst_id: str, timeframe: str, *, stop_at_ms: int, price_type: str = "last"
    ) -> list[Bar]:
        self.request_count += 1
        self.fetch_calls.append((inst_id, timeframe, price_type))
        return self._bars.get((inst_id, timeframe, price_type), [])

    def fetch_funding_all(self, inst_id: str) -> list[Any]:
        self.request_count += 1
        return []


def instrument(base: str) -> dict[str, object]:
    """One instrument in the shape the backfiller consumes."""
    return {
        "symbol": f"{base}/USDT:USDT",
        "inst_id": f"{base}-USDT-SWAP",
        "inst_family": f"{base}-USDT",
    }


def make_bars(n: int, *, start: int) -> list[Bar]:
    return [
        Bar(
            ts_open_ms=start + i * HOUR,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(95),
            close=Decimal(105),
            volume_contracts=Decimal(1),
            volume_base=Decimal(1),
            volume_quote=Decimal(100),
            is_closed=True,
        )
        for i in range(n)
    ]


# --- universe selection -----------------------------------------------------------------


def test_universe_ranks_by_computed_turnover() -> None:
    markets = [market("BTC"), market("ETH"), market("SOL")]
    tickers = {
        "BTC/USDT:USDT": ticker("100", "80000"),  # 8.0m
        "ETH/USDT:USDT": ticker("1000", "4000"),  # 4.0m
        "SOL/USDT:USDT": ticker("10000", "200"),  # 2.0m
    }
    picked = select_universe(
        FakeSource(markets, tickers), {}, size=3, now_ms=NOW, min_quote_volume=1_000_000
    )
    assert [e.symbol for e in picked] == [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
    ]
    assert picked[0].quote_volume_24h == pytest.approx(8_000_000)


def test_universe_is_not_empty_when_tickers_are_swap_shaped() -> None:
    """Regression for the silent-empty-universe bug (venue fact V-6)."""
    picked = select_universe(
        FakeSource([market("BTC")], {"BTC/USDT:USDT": ticker("100", "80000")}),
        {},
        size=10,
        now_ms=NOW,
        min_quote_volume=1_000,
    )
    assert len(picked) == 1, "a correctly-shaped ticker set must yield a non-empty universe"


def test_universe_empty_when_tickers_missing() -> None:
    """The spot-ticker bug's signature: candidates exist but no ticker matches."""
    picked = select_universe(
        FakeSource([market("BTC"), market("ETH")], {}),  # tickers keyed differently
        {},
        size=10,
        now_ms=NOW,
        min_quote_volume=1_000,
    )
    assert picked == []


def test_universe_excludes_illiquid() -> None:
    picked = select_universe(
        FakeSource(
            [market("BTC"), market("TINY")],
            {
                "BTC/USDT:USDT": ticker("100", "80000"),
                "TINY/USDT:USDT": ticker("1", "1"),
            },
        ),
        {},
        size=10,
        now_ms=NOW,
        min_quote_volume=1_000_000,
    )
    assert [e.symbol for e in picked] == ["BTC/USDT:USDT"]


def test_universe_excludes_recent_listings() -> None:
    """New listings show price-discovery artefacts that are not representative."""
    picked = select_universe(
        FakeSource(
            [market("BTC"), market("NEW", list_days_ago=10)],
            {
                "BTC/USDT:USDT": ticker("100", "80000"),
                "NEW/USDT:USDT": ticker("100", "80000"),
            },
        ),
        {},
        size=10,
        now_ms=NOW,
        min_quote_volume=1_000,
        min_listing_age_days=180,
    )
    assert [e.symbol for e in picked] == ["BTC/USDT:USDT"]


def test_universe_selection_is_deterministic() -> None:
    """Equal turnover must break on symbol name, so repeated runs match."""
    markets = [market("CCC"), market("AAA"), market("BBB")]
    tickers = {f"{b}/USDT:USDT": ticker("100", "1000") for b in ("AAA", "BBB", "CCC")}
    src = FakeSource(markets, tickers)
    first = [e.symbol for e in select_universe(src, {}, size=3, now_ms=NOW, min_quote_volume=1)]
    second = [e.symbol for e in select_universe(src, {}, size=3, now_ms=NOW, min_quote_volume=1)]
    assert first == second == ["AAA/USDT:USDT", "BBB/USDT:USDT", "CCC/USDT:USDT"]


def test_universe_respects_size_cap() -> None:
    markets = [market(f"C{i}") for i in range(10)]
    tickers = {f"C{i}/USDT:USDT": ticker("100", str(1000 - i)) for i in range(10)}
    picked = select_universe(
        FakeSource(markets, tickers), {}, size=4, now_ms=NOW, min_quote_volume=1
    )
    assert len(picked) == 4


def test_as_instruments_carries_inst_family() -> None:
    """Index candles need the family (V-8), so it must survive this conversion."""
    picked = select_universe(
        FakeSource([market("BTC")], {"BTC/USDT:USDT": ticker("100", "80000")}),
        {},
        size=1,
        now_ms=NOW,
        min_quote_volume=1,
    )
    assert as_instruments(picked) == [
        {"symbol": "BTC/USDT:USDT", "inst_id": "BTC-USDT-SWAP", "inst_family": "BTC-USDT"}
    ]


# --- backfill orchestration -------------------------------------------------------------


@pytest.fixture
def wiring(tmp_path: Path) -> tuple[ParquetStore, Manifest]:
    return ParquetStore(tmp_path / "parquet"), Manifest(tmp_path / "m.db")


def test_backfill_writes_and_records(wiring: tuple[ParquetStore, Manifest]) -> None:
    store, manifest = wiring
    # Two full months in the past, so both are sealed rather than in-progress.
    start = NOW - 2 * YEAR_MS
    src = FakeSource([], {}, {("BTC-USDT-SWAP", "1h", "last"): make_bars(200, start=start)})
    stats = Backfiller(src, store, manifest, env="PAPER").run_ohlcv(
        [instrument("BTC")],
        "1h",
        horizon_ms=start - HOUR,
    )
    assert stats.instruments == 1
    assert stats.rows_written == 200
    assert stats.partitions_written >= 1
    assert manifest.totals()["rows"] == 200


def test_backfill_skips_sealed_partitions_on_rerun(
    wiring: tuple[ParquetStore, Manifest],
) -> None:
    """Idempotency at the orchestration level, not just the manifest."""
    store, manifest = wiring
    start = NOW - 2 * YEAR_MS
    bars = make_bars(200, start=start)
    inst = [instrument("BTC")]
    src = FakeSource([], {}, {("BTC-USDT-SWAP", "1h", "last"): bars})
    bf = Backfiller(src, store, manifest, env="PAPER")

    bf.run_ohlcv(inst, "1h", horizon_ms=start - HOUR)
    second = bf.run_ohlcv(inst, "1h", horizon_ms=start - HOUR)

    assert second.partitions_skipped >= 1
    assert second.partitions_written == 0, "sealed months must not be rewritten"


def test_index_price_type_fetches_by_family(wiring: tuple[ParquetStore, Manifest]) -> None:
    """V-8: fetch by instFamily, but store under inst_id so it still joins."""
    store, manifest = wiring
    start = NOW - 2 * YEAR_MS
    src = FakeSource([], {}, {("BTC-USDT", "1h", "index"): make_bars(50, start=start)})
    Backfiller(src, store, manifest, env="PAPER").run_ohlcv(
        [instrument("BTC")],
        "1h",
        horizon_ms=start - HOUR,
        price_type="index",
    )
    assert src.fetch_calls == [("BTC-USDT", "1h", "index")]
    # Stored under the instrument id, not the family.
    assert store.series_exists("ohlcv", "BTC-USDT-SWAP", "1h", "index")


def test_one_failing_instrument_does_not_end_the_run(
    wiring: tuple[ParquetStore, Manifest],
) -> None:
    """A single bad instrument is recorded and skipped; the rest still backfill."""
    store, manifest = wiring
    start = NOW - 2 * YEAR_MS

    class Exploding(FakeSource):
        def fetch_candles_back_to(self, inst_id: str, timeframe: str, **kw: Any) -> list[Bar]:
            if inst_id == "BAD-USDT-SWAP":
                raise RuntimeError("venue said no")
            return super().fetch_candles_back_to(inst_id, timeframe, **kw)

    src = Exploding([], {}, {("BTC-USDT-SWAP", "1h", "last"): make_bars(100, start=start)})
    stats = Backfiller(src, store, manifest, env="PAPER").run_ohlcv(
        [instrument("BAD"), instrument("BTC")],
        "1h",
        horizon_ms=start - HOUR,
    )
    assert stats.instruments == 1
    assert len(stats.errors) == 1
    assert "venue said no" in stats.errors[0]
    assert stats.rows_written == 100


def test_gaps_are_recorded_to_manifest(wiring: tuple[ParquetStore, Manifest]) -> None:
    store, manifest = wiring
    start = NOW - 2 * YEAR_MS
    bars = make_bars(10, start=start) + make_bars(10, start=start + 20 * HOUR)
    src = FakeSource([], {}, {("BTC-USDT-SWAP", "1h", "last"): bars})
    stats = Backfiller(src, store, manifest, env="PAPER").run_ohlcv(
        [instrument("BTC")],
        "1h",
        horizon_ms=start - HOUR,
    )
    assert stats.gap_runs == 1
    assert stats.missing_bars == 10
    assert manifest.totals()["gap_runs"] == 1
    # The gap is recorded, never filled.
    assert stats.rows_written == 20


def test_funding_partition_label_is_not_a_cadence(
    wiring: tuple[ParquetStore, Manifest],
) -> None:
    """V-13: the store must not assert 8h, since three instruments fund at 4h."""
    from okxq.data.backfill import FUNDING_TIMEFRAME

    assert FUNDING_TIMEFRAME == "funding"
    store, _ = wiring
    key = PartitionKey("funding", "BTC-USDT-SWAP", FUNDING_TIMEFRAME, 2026, 7)
    assert "timeframe=funding" in store.partition_path(key).as_posix()
    assert "8h" not in store.partition_path(key).as_posix()
