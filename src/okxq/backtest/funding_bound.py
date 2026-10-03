"""``funding-bound-v1`` - the adverse funding bound used where no validated model exists.

Chief Advisor ruling on the D1 escalation (2026-10-03, ``docs/M2_DESIGN.md`` §2c): the
premium model failed validation, and the 95-day realised overlap cannot validate ANY model
across 6.8 years of regimes. Until a model passes FORWARD validation on P-11 data, every
settlement without a realised rate is charged an adverse bound, per instrument and side:

* a long pays ``max(0, max realised rate)`` at every settlement;
* a short pays ``max(0, -min realised rate)`` at every settlement;
* nothing is ever credited, and G-9 doubles it.

It is honest about what it is: the extremes of a 95-day, low-funding regime. It is **not** a
bound on 2020-2021 funding, when longs paid far more. That limit travels with every result
in ``BacktestResult.assumptions``.

The bound is derived once, through the logged calibration door, and frozen in
``docs/funding_bound_v1.json`` so the research door never has to read realised funding from
inside the holdout.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from okxq.backtest.funding_model import measured_interval_ms, schedule
from okxq.backtest.types import FundingRate
from okxq.env.profiles import PROJECT_ROOT

BOUND_VERSION = "funding-bound-v1"
BOUND_FILE = PROJECT_ROOT / "docs" / "funding_bound_v1.json"


@dataclass(frozen=True)
class InstrumentBound:
    inst_id: str
    interval_ms: int
    #: The first realised settlement: the bound applies strictly before it, on its grid.
    anchor_ms: int
    cost_long: Decimal
    cost_short: Decimal
    n_realised: int
    last_realised_ms: int


def derive(inst_id: str, realised: Sequence[FundingRate]) -> InstrumentBound:
    rs = sorted(realised, key=lambda r: r.ts_ms)
    if len(rs) < 2:
        raise ValueError(f"{inst_id}: need realised funding to derive a bound")
    rates = [r.rate for r in rs]
    return InstrumentBound(
        inst_id=inst_id,
        interval_ms=measured_interval_ms(rs),
        anchor_ms=rs[0].ts_ms,
        cost_long=max(Decimal(0), max(rates)),
        cost_short=max(Decimal(0), -min(rates)),
        n_realised=len(rs),
        last_realised_ms=rs[-1].ts_ms,
    )


def write_bounds(bounds: Sequence[InstrumentBound], path: Path | None = None) -> None:
    # The default is resolved at CALL time: a `path=BOUND_FILE` default would be frozen at
    # import, so redirecting the module attribute (tests) would still hit the real file.
    path = path if path is not None else BOUND_FILE
    payload = {
        "version": BOUND_VERSION,
        "ruling": "Chief Advisor D1 escalation ruling, 2026-10-03 (docs/M2_DESIGN.md)",
        "caveat": "95-day low-funding regime extremes; NOT a bound on 2020-2021 funding",
        "instruments": {
            b.inst_id: {k: str(v) for k, v in asdict(b).items() if k != "inst_id"}
            for b in sorted(bounds, key=lambda b: b.inst_id)
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_bounds(path: Path | None = None) -> dict[str, InstrumentBound]:
    path = path if path is not None else BOUND_FILE
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != BOUND_VERSION:
        raise ValueError(f"{path}: expected {BOUND_VERSION}, found {raw.get('version')}")
    return {
        inst: InstrumentBound(
            inst_id=inst,
            interval_ms=int(v["interval_ms"]),
            anchor_ms=int(v["anchor_ms"]),
            cost_long=Decimal(v["cost_long"]),
            cost_short=Decimal(v["cost_short"]),
            n_realised=int(v["n_realised"]),
            last_realised_ms=int(v["last_realised_ms"]),
        )
        for inst, v in raw["instruments"].items()
    }


def bound_series(b: InstrumentBound, start_ms: int, end_ms: int) -> list[FundingRate]:
    """Bound settlements in ``[start, min(end, anchor))`` on the measured grid."""
    return [
        FundingRate(t, b.cost_long, modelled=True, bound_long=b.cost_long, bound_short=b.cost_short)
        for t in schedule(b.anchor_ms, b.interval_ms, start_ms, min(end_ms, b.anchor_ms))
    ]
