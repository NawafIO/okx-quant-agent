"""Walk-forward protocol end to end on synthetic data: folds, trial accounting, holdout."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from okxq.audit.chain import read_chain
from okxq.backtest.engine import EngineConfig, StrategyContext
from okxq.backtest.gates import FROZEN, Verdict
from okxq.backtest.holdout import HoldoutSealedError
from okxq.backtest.sizing import ProvisionalFixedFractionalSizer
from okxq.backtest.trials import TrialLog
from okxq.backtest.types import Action, BarSeries, FundingRate, OrderIntent
from okxq.backtest.walkforward import ResearchProtocol, make_folds
from okxq.env.profiles import build_profile

from .conftest import FEES, FLOOR_SLIPPAGE, INST, SPEC

H4 = 4 * 3_600_000


def utc(y: int, mo: int) -> int:
    return int(datetime(y, mo, 1, tzinfo=UTC).timestamp()) * 1000


START, END = utc(2022, 1), utc(2023, 7)  # 18 months -> folds testing months 12-15, 15-18


def _walk(seed: int) -> BarSeries:
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture
    t, price, rows = utc(2021, 12), 1000.0, []
    while t + H4 <= END:
        o = round(price, 1)
        c = round(max(1.0, o * (1 + rng.gauss(0, 0.015))), 1)
        h = round(max(o, c) * 1.004, 1)
        lo = round(min(o, c) * 0.996, 1)
        rows.append((t, o, h, lo, c))
        price, t = c, t + H4
    d = Decimal
    return BarSeries(
        INST,
        H4,
        tuple(r[0] for r in rows),
        tuple(d(str(r[1])) for r in rows),
        tuple(d(str(r[2])) for r in rows),
        tuple(d(str(r[3])) for r in rows),
        tuple(d(str(r[4])) for r in rows),
        tuple(d(1000) for _ in rows),
    )


SERIES = _walk(5)
FUNDING = [FundingRate(t, Decimal("0.0001")) for t in range(utc(2021, 12), END + H4, 8 * 3_600_000)]


def provider(
    start: int, end: int
) -> tuple[Mapping[str, BarSeries], Mapping[str, Sequence[FundingRate]]]:
    idx = [i for i, t in enumerate(SERIES.ts_open_ms) if t >= start and t + H4 <= end]
    s = SERIES
    cut = BarSeries(
        INST,
        H4,
        tuple(s.ts_open_ms[i] for i in idx),
        tuple(s.open[i] for i in idx),
        tuple(s.high[i] for i in idx),
        tuple(s.low[i] for i in idx),
        tuple(s.close[i] for i in idx),
        tuple(s.volume_base[i] for i in idx),
    )
    return {INST: cut}, {
        INST: [r for r in FUNDING if start - 8 * 3_600_000 <= r.ts_ms <= end + 8 * 3_600_000]
    }


@dataclass
class Momentum:
    lookback: int
    hold: int
    strategy_id: str = "test-momentum"
    _held: int = field(default=0, init=False)

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        v = ctx.views[INST]
        if INST in ctx.positions:
            self._held += 1
            return [OrderIntent(INST, Action.EXIT)] if self._held >= self.hold else []
        self._held = 0
        if len(v) <= self.lookback:
            return []
        now, then = v.closes(self.lookback + 1)[[-1, 0]]
        c = Decimal(repr(float(now)))
        if now > then:
            return [OrderIntent(INST, Action.ENTER_LONG, c * Decimal("0.95"))]
        return [OrderIntent(INST, Action.ENTER_SHORT, c * Decimal("1.05"))]


@dataclass(frozen=True)
class Factory:
    strategy_id: str = "test-momentum"
    version: str = "1"

    def build(self, params: dict[str, Any]) -> Momentum:
        return Momentum(lookback=params["lookback"], hold=params["hold"])


GRID = [{"lookback": 6, "hold": 3}, {"lookback": 12, "hold": 6}]


def protocol(tmp_path: Path) -> tuple[ResearchProtocol, TrialLog]:
    cfg = EngineConfig(
        fees=FEES,
        slippage=FLOOR_SLIPPAGE,
        initial_equity=Decimal(10_000),
        participation_cap=Decimal(1),
        synthetic=True,
    )
    proto = ResearchProtocol(
        factory=Factory(),
        data=provider,
        specs={INST: SPEC},
        config=cfg,
        sizer=ProvisionalFixedFractionalSizer(Decimal("0.01"), Decimal(3)),
        profile=build_profile("PAPER", root=tmp_path),
        warmup_ms=30 * H4,
    )
    return proto, proto.trials


def test_folds_follow_the_frozen_12_3_3_layout() -> None:
    folds = make_folds(START, END)
    assert [(f.train_start_ms, f.test_start_ms, f.test_end_ms) for f in folds] == [
        (utc(2022, 1), utc(2023, 1), utc(2023, 4)),
        (utc(2022, 4), utc(2023, 4), utc(2023, 7)),
    ]


def test_walk_forward_cannot_reach_the_holdout() -> None:
    with pytest.raises(HoldoutSealedError):
        make_folds(utc(2024, 1), FROZEN.holdout_start_ms + 1)


def test_protocol_counts_every_configuration_it_evaluated(tmp_path: Path) -> None:
    proto, log = protocol(tmp_path)
    result = proto.run(GRID, START, END)
    # 2 folds x (2 selection + 1 oos) + final selection 2
    # + perturbations: (lookback, hold) x (0.8, 1.2) x 2 folds' OOS = 8
    # + stress full 1 + stress oos 2 = 19
    assert log.stats().n_trials == 19
    assert result.report.n_trials == 19
    assert {o.gate for o in result.report.outcomes} == {f"G-{i}" for i in range(1, 10)}
    assert result.report.gates_sha256 == FROZEN.sha256()
    # Every verdict records what it was priced at (M-1).
    assert result.report.fee_provenance == "SYNTHETIC"
    assert len(result.report.cost_config_sha) == 64
    # A random walk has no edge; whatever the verdict, it must not be ACCEPT.
    assert result.report.verdict is not Verdict.ACCEPT


def test_protocol_is_deterministic(tmp_path: Path) -> None:
    a, _ = protocol(tmp_path / "a")
    b, _ = protocol(tmp_path / "b")
    ra, rb = a.run(GRID, START, END), b.run(GRID, START, END)
    assert [(o.gate, o.status, o.observed) for o in ra.report.outcomes] == [
        (o.gate, o.status, o.observed) for o in rb.report.outcomes
    ]
    assert ra.final_params == rb.final_params


def test_the_oos_curve_is_stitched_without_a_reset(tmp_path: Path) -> None:
    proto, _ = protocol(tmp_path)
    wf = proto.walk_forward(GRID, START, END)
    ran = [fr for fr in wf.folds if fr.oos is not None]
    assert len(ran) == 2
    first, second = ran[0].oos, ran[1].oos
    assert first is not None and second is not None
    assert second.summary.equity[0] == first.summary.equity[-1]


def test_g6_perturbs_selected_params_out_of_sample_only(tmp_path: Path) -> None:
    """Checkpoint-3 finding 3: every perturbation run is on a fold's OOS window."""
    proto, log = protocol(tmp_path)
    wf = proto.walk_forward(GRID, START, END)
    oos_windows = {(fr.fold.test_start_ms, fr.fold.test_end_ms) for fr in wf.folds}
    perts = proto.perturb(wf, START, END)
    assert perts
    assert all(not p.untestable for p in perts)
    records = [r for r in read_chain(log.path) if r.payload["purpose"] == "perturbation"]
    assert records
    assert {tuple(r.payload["window"]) for r in records} <= oos_windows


def test_a_number_hidden_in_a_string_makes_g6_invalid(tmp_path: Path) -> None:
    """Checkpoint-3 finding 6."""
    from okxq.backtest.gates import Status, g6

    proto, _ = protocol(tmp_path)
    grid = [{"lookback": 6, "hold": 3, "stop": "0.05"}]
    wf = proto.walk_forward(grid, START, END)
    perts = proto.perturb(wf, START, END)
    assert any(p.untestable and p.param == "stop" for p in perts)
    assert g6(perts).status is Status.INVALID


@pytest.mark.parametrize(
    ("values", "kind"),
    [
        ([6, 12], "numeric"),
        ([True], "categorical"),
        (["fast"], "categorical"),
        (["0.05"], "untestable"),
        ([(20, 50)], "untestable"),
        ([6, "x"], "untestable"),
    ],
)
def test_param_kinds(values: list[Any], kind: str) -> None:
    from okxq.backtest.walkforward import _param_kind

    assert _param_kind(values) == kind


def test_integer_scaling_rounds_half_up() -> None:
    from okxq.backtest.walkforward import _scale

    assert _scale(2, Decimal("0.8")) == 2  # 1.6 -> 2: unperturbable
    assert _scale(3, Decimal("1.2")) == 4  # 3.6 -> 4
    assert _scale(5, Decimal("0.8")) == 4
