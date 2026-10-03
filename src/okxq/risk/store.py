"""Portfolio persistence: SQLite, one transaction per event, mirrored to the audit chain
(architecture §14). State is never stored - it is REPLAYED from the event log through the
pure reducer, so a restart reproduces it bit-for-bit.

The database and the audit chain must AGREE, event for event (closing audit #2, #14). A lost
database next to a chain that holds portfolio events, a commit that failed after its audit
record was written, or any edit, makes ``load`` and ``append`` refuse with PortfolioError -
so the engine has no state and REJECTS. The high-water mark, day-open equity and loss streak
are therefore never silently reset by starting a fresh ``Init``."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

from okxq.audit.chain import AuditChain, read_chain, verify_chain
from okxq.contracts import Env
from okxq.risk.portfolio import (
    Closed,
    Event,
    FundingAccrued,
    Init,
    MarkUpdate,
    Opened,
    PortfolioError,
    PortfolioState,
    StopMoved,
    apply,
    initial,
)

TYPES: dict[str, type[Any]] = {
    t.__name__: t for t in (Init, Opened, Closed, MarkUpdate, StopMoved, FundingAccrued)
}


def encode(event: Event) -> dict[str, Any]:
    out: dict[str, Any] = {"type": type(event).__name__}
    for f in fields(event):
        v = getattr(event, f.name)
        out[f.name] = str(v) if isinstance(v, Decimal) else v
    return out


def decode(raw: dict[str, Any]) -> Event:
    cls = TYPES[raw["type"]]
    kw = {}
    for f in fields(cls):
        v = raw[f.name]
        kw[f.name] = Decimal(v) if f.type in ("Decimal",) else v
    event: Event = cls(**kw)
    return event


KIND = "portfolio_event"


class PortfolioStore:
    def __init__(self, env: Env, db: Path, audit_log: Path) -> None:
        self.env = env
        self.db = db
        self.audit_log = audit_log
        db.parent.mkdir(parents=True, exist_ok=True)
        # closing(): sqlite3's own context manager commits but never CLOSES, which leaves
        # the database locked on Windows until garbage collection (found on the first real
        # Windows run). Every connection here is closed explicitly.
        with closing(sqlite3.connect(db)) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS portfolio_events "
                "(seq INTEGER PRIMARY KEY, body TEXT NOT NULL)"
            )

    def _audited(self) -> list[dict[str, Any]]:
        if not self.audit_log.exists():
            return []
        verify_chain(self.audit_log)
        return [r.payload for r in read_chain(self.audit_log) if r.kind == KIND]

    def _fold(self, rows: list[tuple[int, str]]) -> PortfolioState | None:
        bodies = [json.loads(body) for _, body in rows]
        if bodies != self._audited():
            raise PortfolioError(
                "portfolio database and audit chain disagree - refusing to rebuild state "
                "(a lost or edited database would otherwise reset the HWM and day basis)"
            )
        state: PortfolioState | None = None
        for raw in bodies:
            event = decode(raw)
            if state is None:
                if not isinstance(event, Init):
                    raise PortfolioError("event log does not start with Init")
                state = initial(self.env, event)
            else:
                state = apply(state, event)
        return state

    def load(self) -> PortfolioState | None:
        with closing(sqlite3.connect(self.db)) as con:
            rows = con.execute("SELECT seq, body FROM portfolio_events ORDER BY seq").fetchall()
        return self._fold(rows)

    def append(self, event: Event) -> PortfolioState:
        """Reconcile, validate against the replayed state, then write the event and its
        audit record in ONE transaction: if the audit append fails, the event rolls back."""
        con = sqlite3.connect(self.db)
        try:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute("SELECT seq, body FROM portfolio_events ORDER BY seq").fetchall()
            state = self._fold(rows)
            if state is None:
                if not isinstance(event, Init):
                    raise PortfolioError("the first event must be Init")
                new = initial(self.env, event)
            else:
                new = apply(state, event)
            body = json.dumps(encode(event), sort_keys=True)
            con.execute("INSERT INTO portfolio_events (body) VALUES (?)", (body,))
            AuditChain(self.audit_log).append(KIND, json.loads(body))
            con.commit()
            return new
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
