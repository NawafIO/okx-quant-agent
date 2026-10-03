"""Order-book spread collector (cycle 2, decision D2; docs/CYCLE2_DESIGN.md §3).

    python -m okxq.data.spreads --env PAPER                  # one 59-minute run, 60 s polls
    python -m okxq.data.spreads --env PAPER --duration-s 120  # a short manual check

slip-v2's spread term is the Abdi-Ranaldo estimate from 1h bars, which on BTC is ~500x the
live spread (M2 finding M-2). This records what the book actually quoted so the spread term
can be calibrated against measurement instead of an estimator. It records; it calibrates
nothing - the calibration rule is pre-registered separately and runs on the stored files.

**Public endpoint only. No credential is read or transmitted.** LIVE is not accepted.

Each poll appends one JSON line per instrument to ``data/<env>/spreads/YYYY-MM-DD.jsonl``
(UTC day of the request time; each line carries the local request and receive times
and the venue's book timestamp): the top ``DEPTH`` levels as the venue's exact strings, so
depth can later inform the impact term too. A book that fails validation or a failed request
is written as a GAP line with the reason - never dropped, never repaired - so missing samples
stay visible. A run that collected nothing, or more gaps than samples, exits 1 and writes no
success stamp; ``state/<env>/collect_spreads.last_ok`` is written only on success.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

import ccxt

from okxq.data.okx_public import OkxPublic, VenueError
from okxq.env.profiles import PROJECT_ROOT, build_profile, ensure_dirs

#: Book levels kept per side. Five is what the M2 snapshot used; more costs nothing per call.
DEPTH: Final[int] = 5
SCHEMA: Final[str] = "spread-snap-v1"
UNIVERSE_FILE: Final[Path] = PROJECT_ROOT / "docs" / "universe_m4.json"


class BookError(ValueError):
    """A books response that is not a usable two-sided book."""


@dataclass(frozen=True)
class BookSnapshot:
    inst_id: str
    req_ms: int
    recv_ms: int
    venue_ms: int
    seq_id: int | None
    bids: tuple[tuple[str, str], ...]
    asks: tuple[tuple[str, str], ...]

    @property
    def spread_bps(self) -> Decimal:
        """Full quoted spread over the mid, in basis points (exact)."""
        bid, ask = Decimal(self.bids[0][0]), Decimal(self.asks[0][0])
        return (ask - bid) / ((ask + bid) / 2) * 10_000


def _levels(side: Any, name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(side, list) or not side:
        raise BookError(f"{name}: empty or not a list")
    out = []
    for lvl in side:
        if not isinstance(lvl, list) or len(lvl) < 2:
            raise BookError(f"{name}: malformed level {lvl!r}")
        px, sz = str(lvl[0]), str(lvl[1])
        try:
            if Decimal(px) <= 0 or Decimal(sz) <= 0:
                raise BookError(f"{name}: non-positive level {lvl!r}")
        except InvalidOperation as e:
            raise BookError(f"{name}: non-numeric level {lvl!r}") from e
        out.append((px, sz))
    return tuple(out)


def parse_books(inst_id: str, response: Any, req_ms: int, recv_ms: int) -> BookSnapshot:
    """Validate one ``/market/books`` response sent at ``req_ms`` and received at ``recv_ms``
    (local clock). Raises :class:`BookError`; never coerces."""
    if not isinstance(response, dict) or str(response.get("code")) != "0":
        raise BookError(
            f"venue code {response.get('code') if isinstance(response, dict) else response!r}"
        )
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise BookError("data is not a single book")
    book = data[0]
    bids, asks = _levels(book.get("bids"), "bids"), _levels(book.get("asks"), "asks")
    bp, ap = [Decimal(p) for p, _ in bids], [Decimal(p) for p, _ in asks]
    if any(a <= b for a, b in itertools.pairwise(bp)):
        raise BookError("bids not strictly descending")
    if any(b <= a for a, b in itertools.pairwise(ap)):
        raise BookError("asks not strictly ascending")
    if bp[0] >= ap[0]:
        raise BookError(f"crossed or locked book {bp[0]} >= {ap[0]}")
    try:
        venue_ms = int(book["ts"])
    except (KeyError, TypeError, ValueError) as e:
        raise BookError("missing or non-integer ts") from e
    seq = book.get("seqId")
    return BookSnapshot(
        inst_id, req_ms, recv_ms, venue_ms, int(seq) if seq is not None else None, bids, asks
    )


def snapshot_record(s: BookSnapshot) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "inst_id": s.inst_id,
        "req_ms": s.req_ms,
        "recv_ms": s.recv_ms,
        "venue_ms": s.venue_ms,
        "seq_id": s.seq_id,
        "spread_bps": str(s.spread_bps),
        "bids": [list(lv) for lv in s.bids],
        "asks": [list(lv) for lv in s.asks],
    }


def gap_record(inst_id: str, req_ms: int, reason: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "inst_id": inst_id, "req_ms": req_ms, "gap": reason}


class JsonlSink:
    """Append-only, one file per UTC day. Opened per write: a crash loses at most the line
    being written, and no handle is held open across polls (Windows locks open files)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, req_ms: int) -> Path:
        day = datetime.fromtimestamp(req_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        return self.root / f"{day}.jsonl"

    def write(self, rec: dict[str, Any]) -> None:
        path = self.path_for(int(rec["req_ms"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")


@dataclass
class Counts:
    polls: int = 0
    samples: int = 0
    gaps: int = 0

    @property
    def ok(self) -> bool:
        return self.samples > 0 and self.gaps <= self.samples


def collect(
    instruments: Sequence[str],
    fetch: Callable[[str], Any],
    sink: JsonlSink,
    *,
    interval_ms: int,
    duration_ms: int,
    clock_ms: Callable[[], int],
    sleep_s: Callable[[float], None],
) -> Counts:
    """Poll every instrument on a fixed grid ``start + k * interval`` until ``duration``.

    The grid does not drift with request latency. A poll that overruns its interval skips
    the ticks already in the past rather than firing a burst to catch up (the missed ticks
    are simply absent - the gap is in the timestamps, not hidden)."""
    start = clock_ms()
    end = start + duration_ms
    counts = Counts()
    k = 0
    while (tick := start + k * interval_ms) < end:
        now = clock_ms()
        if now < tick:
            sleep_s((tick - now) / 1000)
        counts.polls += 1
        for inst in instruments:
            req = clock_ms()
            try:
                response = fetch(inst)
                snap = parse_books(inst, response, req, clock_ms())
            except (BookError, VenueError, ccxt.BaseError) as e:
                sink.write(gap_record(inst, req, f"{type(e).__name__}: {e}"[:300]))
                counts.gaps += 1
            else:
                sink.write(snapshot_record(snap))
                counts.samples += 1
        k = max(k + 1, -(-(clock_ms() - start) // interval_ms))
    return counts


def _write_stamp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(datetime.now(tz=UTC).isoformat(), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m okxq.data.spreads")
    ap.add_argument("--env", choices=["PAPER", "DEMO"], default="PAPER")
    ap.add_argument("--interval-s", type=int, default=60)
    ap.add_argument("--duration-s", type=int, default=3540)
    a = ap.parse_args(argv)
    if a.interval_s < 5 or a.duration_s < 1:
        ap.error("--interval-s must be >= 5 and --duration-s >= 1")
    members: list[str] = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))["members"]
    profile = build_profile(a.env)
    ensure_dirs(profile)
    sink = JsonlSink(profile.parquet_root.parent / "spreads")
    api = OkxPublic()
    counts = collect(
        members,
        lambda inst: api.fetch_books_raw(inst, DEPTH),
        sink,
        interval_ms=a.interval_s * 1000,
        duration_ms=a.duration_s * 1000,
        clock_ms=lambda: time.time_ns() // 1_000_000,
        sleep_s=time.sleep,
    )
    print(
        f"spreads instruments={len(members)} polls={counts.polls} samples={counts.samples} "
        f"gaps={counts.gaps} dir={sink.root}"
    )
    if not counts.ok:
        print("FAIL: no samples, or more gaps than samples", file=sys.stderr)
        return 1
    _write_stamp(profile.state_db.parent / "collect_spreads.last_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
