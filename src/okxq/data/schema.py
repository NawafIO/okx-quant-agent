"""Parquet schemas for the historical store (architecture §12, §20.1).

Prices and quantities are ``decimal128(38, 18)``, not ``float64``. This is contract rule
D-1 carried into storage: OKX's REST response carries prices as strings, so ingesting via
the raw endpoint keeps them exact, and DuckDB arithmetic over DECIMAL stays exact too.

CCXT's unified ``fetch_ohlcv`` parses prices to Python ``float`` and would discard that
exactness at the ingest boundary, which is why :mod:`okxq.data.okx_public` calls the
implicit endpoints instead.
"""

from __future__ import annotations

import decimal
from decimal import Decimal

import pyarrow as pa

#: Total digits and fractional digits for stored decimals. 20 integer digits is far beyond
#: any crypto price or size; 18 fractional digits covers wei-scale precision.
DECIMAL_PRECISION = 38
DECIMAL_SCALE = 18

PRICE = pa.decimal128(DECIMAL_PRECISION, DECIMAL_SCALE)

_QUANTUM = Decimal(1).scaleb(-DECIMAL_SCALE)


def to_decimal(raw: str | int | Decimal | None) -> Decimal | None:
    """Convert a raw venue string to an exactly-scaled Decimal.

    Built from the string, never via float, so ``'0.1'`` stays ``0.1``. Empty strings -
    which OKX returns for some optional numeric fields - become ``None`` rather than zero,
    because zero is a value and absence is not.

    Quantising runs in a local context with ``prec = DECIMAL_PRECISION``. Python's *default*
    context is ``prec=28``, which is fewer digits than this schema's 38 and silently too
    small: a value with more than 10 integer digits - an ordinary daily quote volume, e.g.
    $91bn - raises ``InvalidOperation`` once 18 fractional digits are appended. Measured at
    M1, where it rejected every 1d bar for BTC and ETH.

    Raises:
        decimal.InvalidOperation: if the value genuinely exceeds the stored precision.
            Deliberately not caught: a number too large for the column is a schema problem
            to fix, not a row to silently drop.
    """
    if raw is None or raw == "":
        return None
    with decimal.localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return Decimal(str(raw)).quantize(_QUANTUM)


OHLCV_SCHEMA = pa.schema(
    [
        pa.field("inst_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("ts_open_ms", pa.int64(), nullable=False),
        pa.field("ts_open", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("open", PRICE, nullable=False),
        pa.field("high", PRICE, nullable=False),
        pa.field("low", PRICE, nullable=False),
        pa.field("close", PRICE, nullable=False),
        # OKX returns three volume representations; all are kept so nothing downstream has
        # to guess which one a figure came from.
        pa.field("volume_contracts", PRICE, nullable=True),
        pa.field("volume_base", PRICE, nullable=True),
        pa.field("volume_quote", PRICE, nullable=True),
        # Always True in the store: partial bars are dropped at ingest (architecture §12).
        pa.field("is_closed", pa.bool_(), nullable=False),
        pa.field("price_type", pa.string(), nullable=False),  # last | mark | index
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", pa.timestamp("ms", tz="UTC"), nullable=False),
    ]
)

FUNDING_SCHEMA = pa.schema(
    [
        pa.field("inst_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("funding_time_ms", pa.int64(), nullable=False),
        pa.field("funding_time", pa.timestamp("ms", tz="UTC"), nullable=False),
        # The rate set for the period, and the rate actually charged.
        pa.field("funding_rate", PRICE, nullable=True),
        pa.field("realized_rate", PRICE, nullable=True),
        pa.field("source", pa.string(), nullable=False),
        pa.field("ingested_at", pa.timestamp("ms", tz="UTC"), nullable=False),
    ]
)
