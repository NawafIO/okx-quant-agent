"""Environment profile construction, credential loading, and the M0 boot path."""

from __future__ import annotations

from pathlib import Path

import pytest

from okxq.audit.chain import read_chain, verify_chain
from okxq.contracts import ENVS, Env
from okxq.env import profiles
from okxq.env.profiles import build_profile, ensure_dirs, parse_env
from okxq.obs.secrets import redact, registered_count
from okxq.run import boot, main

# --- credential loading -----------------------------------------------------------------


def test_credentials_loaded_and_registered_as_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A loaded credential must be registered for redaction immediately."""
    monkeypatch.setenv("OKXQ_DEMO_API_KEY", "key-aaaa1111")
    monkeypatch.setenv("OKXQ_DEMO_API_SECRET", "secret-bbbb2222")
    monkeypatch.setenv("OKXQ_DEMO_API_PASSPHRASE", "pass-cccc3333")

    profile = build_profile("DEMO", root=tmp_path)

    assert profile.credentials is not None
    assert profile.credentials.api_key == "key-aaaa1111"
    assert registered_count() == 3
    # Registration is what makes §20.3 redaction work downstream.
    assert "key-aaaa1111" not in redact("connecting with key-aaaa1111")


@pytest.mark.parametrize(
    "present",
    [
        (),
        ("OKXQ_DEMO_API_KEY",),
        ("OKXQ_DEMO_API_KEY", "OKXQ_DEMO_API_SECRET"),
    ],
)
def test_partial_credentials_yield_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, present: tuple[str, ...]
) -> None:
    """A partial credential set is None, not a half-built Credentials object.

    Absence is the normal, expected state - for PAPER always, and for LIVE throughout
    phases 1-3. Callers that genuinely need to place an order fail at that point.
    """
    for var in ("OKXQ_DEMO_API_KEY", "OKXQ_DEMO_API_SECRET", "OKXQ_DEMO_API_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)
    for var in present:
        monkeypatch.setenv(var, "value-12345678")

    assert build_profile("DEMO", root=tmp_path).credentials is None


# --- profile shape ----------------------------------------------------------------------


@pytest.mark.parametrize("env", ["DEMO", "PAPER"])
def test_ensure_dirs_creates_the_tree(env: Env, tmp_path: Path) -> None:
    profile = build_profile(env, root=tmp_path)
    ensure_dirs(profile)

    assert profile.state_db.parent.is_dir()
    assert profile.audit_log.parent.is_dir()
    assert profile.parquet_root.is_dir()
    assert profile.log_dir.is_dir()


@pytest.mark.parametrize("env", ["DEMO", "PAPER"])
def test_banner_warns_no_real_capital(env: Env, tmp_path: Path) -> None:
    """The operator banner must say plainly that no real capital is involved (§19.1)."""
    banner = build_profile(env, root=tmp_path).banner()
    assert "NO REAL CAPITAL" in banner
    assert env in banner


def test_parse_env_accepts_all_three() -> None:
    for env in ENVS:
        assert parse_env(env) == env


def test_paths_are_namespaced_per_environment(tmp_path: Path) -> None:
    demo = build_profile("DEMO", root=tmp_path)
    assert "demo" in demo.state_db.as_posix()
    assert "demo" in demo.parquet_root.as_posix()


# --- boot -------------------------------------------------------------------------------


def test_boot_writes_and_verifies_audit_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Boot verifies the existing chain, then records its own boot event."""
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)

    profile = boot("PAPER")

    assert profile.env == "PAPER"
    assert verify_chain(profile.audit_log) == 1

    records = read_chain(profile.audit_log)
    assert records[0].kind == "boot"
    assert records[0].payload["env"] == "PAPER"
    assert records[0].payload["real_capital_at_risk"] is False
    assert records[0].payload["prior_records_verified"] == 0


def test_second_boot_extends_the_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A restart continues the chain and reports the records it verified."""
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)

    boot("PAPER")
    profile = boot("PAPER")

    assert verify_chain(profile.audit_log) == 2
    assert read_chain(profile.audit_log)[1].payload["prior_records_verified"] == 1


def test_main_succeeds_for_simulated_environments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)

    assert main(["--env", "PAPER"]) == 0
    assert main(["--env", "DEMO"]) == 0


def test_boot_refuses_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Boot must refuse LIVE without writing anything to a LIVE ledger."""
    monkeypatch.setattr(profiles, "PROJECT_ROOT", tmp_path)

    assert main(["--env", "LIVE"]) == 2
    assert not (tmp_path / "audit" / "live").exists()
