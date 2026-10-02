"""Pure adapter logic - the venue traps from docs/VENUE_FACTS.md, as regression tests.

No network: these cover the parsing and identifier rules that each cost real debugging
time at M1, so that a refactor cannot silently reintroduce them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from okxq.data.backfill import group_by_month, horizon_ms_for_years
from okxq.data.okx_public import BAR_MAP, MAX_CANDLE_LIMIT, TIMEFRAME_MS, Bar, OkxPublic

# --- V-7: quote volume must be computed, not read ---------------------------------------


def test_quote_volume_computed_from_base_and_last() -> None:
    """CCXT's quoteVolume is None for OKX swaps, so turnover is volCcy24h * last."""
    ticker = {"last": 86857.5, "info": {"volCcy24h": "104772.1087", "last": "86857.5"}}
    volume = OkxPublic.quote_volume_24h(ticker)
    assert volume is not None
    assert volume == pytest.approx(104772.1087 * 86857.5)
    assert volume == pytest.approx(9.1e9, rel=0.05)  # ~$9.1bn, the measured figure


def test_quote_volume_does_not_use_contract_count() -> None:
    """vol24h counts CONTRACTS; using it would overstate BTC turnover by 100x."""
    ticker = {
        "last": 86857.5,
        "baseVolume": 10477210.87,  # vol24h - contracts, the trap
        "info": {"volCcy24h": "104772.1087", "vol24h": "10477210.87", "last": "86857.5"},
    }
    volume = OkxPublic.quote_volume_24h(ticker)
    assert volume is not None
    # The wrong reading would be ~100x larger.
    assert volume < 10477210.87 * 86857.5 / 50


@pytest.mark.parametrize(
    "ticker",
    [
        {"last": 100.0, "info": {}},
        {"last": None, "info": {"volCcy24h": "10"}},
        {"info": {"volCcy24h": "", "last": ""}},
        {"last": 100.0, "info": {"volCcy24h": None}},
    ],
)
def test_quote_volume_missing_data_is_none(ticker: dict[str, object]) -> None:
    """Absent volume is None, never zero - zero would read as a real illiquid market."""
    assert OkxPublic.quote_volume_24h(ticker) is None


# --- V-8: index candles use instFamily --------------------------------------------------


def test_index_candles_use_instrument_family() -> None:
    """Passing instId to the index endpoint returns OKX error 51001."""
    market = {"id": "BTC-USDT-SWAP", "info": {"instFamily": "BTC-USDT"}}
    api = OkxPublic.__new__(OkxPublic)  # no network setup needed for this pure method
    assert api.candle_instrument(market, "index") == "BTC-USDT"


@pytest.mark.parametrize("price_type", ["last", "mark"])
def test_non_index_candles_use_instrument_id(price_type: str) -> None:
    market = {"id": "BTC-USDT-SWAP", "info": {"instFamily": "BTC-USDT"}}
    api = OkxPublic.__new__(OkxPublic)
    assert api.candle_instrument(market, price_type) == "BTC-USDT-SWAP"


def test_index_falls_back_to_inst_id_without_family() -> None:
    market = {"id": "BTC-USDT-SWAP", "info": {}}
    api = OkxPublic.__new__(OkxPublic)
    assert api.candle_instrument(market, "index") == "BTC-USDT-SWAP"


# --- V-1, timeframe mapping -------------------------------------------------------------


def test_candle_limit_is_three_hundred() -> None:
    """Measured: requesting 500 or 1000 silently returns 300."""
    assert MAX_CANDLE_LIMIT == 300


def test_bar_map_uses_utc_suffix_for_daily() -> None:
    """OKX needs a UTC-aligned bar for >= 6h, else days start at a local boundary."""
    assert BAR_MAP["1d"] == "1Dutc"
    assert BAR_MAP["6h"] == "6Hutc"
    assert BAR_MAP["1h"] == "1H"
    assert BAR_MAP["5m"] == "5m"


def test_every_mapped_timeframe_has_a_duration() -> None:
    assert set(BAR_MAP) == set(TIMEFRAME_MS)


@pytest.mark.parametrize(
    ("timeframe", "expected_ms"),
    [("1m", 60_000), ("5m", 300_000), ("1h", 3_600_000), ("1d", 86_400_000)],
)
def test_timeframe_durations(timeframe: str, expected_ms: int) -> None:
    assert TIMEFRAME_MS[timeframe] == expected_ms


# --- month grouping and horizons --------------------------------------------------------


def _bar(ms: int) -> Bar:
    return Bar(
        ts_open_ms=ms,
        open=Decimal(1),
        high=Decimal(1),
        low=Decimal(1),
        close=Decimal(1),
        volume_contracts=None,
        volume_base=None,
        volume_quote=None,
        is_closed=True,
    )


def test_group_by_month_splits_on_calendar_boundary() -> None:
    jan = 1_704_067_200_000  # 2024-01-01 00:00 UTC
    feb = 1_706_745_600_000  # 2024-02-01 00:00 UTC
    grouped = group_by_month([_bar(jan), _bar(jan + 86_400_000), _bar(feb)])
    assert sorted(grouped) == [(2024, 1), (2024, 2)]
    assert len(grouped[(2024, 1)]) == 2
    assert len(grouped[(2024, 2)]) == 1


def test_group_by_month_empty() -> None:
    assert group_by_month([]) == {}


def test_horizon_is_in_the_past() -> None:
    now = 1_790_000_000_000
    horizon = horizon_ms_for_years(now, 7.0)
    assert horizon < now
    assert (now - horizon) / (365.25 * 24 * 3600 * 1000) == pytest.approx(7.0)
