"""Portfolio persistence: SQLite, one transaction per event, mirrored to the audit chain
(architecture §14). State is never stored - it is REPLAYED from the event log through the
pure reducer, so a restart reproduces it bit-for-bit, and a missing database means no state,
which the engine rejects (no high-water mark, no day-open equity: never reset)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

from okxq.contracts import Env
from okxq.risk.cycle import Audit
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


class PortfolioStore:
    def __init__(self, env: Env, db: Path, audit: Audit) -> None:
        self.env = env
        self.db = db
        self.audit = audit
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS portfolio_events "
                "(seq INTEGER PRIMARY KEY, body TEXT NOT NULL)"
            )

    def _fold(self, rows: list[tuple[int, str]]) -> PortfolioState | None:
        state: PortfolioState | None = None
        for _, body in rows:
            event = decode(json.loads(body))
            if state is None:
                if not isinstance(event, Init):
                    raise PortfolioError("event log does not start with Init")
                state = initial(self.env, event)
            else:
                state = apply(state, event)
        return state

    def load(self) -> PortfolioState | None:
        with sqlite3.connect(self.db) as con:
            rows = con.execute("SELECT seq, body FROM portfolio_events ORDER BY seq").fetchall()
        return self._fold(rows)

    def append(self, event: Event) -> PortfolioState:
        """Validate against the replayed state, then write the event and its audit record in
        ONE transaction: if the audit append fails, the event is rolled back."""
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
            self.audit.append("portfolio_event", json.loads(body))
            con.commit()
            return new
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
