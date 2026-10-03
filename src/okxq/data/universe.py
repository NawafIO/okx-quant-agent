"""Deterministic research-universe selection (architecture §8.2, M1 subset).

M1 needs a stable, reproducible symbol list rather than the full Market Scanner. Selection
is by published 24h quote volume with a minimum listing age, both recorded alongside the
result so any inclusion is auditable - the §8.2 requirement that a symbol's presence can
always be explained.

The full scanner (spread ceiling, open-interest floor, volatility and relative-strength
ranking) arrives with M3/M4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Instruments listed more recently than this are excluded: new listings show price
#: discovery artefacts that are not representative of normal behaviour.
MIN_LISTING_AGE_DAYS = 180

#: Minimum 24h quote volume in USDT. Below this, modelled slippage is fiction.
MIN_24H_QUOTE_VOLUME = 5_000_000


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    symbol: str
    inst_id: str
    quote_volume_24h: float
    list_time_ms: int | None
    listing_age_days: float | None
    #: Instrument family (``BTC-USDT``). Index candles are published per family, not per
    #: instrument - see OkxPublic.candle_instrument.
    inst_family: str | None = None


def select_universe(
    source: Any,
    markets: dict[str, Any],
    *,
    size: int,
    now_ms: int,
    min_quote_volume: float = MIN_24H_QUOTE_VOLUME,
    min_listing_age_days: int = MIN_LISTING_AGE_DAYS,
) -> list[UniverseEntry]:
    """Pick the top ``size`` liquid USDT perps, deterministically.

    Ties break on symbol name so repeated runs produce an identical list.
    """
    candidates = source.usdt_perp_universe(markets)
    tickers = source.fetch_swap_tickers()

    entries: list[UniverseEntry] = []
    for cand in candidates:
        ticker = tickers.get(cand["symbol"])
        if not ticker:
            continue
        # Computed, not read: CCXT's quoteVolume is None for OKX swaps and its baseVolume
        # is a contract count. See OkxPublic.quote_volume_24h.
        quote_volume = source.quote_volume_24h(ticker)
        if quote_volume is None or quote_volume < min_quote_volume:
            continue

        list_ms = cand.get("list_time_ms")
        age_days = (now_ms - list_ms) / 86_400_000 if list_ms else None
        if age_days is not None and age_days < min_listing_age_days:
            continue

        family = cand.get("inst_family")
        entries.append(
            UniverseEntry(
                symbol=str(cand["symbol"]),
                inst_id=str(cand["inst_id"]),
                quote_volume_24h=float(quote_volume),
                list_time_ms=list_ms,
                listing_age_days=round(age_days, 1) if age_days else None,
                inst_family=str(family) if family else None,
            )
        )

    entries.sort(key=lambda e: (-e.quote_volume_24h, e.symbol))
    return entries[:size]


def as_instruments(entries: list[UniverseEntry]) -> list[dict[str, object]]:
    """Convert to the plain dicts the backfiller consumes."""
    return [
        {"symbol": e.symbol, "inst_id": e.inst_id, "inst_family": e.inst_family} for e in entries
    ]


def with_archived(
    instruments: list[dict[str, object]],
    archived_ids: list[str],
    markets: dict[str, Any],
) -> list[dict[str, object]]:
    """The funding archive's instrument list: the current top-N PLUS every instrument the
    store already holds (prerequisite P-11).

    The top-N by turnover drifts week to week (measured: AAVE left and NIGHT joined between
    2026-10-02 and 2026-10-03). Archiving only the current top-N would silently stop
    archiving an instrument the moment it drops out, and its realised funding would then age
    out of OKX's ~95-day window for good - permanent loss of the one dataset that cannot be
    re-fetched. Instruments no longer listed are kept too (symbol falls back to the inst_id);
    their fetch failing is recorded per instrument and does not stop the run.
    """
    have = {str(i["inst_id"]) for i in instruments}
    by_id = {str(m.get("id")): m for m in markets.values() if isinstance(m, dict)}
    extra: list[dict[str, object]] = []
    for inst_id in sorted(set(archived_ids) - have):
        m = by_id.get(inst_id, {})
        family = (m.get("info") or {}).get("instFamily") if isinstance(m, dict) else None
        extra.append(
            {"symbol": str(m.get("symbol", inst_id)), "inst_id": inst_id, "inst_family": family}
        )
    return instruments + extra
