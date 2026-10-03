"""Spread collector (cycle 2, D2): validation never coerces, gaps are recorded, the poll grid
does not drift, files split on the UTC day, and only a successful run is stamped."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import ccxt
import pytest

from okxq.data import spreads
from okxq.data.okx_public import VenueError
from okxq.data.spreads import BookError, JsonlSink, collect, parse_books
from okxq.env.profiles import build_profile

DAY_MS = 86_400_000


def book(
    bids: list[list[str]] | None = None, asks: list[list[str]] | None = None, **kw: Any
) -> dict[str, Any]:
    b: dict[str, Any] = {
        "bids": bids if bids is not None else [["100.0", "2", "0", "1"], ["99.9", "1", "0", "1"]],
        "asks": asks if asks is not None else [["100.1", "3", "0", "1"], ["100.2", "1", "0", "1"]],
        "ts": "1791061046901",
        "seqId": 7,
    }
    b.update(kw)
    return {"code": "0", "msg": "", "data": [b]}


def test_a_valid_book_keeps_exact_strings_and_spread() -> None:
    s = parse_books("BTC-USDT-SWAP", book(), 5, 9)
    assert s.bids[0] == ("100.0", "2") and s.asks[1] == ("100.2", "1")
    assert s.venue_ms == 1791061046901 and s.seq_id == 7 and (s.req_ms, s.recv_ms) == (5, 9)
    assert s.spread_bps == Decimal("0.1") / Decimal("100.05") * 10_000


def test_a_missing_seq_id_is_kept_as_none() -> None:
    r = book()
    del r["data"][0]["seqId"]
    assert parse_books("X", r, 0, 0).seq_id is None


@pytest.mark.parametrize(
    ("response", "msg"),
    [
        ({"code": "51001", "data": []}, "venue code"),
        ("not a dict", "venue code"),
        ({"code": "0", "data": []}, "single book"),
        ({"code": "0", "data": [{}, {}]}, "single book"),
        ({"code": "0", "data": ["x"]}, "single book"),
        (book(bids=[]), "bids: empty"),
        (book(asks="x"), "asks: empty"),  # type: ignore[arg-type]
        (book(bids=[["100"]]), "malformed"),
        (book(bids=["100"]), "malformed"),  # type: ignore[list-item]
        (book(bids=[["abc", "1"]]), "non-numeric"),
        (book(bids=[["100", "0"]]), "non-positive"),
        (book(asks=[["-1", "1"]]), "non-positive"),
        (book(bids=[["99", "1"], ["100", "1"]]), "bids not strictly descending"),
        (book(asks=[["101", "1"], ["101", "1"]]), "asks not strictly ascending"),
        (book(bids=[["100.1", "1"]], asks=[["100.1", "1"]]), "crossed or locked"),
        (book(ts=None), "ts"),
        (book(ts="x"), "ts"),
        (book(seqId="x"), "seqId"),
        (book(seqId=[1]), "seqId"),
    ],
)
def test_an_unusable_book_is_refused_not_coerced(response: Any, msg: str) -> None:
    with pytest.raises(BookError, match=msg):
        parse_books("X", response, 0, 0)


def test_a_book_without_ts_is_refused() -> None:
    r = book()
    del r["data"][0]["ts"]
    with pytest.raises(BookError, match="ts"):
        parse_books("X", r, 0, 0)


def read(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(ln) for p in sorted(root.glob("*.jsonl")) for ln in p.read_text().splitlines()
    ]


def test_the_sink_appends_and_splits_on_the_utc_day(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "s")
    sink.write(spreads.gap_record("A", DAY_MS - 1, "r"))
    sink.write(spreads.gap_record("B", DAY_MS, "r"))
    sink.write(spreads.gap_record("C", DAY_MS + 5, "r"))
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == [
        "1970-01-01.jsonl",
        "1970-01-02.jsonl",
    ]
    assert [r["inst_id"] for r in read(tmp_path / "s")] == ["A", "B", "C"]


class FakeClock:
    """Each request takes ``latency`` ms; sleep advances the clock exactly."""

    def __init__(self, latency: int = 0) -> None:
        self.t = 1_000_000
        self.latency = latency
        self.sleeps: list[float] = []

    def now(self) -> int:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += round(s * 1000)

    def fetch(self, inst: str) -> Any:
        self.t += self.latency
        if inst == "BAD":
            raise VenueError("down")
        if inst == "REJ":
            raise ccxt.BadSymbol("51001")
        if inst == "CROSS":
            return book(bids=[["101", "1"]], asks=[["100", "1"]])
        return book()


def test_collect_polls_on_a_fixed_grid_and_records_gaps(tmp_path: Path) -> None:
    clk = FakeClock(latency=100)
    sink = JsonlSink(tmp_path)
    c = collect(
        ["OK", "BAD", "REJ", "CROSS"],
        clk.fetch,
        sink,
        interval_ms=60_000,
        duration_ms=180_000,
        clock_ms=clk.now,
        sleep_s=clk.sleep,
    )
    assert (c.polls, c.samples, c.gaps) == (3, 3, 9)
    assert not c.ok and c.failing == ["BAD", "CROSS", "REJ"]
    assert c.per_inst["OK"] == [3, 0] and c.per_inst["BAD"] == [0, 3]
    rows = read(tmp_path)
    oks = [r for r in rows if "gap" not in r]
    # Grid, not drift: every poll starts exactly on start + k * 60 s despite 400 ms per poll.
    assert [r["req_ms"] for r in oks] == [1_000_000, 1_060_000, 1_120_000]
    assert [r["recv_ms"] - r["req_ms"] for r in oks] == [100, 100, 100]
    assert clk.sleeps == [59.6, 59.6]
    gaps = {r["gap"].split(":")[0] for r in rows if "gap" in r}
    assert gaps == {"VenueError", "BadSymbol", "BookError"}


def test_an_overrunning_poll_skips_missed_ticks_instead_of_bursting(tmp_path: Path) -> None:
    clk = FakeClock(latency=150_000)  # one request outlasts two intervals
    c = collect(
        ["OK"],
        clk.fetch,
        JsonlSink(tmp_path),
        interval_ms=60_000,
        duration_ms=300_000,
        clock_ms=clk.now,
        sleep_s=clk.sleep,
    )
    starts = [r["req_ms"] - 1_000_000 for r in read(tmp_path)]
    # Polls at 0 (ends 150 s) -> next tick 180 s; sleep 30 s; ends 330 s -> past the end.
    assert starts == [0, 180_000]
    assert c.polls == 2 and c.ok and clk.sleeps == [30.0]


def test_an_unknown_exception_is_not_swallowed(tmp_path: Path) -> None:
    def boom(inst: str) -> Any:
        raise KeyError("a bug, not a venue failure")

    with pytest.raises(KeyError):
        collect(
            ["A"],
            boom,
            JsonlSink(tmp_path),
            interval_ms=60_000,
            duration_ms=1,
            clock_ms=lambda: 0,
            sleep_s=lambda s: None,
        )


class FakeApi:
    responses: ClassVar[dict[str, Any]] = {}

    def fetch_books_raw(self, inst_id: str, depth: int) -> Any:
        assert depth == spreads.DEPTH
        r = self.responses.get(inst_id, book())
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(spreads, "build_profile", lambda e: build_profile(e, root=tmp_path))
    monkeypatch.setattr(spreads, "OkxPublic", FakeApi)
    return tmp_path


def test_main_writes_the_stamp_only_on_success(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeApi.responses = {}
    assert spreads.main(["--env", "PAPER", "--interval-s", "5", "--duration-s", "1"]) == 0
    assert (env / "state/paper/collect_spreads.last_ok").exists()
    rows = read(env / "data/paper/spreads")
    members = json.loads(spreads.UNIVERSE_FILE.read_text())["members"]
    assert [r["inst_id"] for r in rows] == members and all("gap" not in r for r in rows)
    assert "samples=8 gaps=0" in capsys.readouterr().out


def test_main_fails_without_a_stamp_when_mostly_gaps(env: Path) -> None:
    FakeApi.responses = {
        m: VenueError("down") for m in json.loads(spreads.UNIVERSE_FILE.read_text())["members"]
    }
    assert spreads.main(["--env", "DEMO", "--interval-s", "5", "--duration-s", "1"]) == 1
    assert not (env / "state/demo/collect_spreads.last_ok").exists()


@pytest.mark.parametrize("argv", [["--env", "LIVE"], ["--interval-s", "1"], ["--duration-s", "0"]])
def test_main_refuses_live_and_silly_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        spreads.main(argv)
    assert e.value.code == 2


def test_one_dead_instrument_fails_the_run_however_healthy_the_rest(env: Path) -> None:
    """Advisor should-fix: a global gaps <= samples let one instrument fail on every poll
    while seven healthy ones stamped success."""
    FakeApi.responses = {"SAND-USDT-SWAP": VenueError("down")}
    assert spreads.main(["--env", "PAPER", "--interval-s", "5", "--duration-s", "1"]) == 1
    assert not (env / "state/paper/collect_spreads.last_ok").exists()


def test_a_run_with_no_instruments_is_not_ok() -> None:
    assert not spreads.Counts().ok
