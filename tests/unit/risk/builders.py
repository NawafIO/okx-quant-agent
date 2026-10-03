"""Valid-by-default inputs for risk-engine tests; each test changes one thing."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from okxq.contracts import RegimeLabel, SentimentScore, Signal
from okxq.risk.inputs import MarketFacts, OpenPosition, PortfolioSnapshot, QualInputs

D = Decimal

T = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
T_MS = int(T.timestamp() * 1000)
SYM = "BTC-USDT-SWAP"


def signal(**over: Any) -> Signal:
    long = over.get("side", "LONG") == "LONG"
    fields: dict[str, Any] = {
        "env": "PAPER",
        "strategy_id": "s",
        "strategy_version": "v",
        "symbol": SYM,
        "ts": T,
        "side": "LONG",
        "entry_ref": D("100"),
        "stop_loss": D("98") if long else D("102"),
        "take_profit": (D("104") if long else D("96"),),
        "conviction": D(1),
    }
    return Signal(**(fields | over))


def snap(**over: Any) -> PortfolioSnapshot:
    base = PortfolioSnapshot(
        env="PAPER",
        cycle_ts_ms=T_MS,
        kill_switch_engaged=False,
        equity=D(10_000),
        free_margin=D(10_000),
        high_water_mark=D(10_000),
        day_open_equity=D(10_000),
        positions=(),
        entries_last_hour=0,
        consecutive_losses=0,
        last_loss_ts_ms=None,
        signals_this_bar=(),
        api_error_breach=False,
    )
    return replace(base, **over)


def facts(**over: Any) -> MarketFacts:
    base = MarketFacts(
        env="PAPER",
        symbol=SYM,
        tick_size=D("0.1"),
        lot_size_base=D("0.0001"),
        min_size_base=D("0.0001"),
        max_size_base=D(1000),
        mmr=D("0.004"),
        atr_1h=D(1),
        last_bar_close_ts_ms=T_MS - 60_000,
        timeframe_ms=3_600_000,
        tradable=True,
        spec_sha="spec",
    )
    return replace(base, **over)


def regime(**over: Any) -> RegimeLabel:
    fields: dict[str, Any] = {
        "env": "PAPER",
        "symbol": SYM,
        "ts": T - timedelta(hours=1),
        "regime": "TREND_UP",
        "confidence": D(1),
        "determined_by": "RULE",
    }
    return RegimeLabel(**(fields | over))


def sentiment(**over: Any) -> SentimentScore:
    fields: dict[str, Any] = {
        "env": "PAPER",
        "symbol": SYM,
        "ts": T,
        "score": D(0),
        "confidence": D(1),
        "model_id": "m",
    }
    return SentimentScore(**(fields | over))


def qual(**over: Any) -> QualInputs:
    return replace(QualInputs(regime=regime(), sentiment=None), **over)


def position(**over: Any) -> OpenPosition:
    base = OpenPosition(symbol="ETH-USDT-SWAP", side="LONG", qty_base=D(1), stop=D(90), mark=D(100))
    return replace(base, **over)
