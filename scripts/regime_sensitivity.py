"""Regime threshold sensitivity (Chief Advisor finding M-3) - DESCRIPTIVE, scores no key.

    python scripts/regime_sensitivity.py

Perturbs each numeric threshold by -20% and +20% (integers rounded, at least 1 apart from
the frozen value), one at a time, and reports the share of research-window days whose label
differs from the frozen classifier's, per instrument. A threshold whose small move flips many
labels is one the labels cannot be trusted to. Uses ``classify_with`` (sensitivity only).
"""

import dataclasses
import sys

from okxq.analysis.regime import FROZEN_REGIME, RegimeParams, classify, classify_with
from okxq.analysis.ta import Bars
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile

INSTRUMENTS = ("BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")
SKIP = {"alignment", "min_bars"}


def perturbed(name: str, factor: float) -> RegimeParams:
    base = getattr(FROZEN_REGIME, name)
    if isinstance(base, int):
        v: float = round(base * factor)
        if v == base:
            v = base + (1 if factor > 1 else -1)
    else:
        v = base * factor
    if name == "vol_pctl_high":
        v = min(v, 0.999)  # a percentile above 1 never fires
    fields = {f.name: getattr(FROZEN_REGIME, f.name) for f in dataclasses.fields(RegimeParams)}
    fields[name] = v
    return RegimeParams(**fields)


def main() -> int:
    store = ParquetStore(build_profile("PAPER").parquet_root)
    names = [f.name for f in dataclasses.fields(RegimeParams) if f.name not in SKIP]
    print(f"{'threshold':<22}{'frozen':>8}" + "".join(f"{i[:4]:>14}" for i in INSTRUMENTS))
    bars = {}
    for inst in INSTRUMENTS:
        s = load_research_bars(store, inst, "1d", 0, FROZEN.holdout_start_ms)
        bars[inst] = Bars.of(
            *(list(map(float, c)) for c in (s.open, s.high, s.low, s.close, s.volume_base))
        )
    base = {i: classify(b) for i, b in bars.items()}
    for n in names:
        cells = []
        for inst in INSTRUMENTS:
            flips = []
            for fac in (0.8, 1.2):
                lab = classify_with(bars[inst], perturbed(n, fac))
                flips.append(sum(a != b for a, b in zip(lab, base[inst], strict=True)) / len(lab))
            cells.append(f"{flips[0]:>6.1%}/{flips[1]:<6.1%}")
        print(f"{n:<22}{getattr(FROZEN_REGIME, n)!s:>8}  " + "  ".join(cells))
    print("(cells: share of days relabelled at -20% / +20%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
