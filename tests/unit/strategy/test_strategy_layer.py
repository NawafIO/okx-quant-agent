"""M4 strategy layer: contract, constants rule, purity, bounded history, registry."""

from __future__ import annotations

import ast
import builtins
import inspect
import random
import socket
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from okxq.backtest.engine import PositionSnapshot, StrategyContext
from okxq.backtest.history import HistoryBuffer, HistoryView
from okxq.backtest.types import Action, Side
from okxq.contracts import Signal
from okxq.strategy.base import (
    HOUR_MS,
    BoundedView,
    MarketContext,
    Registry,
    StrategySpec,
    code_version,
)
from okxq.strategy.candidates import keltner_reversion, trend_breakout, vol_compression_breakout
from okxq.strategy.registry import REGISTRY

MODULES = (trend_breakout, keltner_reversion, vol_compression_breakout)
SPECS = [m.SPEC for m in MODULES]
T0 = 1_577_836_800_000  # 2020-01-01T00:00Z


def synthetic(seed: int, n: int) -> HistoryBuffer:
    """Hourly OHLCV with alternating quiet and trending stretches, so every candidate fires."""
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 700) % 2 == 0, 0.002, 0.012)
    drift = np.where((np.arange(n) // 1400) % 2 == 0, 0.0004, -0.0004)
    c = 100 * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + vol * rng.uniform(0, 1, n))
    lo = np.minimum(o, c) * (1 - vol * rng.uniform(0, 1, n))
    buf = HistoryBuffer("SYN-USDT-SWAP", n)
    for i in range(n):
        buf.append(T0 + i * HOUR_MS, o[i], h[i], lo[i], c[i], 1000.0)
    return buf


def contexts(
    buf: HistoryBuffer, warm: int, pos: PositionSnapshot | None = None
) -> Iterator[MarketContext]:
    for n in range(warm, len(buf)):
        view = HistoryView(buf, n)
        yield MarketContext(
            "PAPER",
            T0 + n * HOUR_MS,
            buf.inst_id,
            HOUR_MS,
            BoundedView(view, warm),
            pos,
            None if pos is None else 30,
        )


def decisions(spec: StrategySpec, buf: HistoryBuffer) -> list[Any]:
    s = spec.construct(dict(spec.defaults), "test")
    long_pos = PositionSnapshot(Side.LONG, Decimal(1), Decimal(100), Decimal(90), None)
    out: list[Any] = [s.generate(c) for c in contexts(buf, s.warmup_bars)]
    out += [s.exit(c) for c in contexts(buf, s.warmup_bars, long_pos)]
    return out


# --- contract ------------------------------------------------------------------------------


def test_no_signal_without_a_stop_loss() -> None:
    fields = {
        "env": "PAPER",
        "strategy_id": "x",
        "strategy_version": "v",
        "symbol": "S",
        "ts": "2020-01-01T00:00:00Z",
        "side": "LONG",
        "entry_ref": "100",
        "take_profit": ("110",),
        "conviction": "1",
    }
    with pytest.raises(ValidationError):
        Signal(**fields)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # stop on the wrong side of entry
        Signal(**fields, stop_loss="101")  # type: ignore[arg-type]


# --- constants rule ------------------------------------------------------------------------

ALLOWED_LITERALS = {0, 1, 2}


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_every_numeric_constant_is_a_declared_parameter(module: Any) -> None:
    tree = ast.parse(inspect.getsource(module))
    declared = [
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") in {"DEFAULTS", "GRID"}
    ]
    assert len(declared) == 2, "a candidate declares DEFAULTS and its pre-registered GRID"
    inside = {id(x) for d in declared for x in ast.walk(d)}
    stray = [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float)
        and not isinstance(n.value, bool)
        and id(n) not in inside
        and n.value not in ALLOWED_LITERALS
    ]
    assert stray == [], f"numeric literals outside DEFAULTS: {stray}"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.strategy_id)
def test_build_requires_every_declared_parameter(spec: StrategySpec) -> None:
    full = dict(spec.defaults)
    spec.build(full)
    with pytest.raises(ValueError, match="missing"):
        spec.build({k: v for k, v in full.items() if k != next(iter(full))})
    with pytest.raises(ValueError, match="unknown"):
        spec.build(full | {"hidden_knob": 3})
    assert all(set(p) == set(full) for p in spec.grid([{}, {next(iter(full)): 1}]))


def test_warmup_stays_within_180_days_under_perturbation() -> None:
    for spec in SPECS:
        assert spec.defaults["warmup_bars"] * 1.2 <= 180 * 24, spec.strategy_id


# --- purity --------------------------------------------------------------------------------


def _boom(*_: object, **__: object) -> None:
    raise AssertionError("strategy code touched I/O, the clock or randomness")


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.strategy_id)
def test_generate_and_exit_are_pure(spec: StrategySpec, monkeypatch: pytest.MonkeyPatch) -> None:
    buf = synthetic(7, 6000)
    expected = decisions(spec, buf)
    for target, name in (
        (time, "time"),
        (time, "monotonic"),
        (time, "perf_counter"),
        (random, "random"),
        (random, "randint"),
        (builtins, "open"),
        (socket, "socket"),
        (np.random, "default_rng"),
    ):
        monkeypatch.setattr(target, name, _boom)
    assert decisions(spec, buf) == expected  # identical inputs -> identical outputs


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.strategy_id)
def test_candidates_fire_and_signals_respect_the_contract(spec: StrategySpec) -> None:
    sigs = [d for d in decisions(spec, synthetic(7, 6000)) if isinstance(d, Signal)]
    assert len(sigs) >= 3, "vacuous: the candidate never fired on a fixture built to trigger it"
    assert {s.side for s in sigs} == {"LONG", "SHORT"}
    for s in sigs:
        risk = (s.entry_ref - s.stop_loss) * (1 if s.side == "LONG" else -1)
        assert risk > 0


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.strategy_id)
def test_decisions_ignore_bars_before_the_bounded_window(spec: StrategySpec) -> None:
    """Live keeps only warmup_bars; research must decide identically (bounded buffer).
    Bars [0, POISON) are scaled by 1e6 in one copy (finite, so TA-Lib cannot skip them the
    way it skips leading NaNs). Every decision whose window lies after the poison must match,
    and the span must contain real decisions, so the check cannot pass on all-None."""
    poison = 1000
    clean = synthetic(7, 9000)
    dirty = synthetic(7, 9000)
    for name in ("open", "high", "low", "close"):
        dirty._cols[name][:poison] *= 1e6
    s = spec.construct(dict(spec.defaults), "t")
    warm = s.warmup_bars
    pos = PositionSnapshot(Side.LONG, Decimal(1), Decimal(100), Decimal(90), None)
    live = 0
    for n in range(poison + warm, poison + warm + 2000):
        a = MarketContext(
            "PAPER",
            T0 + n * HOUR_MS,
            clean.inst_id,
            HOUR_MS,
            BoundedView(HistoryView(clean, n), warm),
            None,
            None,
        )
        b = MarketContext(
            "PAPER",
            T0 + n * HOUR_MS,
            dirty.inst_id,
            HOUR_MS,
            BoundedView(HistoryView(dirty, n), warm),
            None,
            None,
        )
        ga, gb = s.generate(a), s.generate(b)
        assert ga == gb, n
        ea = s.exit(MarketContext(a.env, a.ts_ms, a.inst_id, a.timeframe_ms, a.view, pos, 30))
        eb = s.exit(MarketContext(b.env, b.ts_ms, b.inst_id, b.timeframe_ms, b.view, pos, 30))
        assert ea == eb, n
        live += (ga is not None) + ea
    assert live > 0, "vacuous: no decision in the span"


def test_bounded_view_holds_exactly_the_last_n_bars_read_only() -> None:
    buf = synthetic(1, 100)
    v = BoundedView(buf.view(), 50)
    assert len(v) == 50
    assert (v.close == buf.view().closes()[-50:]).all()
    assert int(v.ts_open_ms[0]) == T0 + 50 * HOUR_MS
    with pytest.raises(ValueError):
        v.close[0] = 1.0


# --- adapter and registry ------------------------------------------------------------------


def test_adapter_exits_before_entering_and_never_both() -> None:
    spec = keltner_reversion.SPEC
    adapter = spec.build(dict(spec.defaults))
    buf = synthetic(7, 6000)
    pos = PositionSnapshot(Side.LONG, Decimal(1), Decimal(100), Decimal(90), None)
    actions = set()
    for n in range(500, 2000):
        view = HistoryView(buf, n)
        ctx = StrategyContext(
            T0 + n * HOUR_MS,
            {buf.inst_id: view},
            frozenset({buf.inst_id}),
            {buf.inst_id: pos},
            Decimal(10_000),
        )
        intents = adapter.on_bar(ctx)
        assert all(i.action is Action.EXIT for i in intents)
        actions |= {i.action for i in intents}
    assert actions == {Action.EXIT}  # the time stop fired while holding


def test_registry_refuses_a_changed_version_under_the_same_id() -> None:
    r = Registry()
    spec = trend_breakout.SPEC
    r.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        r.register(StrategySpec(spec.strategy_id, spec.defaults, spec.construct, version="other"))
    assert REGISTRY.ids() == sorted(s.strategy_id for s in SPECS)


def test_allowed_regimes_must_be_empty() -> None:
    spec = trend_breakout.SPEC

    gated = StrategySpec(
        spec.strategy_id,
        spec.defaults,
        lambda p, v: trend_breakout.TrendBreakout(p, v, allowed_regimes=("RANGE",)),
    )
    with pytest.raises(ValueError, match="tripwire"):
        gated.build(dict(spec.defaults))


def test_version_covers_strategy_and_analysis_sources() -> None:
    v = code_version()
    assert len(v) == 64 and v == trend_breakout.SPEC.version


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_grid_is_at_most_8_configurations_of_declared_parameters(module: Any) -> None:
    grid = module.SPEC.grid(module.GRID)
    assert 1 <= len(grid) <= 8
    assert len({tuple(sorted(p.items())) for p in grid}) == len(grid)


# --- trend_breakout's self-aggregated daily bars (Chief Advisor, M4 checkpoint 2) ----------


def test_daily_aggregation_excludes_the_current_day_until_its_2300_bar_has_closed() -> None:
    buf = synthetic(3, 24 * 40)
    view = HistoryView(buf, 24 * 40)
    for n in range(24 * 30, 24 * 40):
        ctx = MarketContext(
            "PAPER",
            T0 + n * HOUR_MS,
            buf.inst_id,
            HOUR_MS,
            BoundedView(HistoryView(buf, n), 24 * 20),
            None,
            None,
        )
        d = trend_breakout.daily(ctx)
        last_bar = n - 1  # the bar that just closed
        if (last_bar + 1) % 24:
            assert d is None, f"decided mid-day at bar {last_bar}"
            continue
        assert d is not None
        hi, lo, cl = d
        day_bars = range(last_bar - 23, last_bar + 1)
        # The last daily bar is exactly the day that just closed - its 24 hours, no more.
        assert cl[-1] == view.bar(last_bar)[4]
        assert hi[-1] == max(view.bar(i)[2] for i in day_bars)
        assert lo[-1] == min(view.bar(i)[3] for i in day_bars)
        assert len(cl) == 20  # 20 whole days in the bounded window, none partial


# --- M4 closing audit: buffer convergence and live parameters -----------------------------
#
# Known defects are STRICT xfails: visible in every run, and a fix that is not also recorded
# (the marker removed, M4_RESEARCH_LOG updated) fails the suite.


def full_decisions(
    spec: StrategySpec,
    params: dict[str, Any],
    buf: HistoryBuffer,
    start: int,
    stop: int,
    warm: int | None = None,
) -> list[Any]:
    """Entries (with stop and target prices) and exits (with varying bars held) over
    [start, stop), from a bounded view of ``warm`` bars (default: the params' warm-up)."""
    s = spec.construct(params, "t")
    w = warm or s.warmup_bars
    pos = PositionSnapshot(Side.LONG, Decimal(1), Decimal(100), Decimal(90), None)
    out: list[Any] = []
    for n in range(start, stop):
        view = BoundedView(HistoryView(buf, n), w)
        flat = MarketContext("PAPER", T0 + n * HOUR_MS, buf.inst_id, HOUR_MS, view, None, None)
        held = MarketContext("PAPER", T0 + n * HOUR_MS, buf.inst_id, HOUR_MS, view, pos, n % 80)
        out.append((s.generate(flat), s.exit(held)))
    return out


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            trend_breakout.SPEC,
            marks=pytest.mark.xfail(
                strict=True,
                reason="KNOWN DEFECT (M4 closing audit): the 20-day Wilder ATR has not "
                "converged in 150 days of daily bars, so stops differ with buffer length",
            ),
        ),
        keltner_reversion.SPEC,
        vol_compression_breakout.SPEC,
    ],
    ids=lambda s: s.strategy_id,
)
def test_decisions_at_warmup_equal_decisions_at_twice_warmup(spec: StrategySpec) -> None:
    """M4_DESIGN §2: a bounded buffer must decide as full history would."""
    warm = int(spec.defaults["warmup_bars"])
    buf = synthetic(11, 2 * warm + 1500)
    p = dict(spec.defaults)
    a = full_decisions(spec, p, buf, 2 * warm, 2 * warm + 1500, warm=warm)
    b = full_decisions(spec, p, buf, 2 * warm, 2 * warm + 1500, warm=2 * warm)
    assert any(d != (None, False) for d in a), "vacuous span"
    assert a == b


DEAD = {("vol_compression_breakout", "bb_k")}
LIVE_CASES = [
    pytest.param(
        spec,
        name,
        marks=[
            pytest.mark.xfail(
                strict=True,
                reason="KNOWN DEFECT (M4 closing audit): bandwidth percentile rank is invariant "
                "to the band multiple; removing it is a new version (second grid, needs sign-off)",
            )
        ]
        if (spec.strategy_id, name) in DEAD
        else [],
        id=f"{spec.strategy_id}.{name}",
    )
    for spec in SPECS
    for name in spec.defaults
    if name != "warmup_bars"  # covered by the convergence test above
]


@pytest.mark.parametrize(("spec", "name"), LIVE_CASES)
def test_every_declared_parameter_changes_a_decision_under_the_g6_step(
    spec: StrategySpec, name: str
) -> None:
    buf = synthetic(7, 9000)
    base = dict(spec.defaults)
    start = int(base["warmup_bars"] * 1.25) + 1
    ref = full_decisions(spec, base, buf, start, len(buf))
    for factor in (0.8, 1.2):
        v = base[name] * factor
        moved = base | {name: round(v) if isinstance(base[name], int) else v}
        if full_decisions(spec, moved, buf, start, len(buf)) != ref:
            return
    pytest.fail(f"{spec.strategy_id}.{name} changes no decision at +/-20%: a dead parameter")
