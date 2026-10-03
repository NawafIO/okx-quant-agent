"""intraday_momentum feasibility check - signal-free (docs/strategies/intraday_momentum.md,
"Feasibility computation"; docs/CYCLE2_DESIGN.md §5).

    python scripts/c2_feasibility.py               # fees-only bound (step 3)

Asks only whether ANY signal could pay the round-trip cost: the break-even hit rate from the
mean MAGNITUDE of the 22:00 -> 23:00 UTC fill-to-fill move. No direction is read, nothing is
selected, no trial is logged. It can only stop the candidate, never start it. The rationale
is pinned by SHA-256 below (committed before this script first ran); an edited rationale is
refused. Reads only through the research door. The result goes on the PAPER audit chain.
"""

import hashlib
import itertools
import json
import math
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from okxq.audit.chain import AuditChain
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs

ROOT = Path(__file__).resolve().parents[1]
RATIONALE = ROOT / "docs/strategies/intraday_momentum.md"
PINNED = "14d217cc8a62e4228796b41fd7dbd9e8a8400df587d1397b62e402f2f3d98f26"
H = 3_600_000
DAY = 24 * H
START_MS = int(datetime(2020, 7, 1, tzinfo=UTC).timestamp() * 1000)
FEES_ONLY_COST = 0.0010  # 2 x 0.05% taker (FEES_MEASURED_PERP)
K = 0.5
SIGMA_N = 24
CUTOFF = 0.55


def p_star(cost: float, e_abs: float) -> float:
    return 0.5 + cost / (2 * e_abs)


def instrument(store: ParquetStore, inst: str) -> dict[str, float | int]:
    s = load_research_bars(store, inst, "1h", 0, FROZEN.holdout_start_ms)
    at = {int(t): i for i, t in enumerate(s.ts_open_ms)}
    o = [float(x) for x in s.open]
    c = [float(x) for x in s.close]
    late_all: list[float] = []
    late_k: list[float] = []
    first = max(START_MS, (int(s.ts_open_ms[0]) // DAY + 2) * DAY)
    for d0 in range(first, FROZEN.holdout_start_ms, DAY):
        need = [d0 - j * H for j in range(SIGMA_N, -1, -1)] + [d0 + 22 * H, d0 + 23 * H]
        if any(t not in at for t in need):
            continue
        closes = [c[at[t]] for t in need[: SIGMA_N + 1]]
        rets = [math.log(b / a) for a, b in itertools.pairwise(closes)]
        sigma = statistics.stdev(rets)
        i0 = at[d0]
        r_first = math.log(c[i0] / o[i0])
        late = abs(math.log(o[at[d0 + 23 * H]] / o[at[d0 + 22 * H]]))
        late_all.append(late)
        if abs(r_first) > K * sigma:
            late_k.append(late)
    e_u = statistics.fmean(late_all)
    e_k = statistics.fmean(late_k)
    return {
        "days": len(late_all),
        "days_k": len(late_k),
        "E_u": e_u,
        "E_k": e_k,
        "p_u": p_star(FEES_ONLY_COST, e_u),
        "p_k": p_star(FEES_ONLY_COST, e_k),
    }


def volume_profile(store: ParquetStore, members: list[str]) -> list[float]:
    shares: list[list[float]] = []
    for inst in members:
        s = load_research_bars(store, inst, "1h", 0, FROZEN.holdout_start_ms)
        by_ts = {int(t): float(v) for t, v in zip(s.ts_open_ms, s.volume_base, strict=True)}
        for d0 in range(START_MS, FROZEN.holdout_start_ms, DAY):
            vols = [by_ts.get(d0 + h * H) for h in range(24)]
            if any(v is None for v in vols):
                continue
            tot = sum(v for v in vols if v is not None)
            if tot > 0:
                shares.append([v / tot for v in vols if v is not None])
    return [statistics.fmean(col) for col in zip(*shares, strict=True)]


def main() -> int:
    # LF-normalised: a Windows (CRLF) checkout must not read as an edited rationale.
    actual = hashlib.sha256(RATIONALE.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != PINNED:
        print(f"REFUSED: {RATIONALE.name} changed after pinning ({actual})")
        return 2
    profile = build_profile("PAPER")
    ensure_dirs(profile)
    store = ParquetStore(profile.parquet_root)
    members: list[str] = json.loads((ROOT / "docs/universe_m4.json").read_text("utf-8"))["members"]
    rows = {inst: instrument(store, inst) for inst in members}
    print(
        f"intraday_momentum feasibility, FEES-ONLY bound c={FEES_ONLY_COST}; "
        f"rationale {actual[:12]}"
    )
    print(
        f"  {'instrument':<16}{'days':>6}{'k-days':>8}{'E_u bp':>9}{'E_k bp':>9}"
        f"{'p*_u':>8}{'p*_k':>8}"
    )
    for inst, r in rows.items():
        print(
            f"  {inst:<16}{r['days']:>6}{r['days_k']:>8}{r['E_u'] * 1e4:>9.1f}"
            f"{r['E_k'] * 1e4:>9.1f}{r['p_u']:>8.3f}{r['p_k']:>8.3f}"
        )
    m_u = statistics.median(float(r["p_u"]) for r in rows.values())
    m_k = statistics.median(float(r["p_k"]) for r in rows.values())
    stop = min(m_u, m_k) > CUTOFF
    verdict = (
        "DISCARDED on feasibility (no trials)" if stop else "NOT STOPPED by the fees-only bound"
    )
    print(f"  median p*: M_u {m_u:.4f}  M_k {m_k:.4f}  min {min(m_u, m_k):.4f}  cutoff {CUTOFF}")
    print(f"VERDICT: {verdict}")
    prof = volume_profile(store, members)
    print("\nreport-only - mean share of daily base volume by UTC hour:")
    for h in range(0, 24, 6):
        print("  " + "  ".join(f"{hh:02d}h {prof[hh]:.3f}" for hh in range(h, h + 6)))
    AuditChain(profile.audit_log).append(
        "c2_feasibility",
        {
            "rationale_sha256": actual,
            "bound": "fees_only",
            "cost": FEES_ONLY_COST,
            "per_instrument": rows,
            "M_u": m_u,
            "M_k": m_k,
            "cutoff": CUTOFF,
            "stopped": stop,
            "volume_share_by_hour": prof,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
