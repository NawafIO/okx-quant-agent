"""Data contracts: D-1 (no float money), geometry validation, round-trips."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from okxq.contracts import (
    Candle,
    RiskCheckResult,
    SentimentScore,
    Signal,
    TradeProposal,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _candle(**over: object) -> Candle:
    base: dict[str, object] = {
        "env": "PAPER",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "ts_open": NOW,
        "open": "100",
        "high": "110",
        "low": "95",
        "close": "105",
        "volume": "12.5",
        "is_closed": True,
        "source": "test",
        "ingested_at": NOW,
    }
    return Candle(**{**base, **over})


def _signal(**over: object) -> Signal:
    base: dict[str, object] = {
        "env": "PAPER",
        "strategy_id": "trend_v1",
        "strategy_version": "abc1234",
        "symbol": "BTC/USDT:USDT",
        "ts": NOW,
        "side": "LONG",
        "entry_ref": "100",
        "stop_loss": "98",
        "take_profit": ("104",),
        "conviction": "0.7",
    }
    return Signal(**{**base, **over})


# --- D-1: no float for money ------------------------------------------------------------


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_float_money_is_rejected(field: str) -> None:
    """M0 acceptance: monetary fields reject float outright (contract rule D-1)."""
    with pytest.raises(ValidationError, match="D-1 violation"):
        _candle(**{field: 105.0})


def test_string_money_is_exact() -> None:
    """The reason D-1 exists: Decimal('0.1') is exact, Decimal(0.1) is not."""
    candle = _candle(open="0.1", low="0.1", high="0.1", close="0.1")
    assert candle.open == Decimal("0.1")
    assert str(candle.open) == "0.1"


def test_int_and_decimal_money_accepted() -> None:
    assert _candle(volume=0).volume == Decimal(0)
    assert _candle(volume=Decimal("1.5")).volume == Decimal("1.5")


def test_float_rejected_on_signal_and_proposal() -> None:
    with pytest.raises(ValidationError, match="D-1 violation"):
        _signal(entry_ref=100.0)


# --- OHLC sanity ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"high": "90"}, "high below close"),
        ({"low": "120"}, "low above open"),
        ({"open": "0"}, "non-positive price"),
        ({"close": "-5"}, "negative price"),
        ({"volume": "-1"}, "negative volume"),
    ],
)
def test_invalid_ohlc_rejected(over: dict[str, str], reason: str) -> None:
    with pytest.raises(ValidationError):
        _candle(**over)


def test_naive_timestamp_rejected() -> None:
    """A bar timestamp without a timezone is ambiguous and must not be accepted."""
    with pytest.raises(ValidationError):
        _candle(ts_open=datetime(2026, 10, 2, 12, 0))  # deliberately naive


# --- Signal geometry --------------------------------------------------------------------


def test_signal_requires_a_stop_loss() -> None:
    """The system has no concept of an unprotected entry."""
    with pytest.raises(ValidationError):
        Signal(  # type: ignore[call-arg]
            env="PAPER",
            strategy_id="s",
            strategy_version="v",
            symbol="BTC/USDT:USDT",
            ts=NOW,
            side="LONG",
            entry_ref="100",
            take_profit=("104",),
            conviction="0.5",
        )


def test_signal_requires_at_least_one_target() -> None:
    with pytest.raises(ValidationError):
        _signal(take_profit=())


@pytest.mark.parametrize(
    "over",
    [
        {"side": "LONG", "stop_loss": "102"},  # stop above entry on a long
        {"side": "SHORT", "stop_loss": "98", "take_profit": ("96",)},  # stop below entry
        {"side": "LONG", "take_profit": ("99",)},  # target below entry on a long
    ],
)
def test_wrong_side_stop_or_target_rejected(over: dict[str, object]) -> None:
    """Wrong-side geometry is a bug, not a bad trade: it inverts the sizing denominator."""
    with pytest.raises(ValidationError):
        _signal(**over)


def test_valid_short_signal() -> None:
    sig = _signal(side="SHORT", stop_loss="102", take_profit=("96", "92"))
    assert sig.side == "SHORT"
    assert sig.stop_loss == Decimal("102")


def test_conviction_bounded() -> None:
    for bad in ("-0.1", "1.1"):
        with pytest.raises(ValidationError):
            _signal(conviction=bad)


# --- bounded LLM output -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["-1.5", "1.5", "99"])
def test_sentiment_score_is_clamped(bad: str) -> None:
    """An LLM must not be able to emit an unbounded number into the system (§5.2)."""
    with pytest.raises(ValidationError):
        SentimentScore(
            env="PAPER",
            symbol="BTC/USDT:USDT",
            ts=NOW,
            score=bad,
            confidence="0.8",
            model_id="m",
        )


def test_sentiment_score_accepts_bounds() -> None:
    for ok in ("-1", "0", "1"):
        assert SentimentScore(
            env="PAPER",
            symbol="BTC/USDT:USDT",
            ts=NOW,
            score=ok,
            confidence="1",
            model_id="m",
        ).score == Decimal(ok)


# --- immutability and round-trip --------------------------------------------------------


def test_records_are_frozen() -> None:
    """A record is a fact; facts are not edited in place."""
    candle = _candle()
    with pytest.raises(ValidationError):
        candle.close = Decimal("1")  # type: ignore[misc]


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _candle(unexpected="x")


def test_round_trip_candle() -> None:
    candle = _candle()
    assert Candle.model_validate_json(candle.model_dump_json()) == candle


def test_round_trip_proposal() -> None:
    proposal = TradeProposal(
        proposal_id=uuid4(),
        env="PAPER",
        signal=_signal(),
        qty_base="0.25",
        notional_quote="25",
        risk_amount="0.5",
        risk_pct_of_equity="0.005",
        leverage="1",
        risk_checks=(
            RiskCheckResult(check_id="RC-05", passed=True, observed="0.005", limit="0.005"),
        ),
        verdict="APPROVED",
        expires_at=NOW + timedelta(seconds=60),
    )
    assert TradeProposal.model_validate_json(proposal.model_dump_json()) == proposal


def test_proposal_executability_requires_approval_and_freshness() -> None:
    """A stale or rejected proposal is unexecutable (architecture §19.2 controls 4-5)."""

    def build(verdict: str, expires: datetime) -> TradeProposal:
        return TradeProposal(
            proposal_id=uuid4(),
            env="PAPER",
            signal=_signal(),
            qty_base="1",
            notional_quote="100",
            risk_amount="2",
            risk_pct_of_equity="0.005",
            leverage="1",
            verdict=verdict,
            expires_at=expires,
        )

    assert build("APPROVED", NOW + timedelta(seconds=60)).is_executable_at(NOW) is True
    assert build("APPROVED", NOW - timedelta(seconds=1)).is_executable_at(NOW) is False
    assert build("REJECTED", NOW + timedelta(seconds=60)).is_executable_at(NOW) is False


# --- property test ----------------------------------------------------------------------


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_no_float_ever_becomes_money(value: float) -> None:
    """Property: *no* float, however ordinary, is accepted as a monetary value.

    Hypothesis is used here because the dangerous cases are the innocuous-looking ones -
    a float that round-trips through str() cleanly still carries binary representation
    error into a sizing calculation.
    """
    with pytest.raises(ValidationError):
        _candle(volume=value)
