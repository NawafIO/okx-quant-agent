"""Kill switch: engaged unless an audited disarm is provably the latest word (§19.3, B-2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from okxq.risk.killswitch import KillSwitch, KillSwitchError


@pytest.fixture
def ks(tmp_path: Path) -> KillSwitch:
    return KillSwitch("PAPER", tmp_path / "state", tmp_path / "audit" / "audit.jsonl")


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def armed_then_disarmed(ks: KillSwitch) -> None:
    ks.engage("test", {})
    ks.disarm("operator", "DISARM PAPER", "test")


def test_a_fresh_clone_with_no_record_reads_engaged(ks: KillSwitch) -> None:
    assert ks.engaged() is True


def test_engage_then_audited_disarm(ks: KillSwitch, tty: None) -> None:
    ks.engage("RC-10 daily loss", {"loss": "0.02"})
    assert ks.engaged() is True
    ks.disarm("operator", "DISARM PAPER", "reviewed the loss")
    assert ks.engaged() is False
    ks.engage("again", {})
    assert ks.engaged() is True


RESTART = """
import sys
from pathlib import Path
from okxq.risk.killswitch import KillSwitch
ks = KillSwitch("PAPER", Path(sys.argv[1]), Path(sys.argv[2]))
print(ks.engaged())
"""


def test_engagement_survives_a_process_restart(ks: KillSwitch, tty: None) -> None:
    def fresh_process() -> str:
        out = subprocess.run(  # noqa: S603 - fixed literal argv: a fresh-process restart test
            [sys.executable, "-c", RESTART, str(ks.cache.parent), str(ks.audit_log)],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()

    ks.engage("crash test", {})
    assert fresh_process() == "True"
    ks.disarm("operator", "DISARM PAPER", "test")
    assert fresh_process() == "False"


def test_a_cache_edited_to_disengaged_without_a_disarm_reads_engaged(ks: KillSwitch) -> None:
    ks.engage("t", {})
    ks.cache.write_text(json.dumps({"engaged": False, "tip_seq": 1, "tip_hash": "x"}))
    assert ks.engaged() is True


def test_truncating_the_chain_back_past_an_engage_reads_engaged(ks: KillSwitch, tty: None) -> None:
    """Defended by the cache FLAG: it was written at the ENGAGE and still says engaged."""
    armed_then_disarmed(ks)
    keep = ks.audit_log.read_text().splitlines(keepends=True)
    ks.engage("later", {})
    ks.audit_log.write_text("".join(keep))  # cut the ENGAGE off: the chain still verifies
    assert ks.engaged() is True


def test_a_chain_cut_back_past_records_the_cache_knows_reads_engaged(
    ks: KillSwitch, tty: None
) -> None:
    """Defended by the TIP PIN (mutation-checked): disarm, engage, disarm; then the chain is
    cut back to the FIRST disarm. The cache honestly says disengaged but pins the second
    disarm's seq, which no longer exists - the chain lost records, so read ENGAGED."""
    armed_then_disarmed(ks)
    keep = ks.audit_log.read_text().splitlines(keepends=True)
    ks.engage("later", {})
    ks.disarm("operator", "DISARM PAPER", "again")
    assert ks.engaged() is False
    ks.audit_log.write_text("".join(keep))  # ends in the first disarm and still verifies
    assert ks.engaged() is True


@pytest.mark.parametrize("damage", ["delete_cache", "garbage_cache", "wrong_hash", "chain"])
def test_any_damage_after_a_disarm_reads_engaged(ks: KillSwitch, tty: None, damage: str) -> None:
    armed_then_disarmed(ks)
    assert ks.engaged() is False
    if damage == "delete_cache":
        ks.cache.unlink()
    elif damage == "garbage_cache":
        ks.cache.write_text("{not json")
    elif damage == "wrong_hash":
        c = json.loads(ks.cache.read_text())
        ks.cache.write_text(json.dumps(c | {"tip_hash": "0" * 64}))
    else:
        lines = ks.audit_log.read_text().splitlines()
        rec = json.loads(lines[0]) | {"kind": "tampered"}
        ks.audit_log.write_text("\n".join([json.dumps(rec), *lines[1:]]) + "\n")
    assert ks.engaged() is True


def test_the_sentinel_engages_and_an_unreadable_sentinel_engages(
    ks: KillSwitch, tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed_then_disarmed(ks)
    ks.sentinel.touch()
    assert ks.engaged() is True
    ks.sentinel.unlink()
    assert ks.engaged() is False

    def denied(path: object) -> os.stat_result:
        raise PermissionError(path)

    monkeypatch.setattr(os, "stat", denied)
    assert ks.engaged() is True


@pytest.mark.parametrize(
    ("operator", "typed", "reason", "match"),
    [
        ("op", "DISARM DEMO", "r", "exactly"),
        ("op", "disarm PAPER", "r", "exactly"),
        (" ", "DISARM PAPER", "r", "required"),
        ("op", "DISARM PAPER", "", "required"),
    ],
)
def test_disarm_refusals(
    ks: KillSwitch, tty: None, operator: str, typed: str, reason: str, match: str
) -> None:
    ks.engage("t", {})
    with pytest.raises(KillSwitchError, match=match):
        ks.disarm(operator, typed, reason)
    assert ks.engaged() is True


def test_disarm_refused_without_a_terminal_or_with_the_sentinel(
    ks: KillSwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    ks.engage("t", {})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(KillSwitchError, match="interactive"):
        ks.disarm("op", "DISARM PAPER", "r")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    ks.sentinel.parent.mkdir(parents=True, exist_ok=True)
    ks.sentinel.touch()
    with pytest.raises(KillSwitchError, match="sentinel"):
        ks.disarm("op", "DISARM PAPER", "r")


def test_an_engage_that_cannot_be_persisted_raises(tmp_path: Path) -> None:
    blocked = tmp_path / "audit"
    blocked.mkdir()
    ks = KillSwitch("PAPER", tmp_path / "state", blocked)  # the audit log path is a directory
    with pytest.raises(KillSwitchError, match="could not be persisted"):
        ks.engage("t", {})
    assert ks.engaged() is True


def test_latched_is_true_only_for_a_durable_engage(ks: KillSwitch, tty: None) -> None:
    assert ks.latched() is False  # nothing recorded
    ks.engage("t", {})
    assert ks.latched() is True
    ks.disarm("op", "DISARM PAPER", "r")
    assert ks.latched() is False
    ks.audit_log.write_text("garbage\n")
    assert ks.latched() is False  # on any doubt: not latched, so a halt WRITES its engage
    assert ks.engaged() is True


def test_no_directory_fsync_on_windows(ks: KillSwitch, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing audit #7: os.open on a directory raises on Windows; the switch must still
    write. (Linux CI cannot prove Windows behaviour - the owner runs verify.ps1 there.)"""
    from okxq.risk import killswitch

    def no_dir_open(*_: object) -> int:
        raise PermissionError("directory open (Windows)")

    monkeypatch.setattr(killswitch, "FSYNC_DIRS", False)
    monkeypatch.setattr(os, "open", no_dir_open)
    ks.engage("t", {})
    assert ks.latched() is True and json.loads(ks.cache.read_text())["engaged"] is True
