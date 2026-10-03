"""OKX public-endpoint data source (architecture §12, §16).

**Public endpoints only. This module never reads, constructs or transmits a credential.**
M1 is unauthenticated by design; the signed surface arrives at M6.

Venue facts established empirically at M1 (2026-10-02) and recorded in
``docs/VENUE_FACTS.md`` - do not change these without re-measuring:

* Max 300 rows per candle request; requesting more silently returns 300.
* Rows come back **descending** (newest first) and must be reversed.
* ``after`` pages **backward** (rows older than the given timestamp); ``before`` pages
  forward. CCXT's ``fetch_funding_rate_history`` maps only ``since`` -> ``before`` and
  **silently ignores** ``until``, so backward walks must use a raw ``after`` cursor.
* The ``confirm`` field is ``"1"`` for a closed bar and ``"0"`` for the in-progress bar.
* Funding-rate history is retained for only ~3 months.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import ccxt

from okxq.data.schema import to_decimal

#: Rows per request the venue honours for candle endpoints (measured, not assumed).
MAX_CANDLE_LIMIT: Final[int] = 300

#: Rows per request for funding-rate history.
MAX_FUNDING_LIMIT: Final[int] = 100

#: CCXT timeframe -> OKX `bar` parameter. OKX uses UTC-aligned suffixes for >= 6h bars.
BAR_MAP: Final[dict[str, str]] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6Hutc",
    "12h": "12Hutc",
    "1d": "1Dutc",
    "1w": "1Wutc",
}

TIMEFRAME_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


class VenueError(RuntimeError):
    """A public endpoint failed in a way retries did not resolve."""


@dataclass(frozen=True, slots=True)
class Bar:
    """One raw candle, prices still exact."""

    ts_open_ms: int
    open: Any
    high: Any
    low: Any
    close: Any
    volume_contracts: Any
    volume_base: Any
    volume_quote: Any
    is_closed: bool


@dataclass(frozen=True, slots=True)
class FundingPoint:
    """One realised funding observation."""

    funding_time_ms: int
    funding_rate: Any
    realized_rate: Any


class OkxPublic:
    """Thin wrapper over CCXT's OKX implicit public endpoints.

    CCXT types do not leak past this boundary (architecture §16): callers receive
    :class:`Bar` and :class:`FundingPoint` with ``Decimal`` fields.
    """

    def __init__(self, *, max_retries: int = 5) -> None:
        # No apiKey/secret/password is passed. There is nothing to authenticate with.
        self._ex = ccxt.okx({"enableRateLimit": True})
        self._max_retries = max_retries
        self._request_count = 0

    # --- lifecycle ---------------------------------------------------------------------

    def load_markets(self) -> dict[str, Any]:
        markets: dict[str, Any] = self._call(self._ex.load_markets)
        return markets

    @property
    def request_count(self) -> int:
        return self._request_count

    def milliseconds(self) -> int:
        return int(self._ex.milliseconds())

    def clock_skew_ms(self) -> int:
        """Local clock minus venue clock, latency-compensated.

        Harmless for M1's unsigned requests, but OKX rejects *signed* requests whose
        timestamp drifts too far, so this is checked at boot from M6 onward.
        """
        t0 = self._ex.milliseconds()
        server = self._call(self._ex.fetch_time)
        t1 = self._ex.milliseconds()
        return int(((t0 + t1) // 2) - server)

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke a venue call with bounded retry and exponential backoff plus jitter.

        Only transport-level and rate-limit failures are retried. A malformed response or
        a venue error code is not retried - it is surfaced, because retrying a request the
        venue actively rejected just burns the rate limit.
        """
        delay = 0.5
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                self._request_count += 1
                return fn(*args, **kwargs)
            except ccxt.RateLimitExceeded as exc:
                last = exc
                time.sleep(delay * (attempt + 1) * 2)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                last = exc
                # Jitter avoids synchronised retry storms across symbols.
                time.sleep(delay * (2**attempt) + (self._request_count % 7) * 0.01)
        raise VenueError(f"{getattr(fn, '__name__', fn)} failed after {self._max_retries}") from last

    # --- universe ----------------------------------------------------------------------

    def fetch_swap_tickers(self) -> dict[str, Any]:
        """24h tickers for swaps only.

        ``fetch_tickers()`` with no params returns **spot** on OKX, so swap symbols are
        absent entirely - a silent empty-universe bug rather than an error.
        """
        tickers: dict[str, Any] = self._call(self._ex.fetch_tickers, params={"type": "swap"})
        return tickers

    @staticmethod
    def quote_volume_24h(ticker: dict[str, Any]) -> float | None:
        """24h turnover in USDT.

        CCXT reports ``quoteVolume = None`` for OKX swaps, and its ``baseVolume`` is
        actually OKX's ``vol24h``, which counts **contracts**, not base units - for
        BTC-USDT-SWAP a contract is 0.01 BTC, so reading ``baseVolume`` as base currency
        overstates size by 100x. The raw ``volCcy24h`` field is the real base-currency
        volume, so turnover is that times last price.
        """
        info = ticker.get("info", {})
        base_volume = info.get("volCcy24h")
        last = ticker.get("last") or info.get("last")
        if not base_volume or not last:
            return None
        return float(base_volume) * float(last)

    def usdt_perp_universe(self, markets: dict[str, Any]) -> list[dict[str, Any]]:
        """Active USDT-margined perpetual swaps, with listing time where published."""
        out: list[dict[str, Any]] = []
        for market in markets.values():
            if not (market.get("swap") and market.get("active")):
                continue
            if market.get("quote") != "USDT" or market.get("settle") != "USDT":
                continue
            info = market.get("info", {})
            list_time = info.get("listTime")
            out.append(
                {
                    "symbol": market["symbol"],
                    "inst_id": market["id"],
                    "inst_family": info.get("instFamily"),
                    "base": market.get("base"),
                    "list_time_ms": int(list_time) if list_time else None,
                }
            )
        return sorted(out, key=lambda m: str(m["symbol"]))

    # --- candles -----------------------------------------------------------------------

    def _candle_endpoint(self, *, history: bool, price_type: str) -> Any:
        if price_type == "mark":
            return (
                self._ex.publicGetMarketHistoryMarkPriceCandles
                if history
                else self._ex.publicGetMarketMarkPriceCandles
            )
        if price_type == "index":
            return (
                self._ex.publicGetMarketHistoryIndexCandles
                if history
                else self._ex.publicGetMarketIndexCandles
            )
        return (
            self._ex.publicGetMarketHistoryCandles
            if history
            else self._ex.publicGetMarketCandles
        )

    def candle_instrument(self, market: dict[str, Any], price_type: str) -> str:
        """The instrument identifier the relevant candle endpoint expects.

        Index candles are published per *instrument family* (``BTC-USDT``), not per
        instrument (``BTC-USDT-SWAP``); passing the instId returns OKX error 51001
        "Instrument ID ... doesn't exist". Measured at M1.
        """
        if price_type == "index":
            family = market.get("info", {}).get("instFamily")
            if family:
                return str(family)
        return str(market["id"])

    def fetch_candles_page(
        self,
        inst_id: str,
        timeframe: str,
        *,
        after_ms: int | None = None,
        limit: int = MAX_CANDLE_LIMIT,
        price_type: str = "last",
        history: bool = True,
    ) -> list[Bar]:
        """Fetch one page of candles, oldest-first.

        Args:
            inst_id: Instrument identifier appropriate to ``price_type`` - see
                :meth:`candle_instrument` for the index-candle exception.
            after_ms: Backward cursor - returns bars strictly older than this timestamp.
                Omit for the most recent page.

        The in-progress bar (``confirm == "0"``) is dropped here, so a partial bar can
        never enter the store or reach a strategy.
        """
        request: dict[str, Any] = {
            "instId": inst_id,
            "bar": BAR_MAP[timeframe],
            "limit": str(min(limit, MAX_CANDLE_LIMIT)),
        }
        if after_ms is not None:
            request["after"] = str(after_ms)

        endpoint = self._candle_endpoint(history=history, price_type=price_type)
        response = self._call(endpoint, request)
        if str(response.get("code", "0")) != "0":
            raise VenueError(f"OKX error for {inst_id} {timeframe} {price_type}: {response}")

        # Last-price rows carry three volume fields; mark and index rows carry none, so
        # their row[5] is the `confirm` flag. Reading it as a volume would silently store
        # 0/1 as turnover.
        has_volume = price_type == "last"

        bars: list[Bar] = []
        for row in response.get("data", []):
            if str(row[-1]) != "1":
                continue  # in-progress bar
            bars.append(
                Bar(
                    ts_open_ms=int(row[0]),
                    open=to_decimal(row[1]),
                    high=to_decimal(row[2]),
                    low=to_decimal(row[3]),
                    close=to_decimal(row[4]),
                    volume_contracts=to_decimal(row[5]) if has_volume else None,
                    volume_base=to_decimal(row[6]) if has_volume else None,
                    volume_quote=to_decimal(row[7]) if has_volume else None,
                    is_closed=True,
                )
            )
        # The venue returns newest-first; the store and the backtester are chronological.
        bars.sort(key=lambda b: b.ts_open_ms)
        return bars

    def fetch_candles_back_to(
        self,
        inst_id: str,
        timeframe: str,
        *,
        stop_at_ms: int,
        price_type: str = "last",
        max_pages: int = 20_000,
    ) -> list[Bar]:
        """Walk backward from now until ``stop_at_ms`` or the venue runs out of history.

        Termination is on an empty page or a cursor that stops moving - never on a page
        count alone, so a venue that silently repeats a window cannot cause an infinite
        loop (the failure mode hit during Q-1 reconnaissance with the ignored ``until``).
        """
        collected: dict[int, Bar] = {}
        cursor: int | None = None
        pages = 0
        while pages < max_pages:
            page = self.fetch_candles_page(
                inst_id, timeframe, after_ms=cursor, price_type=price_type
            )
            if not page:
                break
            pages += 1
            oldest = page[0].ts_open_ms
            new_rows = {b.ts_open_ms: b for b in page if b.ts_open_ms not in collected}
            collected.update(new_rows)
            if not new_rows:
                break  # cursor is not advancing
            if oldest <= stop_at_ms:
                break
            cursor = oldest
        return [collected[k] for k in sorted(collected) if k >= stop_at_ms]

    # --- funding -----------------------------------------------------------------------

    def fetch_funding_all(self, inst_id: str, *, max_pages: int = 400) -> list[FundingPoint]:
        """Walk the venue's entire retained funding-rate history, oldest-first.

        Only ~3 months is retained (measured at M1), so this is cheap - and is why
        realised funding must be archived on an ongoing schedule from now on, since every
        week of delay permanently loses a week of ground truth.
        """
        collected: dict[int, FundingPoint] = {}
        cursor: int | None = None
        pages = 0
        while pages < max_pages:
            request: dict[str, Any] = {"instId": inst_id, "limit": str(MAX_FUNDING_LIMIT)}
            if cursor is not None:
                request["after"] = str(cursor)
            response = self._call(self._ex.publicGetPublicFundingRateHistory, request)
            if str(response.get("code", "0")) != "0":
                raise VenueError(f"OKX funding error for {inst_id}: {response}")
            rows = response.get("data", [])
            if not rows:
                break
            pages += 1
            before = len(collected)
            for row in rows:
                ms = int(row["fundingTime"])
                collected[ms] = FundingPoint(
                    funding_time_ms=ms,
                    funding_rate=to_decimal(row.get("fundingRate")),
                    realized_rate=to_decimal(row.get("realizedRate")),
                )
            if len(collected) == before:
                break  # cursor not advancing
            cursor = min(collected)
            if len(rows) < MAX_FUNDING_LIMIT:
                break  # venue exhausted its retained history
        return [collected[k] for k in sorted(collected)]


def utc(ms: int) -> datetime:
    """Convert epoch milliseconds to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
