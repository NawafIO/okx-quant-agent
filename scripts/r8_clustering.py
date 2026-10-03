"""R-8 correlation-clustering demonstration (roadmap M3, Chief Advisor finding M-5).

    python scripts/r8_clustering.py

Scores the expectations PRE-REGISTERED in docs/r8_preregistration.json (pinned by SHA-256
below, committed before this script first ran). Reads only through the research door.
Descriptive, batch statistics (quant.KIND): this informs the RC-07 design, it gates nothing.
The result is recorded on the PAPER audit chain.
"""

import hashlib
import itertools
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from okxq.analysis.quant import average_linkage, clusters_at, correlation_matrix
from okxq.audit.chain import AuditChain
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "docs/r8_preregistration.json"
PINNED = "7b9ed1123da169c7351605a2f1d18d5e6deba4301b51bc99cce47e9a4d5540be"
DAY_MS = 86_400_000
START_MS = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
BTC, XAU = "BTC-USDT-SWAP", "XAU-USDT-SWAP"
CUT = 0.5


def day(ts: int) -> date:
    return datetime.fromtimestamp(ts / 1000, tz=UTC).date()


def returns_by_ts(store: ParquetStore, inst: str) -> tuple[dict[int, float], dict[int, float]]:
    """Daily log returns between CONSECUTIVE daily bars only (a gap yields no return), and
    closes, both keyed by ts_open_ms, from the window start to the holdout start."""
    s = load_research_bars(store, inst, "1d", 0, FROZEN.holdout_start_ms)
    ts = [int(t) for t in s.ts_open_ms]
    c = [float(x) for x in s.close]
    rets = {
        ts[k]: math.log(c[k] / c[k - 1])
        for k in range(1, len(ts))
        if ts[k] - ts[k - 1] == DAY_MS and ts[k] >= START_MS
    }
    return rets, dict(zip(ts, c, strict=True))


def rho(a: dict[int, float], b: dict[int, float], keys: list[int] | None = None) -> float:
    common = sorted(set(a) & set(b)) if keys is None else [k for k in keys if k in a and k in b]
    if len(common) < 2:
        return math.nan
    x = np.array([a[k] for k in common])
    y = np.array([b[k] for k in common])
    if np.std(x) == 0 or np.std(y) == 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def mean_pairwise(rets: dict[str, dict[int, float]], names: list[str], keys: list[int]) -> float:
    vals = [rho(rets[a], rets[b], keys) for i, a in enumerate(names) for b in names[i + 1 :]]
    vals = [v for v in vals if not math.isnan(v)]
    return sum(vals) / len(vals)


def main() -> int:
    # LF-normalised: a Windows (CRLF) checkout must not read as an edited pre-registration.
    actual = hashlib.sha256(PREREG.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != PINNED:
        print(f"REFUSED: {PREREG.name} changed after pre-registration ({actual})")
        return 2
    pre = json.loads(PREREG.read_text(encoding="utf-8"))
    profile = build_profile("PAPER")
    ensure_dirs(profile)
    store = ParquetStore(profile.parquet_root)
    stored = sorted(
        p.name.removeprefix("inst_id=") for p in (profile.parquet_root / "ohlcv").iterdir()
    )
    rets: dict[str, dict[int, float]] = {}
    closes: dict[str, dict[int, float]] = {}
    excluded = []
    for inst in stored:
        try:
            r, c = returns_by_ts(store, inst)
        except Exception as e:  # an instrument with no research data is excluded
            excluded.append(f"{inst} ({type(e).__name__})")
            continue
        if not c or min(c) >= FROZEN.holdout_start_ms or not r:
            excluded.append(inst)
            continue
        rets[inst], closes[inst] = r, c
    print(f"universe {len(rets)}: {sorted(rets)}")
    print(f"excluded by rule (no research-window data): {excluded}")
    out: list[str] = []
    ok_all = True

    cm = correlation_matrix(rets, min_overlap=60)
    ix = {n: i for i, n in enumerate(cm.names)}
    alts = [n for n in cm.names if n not in (BTC, XAU)]
    alt_btc = {a: cm.rho[ix[a], ix[BTC]] for a in alts}
    print("\nfull-window rho(alt, BTC) and overlap days:")
    for a in sorted(alts, key=lambda a: -np.nan_to_num(alt_btc[a], nan=-9)):
        print(f"  {a:<16} {alt_btc[a]:+.3f}  n={cm.overlap[ix[a], ix[BTC]]}")
    if XAU in ix:
        print(f"  {XAU:<16} {cm.rho[ix[XAU], ix[BTC]]:+.3f}  n={cm.overlap[ix[XAU], ix[BTC]]}")

    defined = [v for v in alt_btc.values() if not math.isnan(v)]
    med = float(np.median(defined))
    e1 = med >= 0.60
    out.append(f"[{'PASS' if e1 else 'FAIL'}] E1 median rho(alt, BTC) {med:.3f} (>=0.60)")

    groups = clusters_at(cm, CUT)
    full = pre["full_history_set"]
    e2 = any(set(full) <= g for g in groups)
    out.append(
        f"[{'PASS' if e2 else 'FAIL'}] E2 full-history set in one cluster at cut {CUT}: "
        f"{[sorted(g) for g in groups]}"
    )

    if XAU in ix:
        xr = cm.rho[ix[XAU], ix[BTC]]
        crypto: frozenset[str] = next((g for g in groups if BTC in g), frozenset())
        e3 = XAU not in crypto and xr < 0.30
        out.append(
            f"[{'PASS' if e3 else 'FAIL'}] E3 XAU outside crypto cluster and rho(XAU,BTC) "
            f"{xr:+.3f} (<0.30); overlap {cm.overlap[ix[XAU], ix[BTC]]} days"
        )
    else:
        e3 = False
        out.append("[FAIL] E3 XAU has no research-window data")

    # E4 - BTC's worst calendar month, by month-end closes (mechanical).
    month_end: dict[tuple[int, int], float] = {}
    for t in sorted(closes[BTC]):
        d = day(t)
        month_end[(d.year, d.month)] = closes[BTC][t]
    months = sorted(month_end)
    mret = {
        m: month_end[m] / month_end[p] - 1 for p, m in itertools.pairwise(months) if m >= (2023, 1)
    }
    worst = min(mret, key=lambda m: mret[m])
    wkeys = sorted(t for t in rets[BTC] if (day(t).year, day(t).month) == worst)
    allkeys = sorted(rets[BTC])
    mp_full = mean_pairwise(rets, full, allkeys)
    mp_worst = mean_pairwise(rets, full, wkeys)
    e4 = mp_worst >= mp_full
    out.append(
        f"[{'PASS' if e4 else 'FAIL'}] E4 worst BTC month {worst[0]}-{worst[1]:02d} "
        f"({mret[worst]:+.1%}, {len(wkeys)} days): mean pairwise rho {mp_worst:.3f} vs "
        f"full-window {mp_full:.3f} (worst >= full)"
    )
    ok_all = e1 and e2 and e3 and e4

    print("\nreport-only - rolling 90-day rho(alt, BTC) (>= 60 common days):")
    roll: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for a in alts:
        for t in allkeys:
            keys = [k for k in allkeys if t - 90 * DAY_MS < k <= t]
            ks = [k for k in keys if k in rets[a]]
            if len(ks) >= 60:
                roll[a].append((day(t), rho(rets[a], rets[BTC], ks)))
        if roll[a]:
            lo = min(roll[a], key=lambda x: x[1])
            hi = max(roll[a], key=lambda x: x[1])
            print(f"  {a:<16} min {lo[1]:+.3f} ({lo[0]})  max {hi[1]:+.3f} ({hi[0]})")
        else:
            print(f"  {a:<16} fewer than 60 common days in every 90-day window")

    print("\nreport-only - average-linkage merge order (distance 1 - rho):")
    for m in average_linkage(cm):
        print(f"  {m.distance:.3f}  {sorted(m.left)} + {sorted(m.right)}")

    wrets = {n: {k: v for k, v in r.items() if k in set(wkeys)} for n, r in rets.items()}
    wcm = correlation_matrix(wrets, min_overlap=15)
    print(
        f"\nreport-only - clusters at cut {CUT} within {worst[0]}-{worst[1]:02d} (>=15 days): "
        f"{[sorted(g) for g in clusters_at(wcm, CUT)]}"
    )

    print()
    for ln in out:
        print("  " + ln)
    print("R-8 DEMONSTRATION:", "ALL EXPECTATIONS MET" if ok_all else "NOT ALL MET")
    AuditChain(profile.audit_log).append(
        "r8_clustering",
        {"prereg_sha256": actual, "passed": ok_all, "lines": out, "universe": sorted(rets)},
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
