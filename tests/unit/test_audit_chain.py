"""Audit chain: append, verify, and detect tampering (architecture §20.2)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from okxq.audit.chain import GENESIS_HASH, AuditChain, read_chain, verify_chain
from okxq.errors import AuditChainError, SafetyError


@pytest.fixture
def chain_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


def _seed(path: Path, n: int = 3) -> AuditChain:
    chain = AuditChain(path)
    for i in range(n):
        chain.append("event", {"i": i, "note": f"record {i}"})
    return chain


def test_three_record_chain_verifies(chain_path: Path) -> None:
    """M0 acceptance: a 3-record chain verifies."""
    _seed(chain_path, 3)
    assert verify_chain(chain_path) == 3


def test_chain_is_linked(chain_path: Path) -> None:
    """Each record's prev_hash must be the previous record's hash."""
    _seed(chain_path, 3)
    records = read_chain(chain_path)

    assert records[0].prev_hash == GENESIS_HASH
    assert [r.seq for r in records] == [1, 2, 3]
    for earlier, later in itertools.pairwise(records):
        assert later.prev_hash == earlier.hash


def test_empty_and_missing_chains_verify(tmp_path: Path) -> None:
    assert verify_chain(tmp_path / "absent.jsonl") == 0
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert verify_chain(tmp_path / "empty.jsonl") == 0


def test_tampered_payload_is_detected(chain_path: Path) -> None:
    """M0 acceptance: a deliberately modified record must break verification."""
    _seed(chain_path, 3)
    lines = chain_path.read_text(encoding="utf-8").splitlines()

    record = json.loads(lines[1])
    record["payload"]["note"] = "quietly edited"
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AuditChainError, match="content hash mismatch"):
        verify_chain(chain_path)


def test_deleted_record_is_detected(chain_path: Path) -> None:
    """Removing a record must break the chain, not silently shorten history."""
    _seed(chain_path, 3)
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    chain_path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    with pytest.raises(AuditChainError):
        verify_chain(chain_path)


def test_reordered_records_are_detected(chain_path: Path) -> None:
    _seed(chain_path, 3)
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    chain_path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")

    with pytest.raises(AuditChainError):
        verify_chain(chain_path)


def test_rehashed_tamper_still_breaks_the_link(chain_path: Path) -> None:
    """An attacker who edits a payload *and* fixes its own hash still breaks the chain.

    This is the property that makes the chain worth having: repairing one record requires
    rewriting every subsequent record, because each one commits to its predecessor.
    """
    _seed(chain_path, 3)
    lines = chain_path.read_text(encoding="utf-8").splitlines()

    record = json.loads(lines[1])
    record["payload"]["note"] = "edited"
    from okxq.audit.chain import compute_hash

    record["hash"] = compute_hash(
        record["seq"], record["ts"], record["kind"], record["payload"], record["prev_hash"]
    )
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AuditChainError, match="prev_hash"):
        verify_chain(chain_path)


def test_chain_resumes_across_restart(chain_path: Path) -> None:
    """A restart must continue the existing chain, not start a second one."""
    _seed(chain_path, 2)
    reopened = AuditChain(chain_path)
    reopened.append("event", {"i": 99})

    records = read_chain(chain_path)
    assert [r.seq for r in records] == [1, 2, 3]
    assert verify_chain(chain_path) == 3


def test_audit_chain_error_is_a_safety_error() -> None:
    assert issubclass(AuditChainError, SafetyError)
