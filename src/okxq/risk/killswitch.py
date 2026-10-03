"""Kill switch (architecture §19.3; M5_DESIGN §8, §11 B-2, §12.3).

The switch's state is DERIVED FROM THE HASH-CHAINED AUDIT LOG. ``engaged()`` returns False
only when every one of these holds; anything else - including any error - reads ENGAGED:

* no ``KILL`` sentinel file (checked with ``os.stat``: only FileNotFoundError means absent);
* the audit chain verifies and its latest kill-switch record is an audited DISARM;
* the cache exists, says disengaged, and pins that disarm as the chain record at its
  ``tip_seq`` with its ``tip_hash``, and the chain still contains that record.

So a lost ``state/`` or ``audit/`` directory, a fresh clone, a rebuilt container, a cache
edited to ``{"engaged": false}``, or a chain truncated back past a later ENGAGE all read as
ENGAGED. Which layer catches what: the CHAIN's latest record catches a stale or
edited cache; the cache FLAG catches a chain cut back past an ENGAGE; the TIP PIN catches a
chain cut back past any record the cache has seen (lost records read as tampering).

Threat model (recorded): accidental loss, corruption and careless edits. A local user who
can rewrite both the chain and the cache consistently can defeat any unsigned scheme;
Layer 1 (no LIVE credential exists) is what bounds that case.

Disarming is manual only: an exact typed phrase at an interactive terminal, refused while the
sentinel exists, audited. Nothing disarms automatically (guard test). An LLM agent with a
pseudo-terminal could still type the phrase - recorded as weak; §1.2 is enforced by keeping
agents away from LIVE operations, not by this check.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from okxq.audit.chain import AuditChain, read_chain, verify_chain
from okxq.contracts import Env
from okxq.errors import SafetyError

ENGAGE = "killswitch_engage"
DISARM = "killswitch_disarm"


class KillSwitchError(SafetyError):
    """The switch could not be written, or a disarm was refused."""


class KillSwitch:
    def __init__(self, env: Env, state_dir: Path, audit_log: Path) -> None:
        self.env = env
        self.cache = state_dir / "killswitch.json"
        self.sentinel = state_dir / "KILL"
        self.audit_log = audit_log

    # -- reading: every failure reads ENGAGED -------------------------------------------

    def _sentinel_present(self) -> bool:
        try:
            os.stat(self.sentinel)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def engaged(self) -> bool:
        try:
            if self._sentinel_present():
                return True
            verify_chain(self.audit_log)
            records = read_chain(self.audit_log)
            switch = [r for r in records if r.kind in (ENGAGE, DISARM)]
            if not switch or switch[-1].kind != DISARM:
                return True
            cache = json.loads(self.cache.read_text(encoding="utf-8"))
            last = switch[-1]
            pinned = next((r for r in records if r.seq == cache["tip_seq"]), None)
            return not (
                cache["engaged"] is False
                and cache["tip_seq"] == last.seq
                and cache["tip_hash"] == last.hash
                and pinned is not None
                and pinned.hash == cache["tip_hash"]
            )
        except Exception:
            return True

    # -- writing ------------------------------------------------------------------------

    def _write_cache(self, engaged: bool, seq: int, digest: str) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache.with_suffix(".tmp")
        payload = {"engaged": engaged, "tip_seq": seq, "tip_hash": digest}
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.cache)
        fd = os.open(self.cache.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def engage(self, trigger: str, detail: dict[str, Any]) -> None:
        """Audited and persisted. Raises KillSwitchError if it cannot be written - the
        caller must then stop (entries are blocked either way: an unwritten switch is an
        unreadable one, which reads ENGAGED)."""
        try:
            rec = AuditChain(self.audit_log).append(
                ENGAGE, {"env": self.env, "trigger": trigger, "detail": detail}
            )
            self._write_cache(True, rec.seq, rec.hash)
        except Exception as exc:
            raise KillSwitchError(f"kill switch engage could not be persisted: {exc}") from exc

    def disarm(self, operator: str, typed: str, reason: str) -> None:
        """Manual only. Never called by any code path except the operator CLI (guard)."""
        if not sys.stdin.isatty():
            raise KillSwitchError("disarm requires an interactive terminal")
        if typed != f"DISARM {self.env}":
            raise KillSwitchError(f"confirmation must be exactly 'DISARM {self.env}'")
        if self._sentinel_present():
            raise KillSwitchError(f"remove the sentinel {self.sentinel} first")
        if not operator.strip() or not reason.strip():
            raise KillSwitchError("operator and reason are required")
        rec = AuditChain(self.audit_log).append(
            DISARM, {"env": self.env, "operator": operator, "reason": reason}
        )
        self._write_cache(False, rec.seq, rec.hash)
