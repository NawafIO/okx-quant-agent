"""Trial counter and the sealed holdout (M2 acceptance: research code reading it raises)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from okxq.audit.chain import AuditChain, read_chain
from okxq.backtest import holdout as h
from okxq.backtest.gates import FROZEN
from okxq.backtest.trials import Trial, TrialLog
from okxq.data.manifest import PartitionKey
from okxq.data.okx_public import TIMEFRAME_MS, Bar, FundingPoint
from okxq.data.store import ParquetStore
from okxq.errors import AuditChainError, SafetyError

HOUR = TIMEFRAME_MS["1h"]
HOLDOUT = FROZEN.holdout_start_ms  # 2025-10-01T00:00Z
INST = "BTC-USDT-SWAP"


def trial(sr: float | None, purpose: str = "selection") -> Trial:
    return Trial("s", "1", {"k": 1}, 0, 1, purpose, 120, sr, "1.6", "d" * 64)


# --- trial counter -----------------------------------------------------------------------


def test_trial_log_counts_every_trial_and_their_sharpe_variance(tmp_path: Path) -> None:
    log = TrialLog(tmp_path / "trials.jsonl")
    assert log.stats().n_trials == 0
    for sr in (0.01, 0.03, None, 0.05):
        log.record(trial(sr))
    st = log.stats()
    assert st.n_trials == 4  # an undefined Sharpe still counts as a trial tried
    assert st.sharpe_variance == pytest.approx(0.0004)  # sample variance of .01, .03, .05
    assert st.chain_head == read_chain(tmp_path / "trials.jsonl")[-1].hash


def test_editing_the_trial_log_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "trials.jsonl"
    log = TrialLog(path)
    for sr in (0.01, 0.02, 0.03):
        log.record(trial(sr))
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # quietly forget an embarrassing trial
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AuditChainError):
        log.stats()
    with pytest.raises(AuditChainError):
        TrialLog(path)


# --- store fixture -----------------------------------------------------------------------


def _bars(start_ms: int, n: int) -> list[Bar]:
    p = Decimal(100)
    return [
        Bar(start_ms + i * HOUR, p, p + 1, p - 1, p, Decimal(10), Decimal(1), Decimal(100), True)
        for i in range(n)
    ]


def _key(ts: int, price_type: str = "last") -> PartitionKey:
    d = datetime.fromtimestamp(ts / 1000, tz=UTC)
    return PartitionKey("ohlcv", INST, "1h", d.year, d.month, price_type)


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    s = ParquetStore(tmp_path / "parquet")
    before, after = _bars(HOLDOUT - 48 * HOUR, 48), _bars(HOLDOUT, 48)
    for price_type in ("last", "mark"):
        s.write_ohlcv(_key(before[0].ts_open_ms, price_type), before, symbol="BTC", source="t")
        s.write_ohlcv(_key(after[0].ts_open_ms, price_type), after, symbol="BTC", source="t")
    d = datetime.fromtimestamp(HOLDOUT / 1000, tz=UTC)
    pts = [
        FundingPoint(HOLDOUT + i * 8 * HOUR, Decimal("0.0001"), Decimal("0.0001")) for i in range(6)
    ]
    s.write_funding(
        PartitionKey("funding", INST, "funding", d.year, d.month), pts, symbol="BTC", source="t"
    )
    return s


# --- research door: sealed ---------------------------------------------------------------


def test_research_reads_before_the_holdout(store: ParquetStore) -> None:
    bars = h.load_research_bars(store, INST, "1h", HOLDOUT - 48 * HOUR, HOLDOUT)
    assert len(bars) == 48
    assert bars.ts_open_ms[-1] + HOUR == HOLDOUT


def test_research_code_reading_the_holdout_range_raises(store: ParquetStore) -> None:
    with pytest.raises(h.HoldoutSealedError, match="sealed holdout"):
        h.load_research_bars(store, INST, "1h", HOLDOUT - 48 * HOUR, HOLDOUT + HOUR)
    with pytest.raises(h.HoldoutSealedError):
        h.load_research_funding(store, INST, HOLDOUT - HOUR, HOLDOUT + 8 * HOUR)
    # It is a SafetyError: never retried, never swallowed.
    assert issubclass(h.HoldoutSealedError, SafetyError)


def test_the_holdout_is_open_ended(store: ParquetStore) -> None:
    far_future = HOLDOUT + 10 * 365 * 24 * HOUR
    with pytest.raises(h.HoldoutSealedError):
        h.load_research_bars(store, INST, "1h", far_future, far_future + HOUR)


# --- calibration door --------------------------------------------------------------------


def test_calibration_reads_mark_and_funding_inside_the_holdout_and_logs_it(
    store: ParquetStore, tmp_path: Path
) -> None:
    log = AuditChain(tmp_path / "calib.jsonl")
    mark = h.load_calibration_bars(store, log, INST, "mark", HOLDOUT, HOLDOUT + 48 * HOUR)
    fund = h.load_calibration_funding(store, log, INST, HOLDOUT, HOLDOUT + 48 * HOUR)
    assert len(mark) == 48
    assert len(fund) == 6
    assert [r.kind for r in read_chain(tmp_path / "calib.jsonl")] == ["calibration_read"] * 2


def test_calibration_cannot_read_last_price_bars(store: ParquetStore, tmp_path: Path) -> None:
    with pytest.raises(h.HoldoutSealedError, match="calibration may read"):
        h.load_calibration_bars(
            store, AuditChain(tmp_path / "c.jsonl"), INST, "last", HOLDOUT, HOLDOUT + HOUR
        )


# --- holdout door: exactly once ----------------------------------------------------------


def test_holdout_is_unsealed_exactly_once_per_strategy(store: ParquetStore, tmp_path: Path) -> None:
    path = tmp_path / "holdout.jsonl"
    key = h.unseal_holdout(path, "trend-v1", "abc123")
    assert len(h.load_holdout_bars(key, store, INST, "1h", HOLDOUT + 48 * HOUR)) == 48
    with pytest.raises(h.HoldoutSealedError, match="exactly once"):
        h.unseal_holdout(path, "trend-v1", "def456")  # re-tuned code: still refused
    h.unseal_holdout(path, "other-v1", "abc123")  # a different strategy has its own read
    rec = read_chain(path)[0]
    assert rec.payload["gates_sha256"] == FROZEN.sha256()
    assert json.loads(rec.to_json())["kind"] == "holdout_unseal"


def test_research_funding_prefers_realised_over_modelled(
    store: ParquetStore, tmp_path: Path
) -> None:
    d = datetime.fromtimestamp((HOLDOUT - 48 * HOUR) / 1000, tz=UTC)
    modelled = [
        FundingPoint(HOLDOUT - 48 * HOUR + i * 8 * HOUR, Decimal("0.0003"), Decimal("0.0003"))
        for i in range(6)
    ]
    store.write_funding(
        PartitionKey(h.MODELLED_FUNDING, INST, "funding", d.year, d.month),
        modelled,
        symbol="BTC",
        source="model-v0",
    )
    rates = h.load_research_funding(store, INST, HOLDOUT - 48 * HOUR, HOLDOUT)
    assert len(rates) == 6
    assert all(r.modelled for r in rates)
