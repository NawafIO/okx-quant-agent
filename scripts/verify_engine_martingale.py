"""Powered martingale invariant for M2 sign-off (Chief Advisor ruling, 2026-10-03).

    python scripts/verify_engine_martingale.py [--bars-per-task 20000] [--tasks 600]

Gate: on a fat-tailed (Student-t nu=3) price martingale at 48 sub-steps per bar, with zero
fees and the research slippage model ON, random entry must not make money through the
engine: mean net <= +0.5 bps/trade with stderr <= 0.3 bps (>= ~1M trades), with a 3% stop
AND with a stop that never triggers. Pre-cost (net + slippage) is REPORTED alongside: it is
the stop-overshoot optimism the slippage would otherwise mask.
"""

import argparse
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

from okxq.backtest import martingale as mg
from okxq.backtest.costs import RESEARCH_SLIPPAGE


def task(args: tuple[int, int, str]) -> tuple[list[float], list[float], list[str]]:
    n_bars, seed, stop = args
    r = mg.run_once(n_bars, seed, RESEARCH_SLIPPAGE, stop_frac=Decimal(stop))
    nets, pres, reasons = [], [], []
    for t in r.trades:
        notional = t.max_qty * t.avg_entry
        if notional > 0:
            nets.append(float(t.net_pnl / notional) * 1e4)
            pres.append(float((t.net_pnl + t.slippage_cost) / notional) * 1e4)
            reasons.append(t.exit_reason)
    return nets, pres, reasons


def mean_se(xs: list[float]) -> tuple[float, float]:
    mu = math.fsum(xs) / len(xs)
    sd = math.sqrt(math.fsum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
    return mu, sd / math.sqrt(len(xs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-per-task", type=int, default=20_000)
    ap.add_argument("--tasks", type=int, default=600)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    ok = True
    for label, stop in (("3% stop", "0.03"), ("stop never triggers", "0.5")):
        jobs = [(a.bars_per_task, 1000 + s, stop) for s in range(a.tasks)]
        nets: list[float] = []
        pres: list[float] = []
        stops: list[float] = []
        with ProcessPoolExecutor(a.workers) as ex:
            for n, p, rs in ex.map(task, jobs):
                nets += n
                pres += p
                stops += [x for x, r in zip(p, rs, strict=True) if r == "stop"]
        res = mg.evaluate(label, nets)
        pre_mu, pre_se = mean_se(pres)
        print(
            f"{label}: n={res.n:,} net {res.mean_bps:+.3f} +/- {res.stderr_bps:.3f} bps "
            f"(gate <= +{mg.POLICY.max_mean_bps}, se <= {mg.POLICY.max_stderr_bps}) "
            f"-> {'PASS' if res.passed else 'FAIL'}"
        )
        print(
            f"    pre-cost (overshoot, reported) {pre_mu:+.3f} +/- {pre_se:.3f} bps; "
            f"slippage {pre_mu - res.mean_bps:.3f} bps/trade; stop exits {len(stops):,}"
            + (f" at mean {sum(stops) / len(stops):+.2f} bps" if stops else "")
        )
        ok = ok and res.passed
    print("MARTINGALE INVARIANT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
