"""Synthetic fixtures for engine tests. Every venue parameter here is SYNTHETIC by design."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from okxq.backtest.engine import BacktestEngine, EngineConfig, StrategyContext
from okxq.backtest.sizing import SizeDecision
from okxq.backtest.types import (
    BarSeries,
    CostStress,
    FeeSchedule,
    FundingRate,
    InstrumentSpec,
    OrderIntent,
    Provenance,
    Side,
    SlippageModel,
)

H = 3_600_000
T0 = 1_700_000_000_000 // H * H
INST = "TEST-USDT-SWAP"

SPEC = InstrumentSpec(
    inst_id=INST,
    tick_size=Decimal("0.1"),
    lot_size_base=Decimal("0.001"),
    min_size_base=Decimal("0.001"),
    mmr=Decimal("0.005"),
    provenance=Provenance.SYNTHETIC,
)
FEES = FeeSchedule(
    maker=Decimal("0.0002"), taker=Decimal("0.0005"), provenance=Provenance.SYNTHETIC
)
#: Coefficients zero: slippage is exactly the one-tick floor, so fixtures are hand-checkable.
FLOOR_SLIPPAGE = SlippageModel(Decimal(0), 20, 0, "test-floor-only")


def ts(i: int) -> int:
    return T0 + i * H


def series(
    rows: Sequence[tuple[str, str, str, str, str]],
    *,
    inst: str = INST,
    index: Sequence[int] | None = None,
) -> BarSeries:
    idx = list(index) if index is not None else list(range(len(rows)))
    return BarSeries(
        inst_id=inst,
        timeframe_ms=H,
        ts_open_ms=tuple(ts(i) for i in idx),
        open=tuple(Decimal(r[0]) for r in rows),
        high=tuple(Decimal(r[1]) for r in rows),
        low=tuple(Decimal(r[2]) for r in rows),
        close=tuple(Decimal(r[3]) for r in rows),
        volume_base=tuple(Decimal(r[4]) for r in rows),
    )


def flat_funding(n: int, overrides: dict[int, str] | None = None) -> list[FundingRate]:
    """Hourly settlements at rate 0, except the given bar indices."""
    o = overrides or {}
    return [FundingRate(ts(i), Decimal(o.get(i, "0"))) for i in range(-1, n + 2)]


@dataclass(frozen=True)
class FixedSizer:
    qty: Decimal = Decimal(1)
    leverage: Decimal = Decimal(10)
    sizer_id: str = "test-fixed"

    def size(self, **_: object) -> SizeDecision:
        return SizeDecision(self.qty, self.leverage)


@dataclass
class Scripted:
    """Emits pre-scripted intents at given decision times; records what it saw."""

    script: dict[int, list[OrderIntent]] = field(default_factory=dict)
    strategy_id: str = "scripted"
    seen: list[StrategyContext] = field(default_factory=list)
    hook: Callable[[StrategyContext], None] | None = None

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        self.seen.append(ctx)
        if self.hook is not None:
            self.hook(ctx)
        return self.script.get(ctx.ts_ms, [])


def engine(
    bars: BarSeries | dict[str, BarSeries],
    *,
    funding: list[FundingRate] | dict[str, list[FundingRate]] | None = None,
    start: int,
    end: int,
    sizer: object | None = None,
    stress: CostStress | None = None,
    participation_cap: str = "1",
    ttl: int = 1,
    synthetic: bool = True,
    spec: InstrumentSpec = SPEC,
) -> BacktestEngine:
    all_series = bars if isinstance(bars, dict) else {bars.inst_id: bars}
    if funding is None:
        n = max(len(s) for s in all_series.values()) + 5
        funding = {k: flat_funding(n) for k in all_series}
    elif isinstance(funding, list):
        funding = {k: funding for k in all_series}
    specs = {
        k: InstrumentSpec(
            k, spec.tick_size, spec.lot_size_base, spec.min_size_base, spec.mmr, spec.provenance
        )
        for k in all_series
    }
    return BacktestEngine(
        series=all_series,
        specs=specs,
        funding=funding,
        config=EngineConfig(
            fees=FEES,
            slippage=FLOOR_SLIPPAGE,
            initial_equity=Decimal(10_000),
            participation_cap=Decimal(participation_cap),
            entry_ttl_bars=ttl,
            stress=stress or CostStress(),
            synthetic=synthetic,
        ),
        sizer=sizer or FixedSizer(),  # type: ignore[arg-type]
        trade_start_ms=start,
        end_ms=end,
    )


LONG = Side.LONG
