"""Append-only, hash-chained audit trail (architecture §20.2).

Each record stores the SHA-256 of the previous record, so any retroactive edit, deletion or
reordering breaks the chain and is detectable by :func:`verify_chain`. One chain per
environment; the chain is verified on boot.

This is tamper *evidence*, not tamper *prevention* - an attacker with write access can
rewrite the whole chain. It defends against the realistic threat: a well-meaning edit to
make history look tidier, and silent corruption.

Reconstruction requirement: for any trade, this trail alone must answer - what data, what
indicator values, which strategy version, which checks with what numbers, who approved it,
what was sent, what came back, what it cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from okxq.errors import AuditChainError
from okxq.obs.secrets import redact

#: Hash value standing in for "no previous record" at the head of a chain.
GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    """Serialise deterministically so an identical record always hashes identically."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(seq: int, ts: str, kind: str, payload: dict[str, Any], prev_hash: str) -> str:
    """Compute a record's chain hash over all of its content plus the previous hash."""
    material = _canonical(
        {"seq": seq, "ts": ts, "kind": kind, "payload": payload, "prev_hash": prev_hash}
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """One link in the chain."""

    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "kind": self.kind,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "hash": self.hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def recompute(self) -> str:
        return compute_hash(self.seq, self.ts, self.kind, self.payload, self.prev_hash)


class AuditChain:
    """Append-only writer over a JSONL file.

    Appends are ``O(1)``; on construction the tail is read once to recover the sequence
    number and previous hash, so a restart continues the existing chain rather than
    starting a new one.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq, self._prev_hash = self._recover_tail()

    def _recover_tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS_HASH
        last: str | None = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS_HASH
        rec = json.loads(last)
        return int(rec["seq"]), str(rec["hash"])

    def append(self, kind: str, payload: dict[str, Any]) -> AuditRecord:
        """Append a record and return it.

        The payload is redacted before hashing, so a secret is never committed to the
        chain - and because the hash covers the redacted form, redaction cannot later be
        mistaken for tampering.
        """
        with self._lock:
            seq = self._seq + 1
            ts = datetime.now(tz=UTC).isoformat()
            safe_payload: dict[str, Any] = json.loads(redact(_canonical(payload)))
            digest = compute_hash(seq, ts, kind, safe_payload, self._prev_hash)
            record = AuditRecord(
                seq=seq,
                ts=ts,
                kind=kind,
                payload=safe_payload,
                prev_hash=self._prev_hash,
                hash=digest,
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(record.to_json() + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._seq = seq
            self._prev_hash = digest
            return record


def read_chain(path: Path) -> list[AuditRecord]:
    """Read every record from a chain file."""
    if not path.exists():
        return []
    records: list[AuditRecord] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw = json.loads(line)
            records.append(
                AuditRecord(
                    seq=int(raw["seq"]),
                    ts=str(raw["ts"]),
                    kind=str(raw["kind"]),
                    payload=dict(raw["payload"]),
                    prev_hash=str(raw["prev_hash"]),
                    hash=str(raw["hash"]),
                )
            )
    return records


def verify_chain(path: Path) -> int:
    """Verify an entire chain and return the number of records verified.

    Checks, in order: sequence numbers are consecutive from 1; each ``prev_hash`` matches
    the preceding record's hash; and each stored hash matches a recomputation over the
    record's own content. The third check is what catches an edited payload.

    Raises:
        AuditChainError: on the first inconsistency found, naming the sequence number.
    """
    records = read_chain(path)
    expected_prev = GENESIS_HASH
    for index, rec in enumerate(records, start=1):
        if rec.seq != index:
            raise AuditChainError(
                f"audit chain break at position {index}: seq={rec.seq}, expected {index} "
                "(record inserted, deleted or reordered)"
            )
        if rec.prev_hash != expected_prev:
            raise AuditChainError(
                f"audit chain break at seq={rec.seq}: prev_hash does not match the "
                "preceding record's hash"
            )
        recomputed = rec.recompute()
        if recomputed != rec.hash:
            raise AuditChainError(
                f"audit chain break at seq={rec.seq}: content hash mismatch "
                "(record was modified after being written)"
            )
        expected_prev = rec.hash
    return len(records)
