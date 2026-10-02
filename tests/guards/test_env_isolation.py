"""GUARD: environment decoupling and contract rule D-2 (architecture §4, §6.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from okxq.contracts import Candle, Env, require_env
from okxq.env.profiles import build_profile
from okxq.errors import ConfigError, EnvironmentMismatchError, SafetyError
from okxq.run import main

pytestmark = pytest.mark.guard


def _candle(env: Env) -> Candle:
    now = datetime.now(tz=UTC)
    return Candle(
        env=env,
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        ts_open=now,
        open="100",
        high="110",
        low="95",
        close="105",
        volume="12.5",
        is_closed=True,
        source="test",
        ingested_at=now,
    )


def test_matching_env_passes() -> None:
    require_env(_candle("PAPER"), "PAPER")


@pytest.mark.parametrize(
    ("record_env", "process_env"),
    [("DEMO", "PAPER"), ("PAPER", "DEMO"), ("LIVE", "PAPER"), ("PAPER", "LIVE")],
)
def test_mismatched_env_raises(record_env: Env, process_env: Env) -> None:
    """A foreign-environment record must halt, not be coerced."""
    with pytest.raises(EnvironmentMismatchError):
        require_env(_candle(record_env), process_env)


def test_env_mismatch_is_a_safety_error() -> None:
    assert issubclass(EnvironmentMismatchError, SafetyError)


def test_env_is_required_on_records() -> None:
    """There is no default environment on a record - omitting it is an error."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        Candle(  # type: ignore[call-arg]
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            ts_open=datetime.now(tz=UTC),
            open="1",
            high="1",
            low="1",
            close="1",
            volume="0",
            is_closed=True,
            source="test",
            ingested_at=datetime.now(tz=UTC),
        )


def test_profiles_are_physically_isolated(tmp_path: Path) -> None:
    """DEMO and PAPER must share no state file, data root, audit chain or port."""
    demo = build_profile("DEMO", root=tmp_path)
    paper = build_profile("PAPER", root=tmp_path)

    assert demo.state_db != paper.state_db
    assert demo.parquet_root != paper.parquet_root
    assert demo.audit_log != paper.audit_log
    assert demo.log_dir != paper.log_dir
    assert demo.dashboard_port != paper.dashboard_port


def test_paper_never_places_orders(tmp_path: Path) -> None:
    """PAPER simulates fills locally and must never reach an order endpoint (§6.3)."""
    assert build_profile("PAPER", root=tmp_path).can_place_orders is False


def test_no_environment_carries_real_capital_in_this_phase(tmp_path: Path) -> None:
    """ZERO-LIVE-CAPITAL: every constructible environment must be simulated."""
    for env in ("DEMO", "PAPER"):
        assert build_profile(env, root=tmp_path).real_capital_at_risk is False


def test_profiles_are_frozen(tmp_path: Path) -> None:
    """There is no mutable global 'current env' - a profile cannot be edited in place."""
    profile = build_profile("PAPER", root=tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
        profile.env = "LIVE"  # type: ignore[misc]


def test_unknown_env_string_is_rejected() -> None:
    """Case-sensitive and unforgiving: 'live' must not become LIVE."""
    for bad in ("live", "Live", "demo", "", "PROD", "LIVE "):
        with pytest.raises(ConfigError):
            from okxq.env.profiles import parse_env

            parse_env(bad)


def test_cli_requires_explicit_env() -> None:
    """Starting without --env must fail rather than defaulting to anything."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_cli_refuses_live() -> None:
    """The CLI must refuse LIVE with a non-zero exit, not a traceback or a trade."""
    assert main(["--env", "LIVE"]) == 2


def test_credentials_repr_is_redacted() -> None:
    """A credential must not leak through an f-string or a traceback."""
    from okxq.env.profiles import Credentials

    creds = Credentials(api_key="k-abc123", api_secret="s-def456", passphrase="p-ghi789")
    rendered = f"{creds!r} {creds}"
    for secret in ("k-abc123", "s-def456", "p-ghi789"):
        assert secret not in rendered


def test_decimal_fields_are_exact() -> None:
    """Sanity: a Candle built from strings holds exact decimals."""
    candle = _candle("PAPER")
    assert candle.close == Decimal("105")
