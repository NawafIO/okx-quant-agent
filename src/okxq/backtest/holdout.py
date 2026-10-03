"""The sanctioned data path for research, with the sealed holdout enforced in code.

Three doors, deliberately separate:

* **Research** (:func:`load_research_bars`, :func:`load_research_funding`): any request that
  reaches into the holdout raises :class:`HoldoutSealedError` before a byte is read. The
  holdout is open-ended (``[start, inf)``), so data archived later stays sealed too (A-3).
* **Calibration** (:func:`load_calibration_bars`, :func:`load_calibration_funding`): the
  funding model (deliverable D1) must be validated against realised funding, and realised
  funding exists ONLY from 2026-06-29 - entirely inside the holdout. This door may read into
  the holdout but only ``mark``/``index`` candles and funding, never ``last`` OHLCV that a
  strategy trades on, and every read is logged to the audit chain (finding A-3).
* **Holdout** (:func:`unseal_holdout` then :func:`load_holdout_bars`): once per strategy,
  logged; a second unseal for the same strategy raises. If the holdout fails, the strategy
  is dead - it is not re-tuned on the holdout.

Research and strategy code must not import ``duckdb``, ``pyarrow`` or the store directly; a
guard test scans for that (finding A-11). Python cannot make this airtight - the chain is the
evidence, the guard is the tripwire.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import duckdb

from okxq.audit.chain import AuditChain, read_chain
from okxq.backtest.gates import FROZEN
from okxq.backtest.types import BarSeries, FundingRate
from okxq.data.okx_public import TIMEFRAME_MS
from okxq.data.store import ParquetStore
from okxq.env.profiles import EnvProfile
from okxq.errors import SafetyError

CALIBRATION_PRICE_TYPES = frozenset({"mark", "index"})
#: Realised funding as archived by M1, and the modelled series D1 writes.
REALISED_FUNDING = "funding"
MODELLED_FUNDING = "funding_modelled"
FUNDING_TIMEFRAME = "funding"


class HoldoutSealedError(SafetyError):
    """Research code attempted to read the sealed holdout, or to read it twice."""


def _check_sealed(end_ms: int) -> None:
    if end_ms > FROZEN.holdout_start_ms:
        raise HoldoutSealedError(
            f"requested data up to {end_ms} reaches the sealed holdout starting "
            f"{FROZEN.holdout_start_utc}; research may read only before it"
        )


def _read_bars(
    store: ParquetStore,
    inst_id: str,
    timeframe: str,
    price_type: str,
    start_ms: int,
    end_ms: int,
) -> BarSeries:
    tf = TIMEFRAME_MS[timeframe]
    if not store.series_exists("ohlcv", inst_id, timeframe, price_type):
        rows: list[tuple[int, Decimal, Decimal, Decimal, Decimal, Decimal | None]] = []
    else:
        with duckdb.connect() as conn:
            rows = conn.execute(
                "SELECT ts_open_ms, open, high, low, close, volume_base "
                "FROM read_parquet(?, hive_partitioning=true) "
                "WHERE ts_open_ms >= ? AND ts_open_ms + ? <= ? ORDER BY ts_open_ms",
                [store.series_glob("ohlcv", inst_id, timeframe, price_type), start_ms, tf, end_ms],
            ).fetchall()
    return BarSeries(
        inst_id=inst_id,
        timeframe_ms=tf,
        ts_open_ms=tuple(int(r[0]) for r in rows),
        open=tuple(r[1] for r in rows),
        high=tuple(r[2] for r in rows),
        low=tuple(r[3] for r in rows),
        close=tuple(r[4] for r in rows),
        # mark/index carry no volume (fact V-9); None is not zero, but a bar with unknown
        # volume can absorb nothing, which is the conservative reading for fills.
        volume_base=tuple(r[5] if r[5] is not None else Decimal(0) for r in rows),
    )


def _read_funding(
    store: ParquetStore, dataset: str, inst_id: str, start_ms: int, end_ms: int, modelled: bool
) -> list[FundingRate]:
    if not store.series_exists(dataset, inst_id, FUNDING_TIMEFRAME):
        return []
    with duckdb.connect() as conn:
        rows = conn.execute(
            "SELECT funding_time_ms, realized_rate, funding_rate "
            "FROM read_parquet(?, hive_partitioning=true) "
            "WHERE funding_time_ms >= ? AND funding_time_ms < ? ORDER BY funding_time_ms",
            [store.series_glob(dataset, inst_id, FUNDING_TIMEFRAME), start_ms, end_ms],
        ).fetchall()
    out = []
    for ts, realised, published in rows:
        rate = realised if realised is not None else published
        if rate is not None:
            out.append(FundingRate(int(ts), rate, modelled=modelled))
    return out


# --- research door -----------------------------------------------------------------------


def load_research_bars(
    store: ParquetStore,
    inst_id: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> BarSeries:
    """Last-price bars closing in ``[start_ms, end_ms]``. Raises if that reaches the holdout."""
    _check_sealed(end_ms)
    return _read_bars(store, inst_id, timeframe, "last", start_ms, end_ms)


def load_research_funding(
    store: ParquetStore,
    inst_id: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRate]:
    """Realised funding where it exists, else the D1 modelled series, flagged as modelled."""
    _check_sealed(end_ms)
    realised = _read_funding(store, REALISED_FUNDING, inst_id, start_ms, end_ms, False)
    have = {r.ts_ms for r in realised}
    modelled = [
        r
        for r in _read_funding(store, MODELLED_FUNDING, inst_id, start_ms, end_ms, True)
        if r.ts_ms not in have
    ]
    return sorted(realised + modelled, key=lambda r: r.ts_ms)


# --- calibration door --------------------------------------------------------------------


def load_calibration_bars(
    store: ParquetStore,
    log: AuditChain,
    inst_id: str,
    price_type: str,
    start_ms: int,
    end_ms: int,
    *,
    timeframe: str = "1h",
) -> BarSeries:
    if price_type not in CALIBRATION_PRICE_TYPES:
        raise HoldoutSealedError(
            f"calibration may read {sorted(CALIBRATION_PRICE_TYPES)} candles only, not "
            f"{price_type!r}: last-price bars are what strategies trade on"
        )
    log.append(
        "calibration_read",
        {
            "dataset": "ohlcv",
            "inst_id": inst_id,
            "price_type": price_type,
            "window": [start_ms, end_ms],
        },
    )
    return _read_bars(store, inst_id, timeframe, price_type, start_ms, end_ms)


def load_calibration_funding(
    store: ParquetStore, log: AuditChain, inst_id: str, start_ms: int, end_ms: int
) -> list[FundingRate]:
    log.append(
        "calibration_read",
        {"dataset": REALISED_FUNDING, "inst_id": inst_id, "window": [start_ms, end_ms]},
    )
    return _read_funding(store, REALISED_FUNDING, inst_id, start_ms, end_ms, False)


# --- holdout door ------------------------------------------------------------------------

UNSEAL_KIND = "holdout_unseal"


@dataclass(frozen=True)
class HoldoutKey:
    strategy_id: str
    code_sha: str
    start_ms: int


def unseal_holdout(profile: EnvProfile, strategy_id: str, code_sha: str) -> HoldoutKey:
    """Record the single permitted holdout read for ``strategy_id``. Raises on a second.

    The record goes on the environment's own audit chain, not a caller-chosen file, so a
    fresh path cannot buy a fresh "exactly once" (checkpoint-3 finding 2).
    """
    log_path = profile.audit_log
    if log_path.exists():
        for rec in read_chain(log_path):
            if rec.kind == UNSEAL_KIND and rec.payload.get("strategy_id") == strategy_id:
                raise HoldoutSealedError(
                    f"holdout already unsealed for {strategy_id} at {rec.ts} "
                    f"(code {rec.payload.get('code_sha')}); it is read exactly once"
                )
    AuditChain(log_path).append(
        UNSEAL_KIND,
        {"strategy_id": strategy_id, "code_sha": code_sha, "gates_sha256": FROZEN.sha256()},
    )
    return HoldoutKey(strategy_id, code_sha, FROZEN.holdout_start_ms)


def load_holdout_bars(
    key: HoldoutKey, store: ParquetStore, inst_id: str, timeframe: str, end_ms: int
) -> BarSeries:
    return _read_bars(store, inst_id, timeframe, "last", key.start_ms, end_ms)


def load_holdout_funding(
    key: HoldoutKey, store: ParquetStore, inst_id: str, end_ms: int
) -> list[FundingRate]:
    realised = _read_funding(store, REALISED_FUNDING, inst_id, key.start_ms, end_ms, False)
    have = {r.ts_ms for r in realised}
    modelled = [
        r
        for r in _read_funding(store, MODELLED_FUNDING, inst_id, key.start_ms, end_ms, True)
        if r.ts_ms not in have
    ]
    return sorted(realised + modelled, key=lambda r: r.ts_ms)
