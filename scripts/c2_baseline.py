"""Track 1 lower-bound feasibility baseline (docs/c2_baseline_spec.md, pinned below).

    python scripts/c2_baseline.py            # all runs in the spec, 4 worker processes

Signal-free random-entry brackets through the real engine and the D3 risk gate. It is NOT a
trial: it calls BacktestEngine directly (never the walk-forward protocol) and refuses to
finish if the trial log changed. Research door only; the holdout stays sealed. Its one use is
the pinned stop rule, which can only DISCARD a candidate.

OBSERVE_HALTS is permitted here and in okxq/backtest/riskgate.py only (guard).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from okxq.analysis.regime import classify
from okxq.analysis.ta import Bars
from okxq.audit.chain import AuditChain
from okxq.backtest import costs
from okxq.backtest.cli import _load_specs
from okxq.backtest.engine import BacktestEngine, StrategyContext
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars, load_research_funding
from okxq.backtest.riskgate import DailyLabel, GateFacts, GateMode, ResearchRiskGate
from okxq.backtest.sizing import RESEARCH_SIZING
from okxq.backtest.trials import TrialLog
from okxq.backtest.types import Action, BarSeries, OrderIntent, SlippageModel
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/c2_baseline_spec.md"
PINNED = "924be378bffc83d4710073a993b01614a9048ba8b13106cf40409c6376a96f59"
OUT = ROOT / "docs/c2_runs"
H = 3_600_000
DAY = 86_400_000
START_MS = int(datetime(2020, 7, 1, tzinfo=UTC).timestamp() * 1000)
CANDIDATES: dict[str, tuple[tuple[int, ...], float]] = {
    "session_orb": ((2, 3, 4, 5), 0.5),
    "impulse_continuation": ((1, 2, 3, 4, 5), 0.12),
}
TARGETS = (Decimal("1.5"), Decimal("2.0"))
STOP_ATR = Decimal(1)
ATR_N = 24
MAX_HOLD = 6
FORCED_K = 7
CUTOFF_R = 0.15
DECISION_SEEDS = tuple(range(20))
REPORT_SEEDS = tuple(range(5))
LB_SLIPPAGE = SlippageModel(
    y_impact=Decimal(1),
    vol_lookback=24,
    spread_lookback=0,
    assumption_id="c2-baseline LOWER BOUND: slip-v2 impact Y=1, stop overshoot k=0.2, "
    "spread = 1-tick floor (no Abdi-Ranaldo)",
    stop_overshoot_k=Decimal("0.2"),
)


def _uniforms(seed: int, inst: str, block_ms: int) -> tuple[float, float, float]:
    d = hashlib.sha256(f"{seed}|{inst}|{block_ms}".encode()).digest()
    return tuple(int.from_bytes(d[i : i + 8], "big") / 2**64 for i in (0, 8, 16))  # type: ignore[return-value]


@dataclass
class RandomBracket:
    """Random entries at eligible block offsets; stop 1 ATR, maker target, time exits."""

    seed: int
    eligible: tuple[int, ...]
    p_entry: float
    target_atr: Decimal
    strategy_id: str = "c2-random-bracket"
    #: (inst, entry decision ts) -> (side, stop distance, stop, target)
    plans: dict[tuple[str, int], tuple[str, Decimal, Decimal, Decimal]] = field(
        default_factory=dict
    )
    _entered: dict[str, int] = field(default_factory=dict)

    def on_bar(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        out: list[OrderIntent] = []
        t = ctx.ts_ms
        k = (t // H) % 24 % 8
        block = t - k * H
        for inst in sorted(ctx.closed_now):
            if inst in ctx.positions:
                held = (t - self._entered.get(inst, t)) // H
                if k == FORCED_K or held >= MAX_HOLD:
                    out.append(OrderIntent(inst, Action.EXIT, tag="time"))
                continue
            if k not in self.eligible:
                continue
            go, pick, coin = _uniforms(self.seed, inst, block)
            if go >= self.p_entry or self.eligible[int(pick * len(self.eligible))] != k:
                continue
            v = ctx.views[inst]
            if len(v) < ATR_N + 1:
                continue
            hi, lo, cl = v.highs(ATR_N + 1), v.lows(ATR_N + 1), v.closes(ATR_N + 1)
            tr = [
                max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
                for i in range(1, ATR_N + 1)
            ]
            atr = Decimal(repr(float(sum(tr)) / ATR_N))
            if atr <= 0:
                continue
            ref = Decimal(repr(float(v.last_close)))
            dist = STOP_ATR * atr
            if coin < 0.5:
                side, stop, tgt = "LONG", ref - dist, ref + self.target_atr * atr
                act = Action.ENTER_LONG
            else:
                side, stop, tgt = "SHORT", ref + dist, ref - self.target_atr * atr
                act = Action.ENTER_SHORT
            if stop <= 0 or tgt <= 0:
                continue
            self.plans[(inst, t)] = (side, dist, stop, tgt)
            self._entered[inst] = t
            out.append(OrderIntent(inst, act, stop, tgt, tag="random"))
        return out


# --- data (loaded once per worker) -------------------------------------------------------

_DATA: dict[str, Any] = {}


def _data() -> dict[str, Any]:
    if _DATA:
        return _DATA
    profile = build_profile("PAPER")
    store = ParquetStore(profile.parquet_root)
    members: list[str] = json.loads((ROOT / "docs/universe_m4.json").read_text("utf-8"))["members"]
    end = FROZEN.holdout_start_ms
    series = {i: load_research_bars(store, i, "1h", 0, end) for i in members}
    funding = {i: load_research_funding(store, i, 0, end) for i in members}
    specs = _load_specs(ROOT / "docs/instrument_specs.json")
    raw = json.loads((ROOT / "docs/instrument_specs.raw.json").read_text("utf-8"))
    rawi = {r["instId"]: r for r in raw["instruments"]["data"]}
    facts = {
        i: GateFacts(
            specs[i].tick_size,
            specs[i].lot_size_base,
            specs[i].min_size_base,
            Decimal(rawi[i]["maxMktSz"]) * Decimal(rawi[i]["ctVal"]),
            specs[i].mmr,
        )
        for i in members
    }
    labels = {}
    for i in members:
        d = load_research_bars(store, i, "1d", 0, end)
        cols = (d.open, d.high, d.low, d.close, d.volume_base)
        regimes = classify(Bars.of(*(list(map(float, c)) for c in cols)))
        labels[i] = [DailyLabel(int(t), str(r)) for t, r in zip(d.ts_open_ms, regimes, strict=True)]
    spec_sha = hashlib.sha256((ROOT / "docs/instrument_specs.json").read_bytes()).hexdigest()
    _DATA.update(
        members=members,
        series=series,
        funding=funding,
        specs={i: specs[i] for i in members},
        facts=facts,
        labels=labels,
        spec_sha=spec_sha,
        end=end,
    )
    return _DATA


def _config(name: str) -> Any:
    cfg = costs.research_config(costs.FEES_MEASURED_PERP)
    return replace(cfg, slippage=LB_SLIPPAGE) if name == "LB" else cfg


# --- one run -----------------------------------------------------------------------------


def run_one(task: tuple[str, str, str, str, int]) -> dict[str, Any]:
    cand, target, cfg_name, mode, seed = task
    d = _data()
    eligible, p = CANDIDATES[cand]
    cfg = _config(cfg_name)
    g = ResearchRiskGate(
        initial_equity=cfg.initial_equity,
        start_ms=START_MS,
        facts=d["facts"],
        labels=d["labels"],
        spec_sha=d["spec_sha"],
        mode=GateMode(mode),
    )
    strat = RandomBracket(seed, eligible, p, Decimal(target))
    eng = BacktestEngine(
        series=d["series"],
        specs=d["specs"],
        funding=d["funding"],
        config=cfg,
        sizer=RESEARCH_SIZING.sizer(),
        trade_start_ms=START_MS,
        end_ms=d["end"],
        risk_gate=g,
    )
    res = eng.run(strat)
    rs, exits, ambiguous = [], Counter(), []
    for tr in res.trades:
        side, dist, stop, tgt = strat.plans[(tr.inst_id, tr.entry_ts_ms)]
        rs.append(float(tr.net_pnl / (tr.max_qty * dist)))
        reason = tr.exit_reason
        kind = (
            "stop"
            if reason.startswith("stop")
            else "target"
            if reason == "take_profit"
            else "time"
            if reason == "time"
            else "other"
        )
        exits[kind] += 1
        if reason == "stop":
            s = d["series"][tr.inst_id]
            i = s.ts_open_ms.index(tr.exit_ts_ms)
            hit = s.high[i] > tgt if side == "LONG" else s.low[i] < tgt
            if hit:
                ambiguous.append((tr.inst_id, tr.exit_ts_ms, side, str(stop), str(tgt)))
    rep = g.report()
    refusals = Counter(
        r.reason.split(":")[0] if not r.reason.startswith("risk:") else r.reason
        for r in res.rejections
    )
    return {
        "task": {
            "candidate": cand,
            "target_atr": target,
            "config": cfg_name,
            "mode": mode,
            "seed": seed,
        },
        "trades": len(rs),
        "mean_r": statistics.fmean(rs) if rs else math.nan,
        "exits": dict(exits),
        "ambiguous": ambiguous,
        "funding_total": str(sum((t.funding for t in res.trades), Decimal(0))),
        "engine_refusals": dict(refusals),
        "gate": {
            "mode": rep.mode,
            "policy": rep.policy_sha256,
            "not_applicable": dict(rep.not_applicable),
            "evaluated": rep.evaluated,
            "approved": rep.approved,
            "failures": dict(rep.failures),
            "refusals": dict(rep.refusals),
            "halts": dict(rep.halts),
            "engagements": len(rep.engagements),
            "no_label_by_year": {str(k): v for k, v in rep.no_label_by_year.items()},
        },
        "final_equity": str(res.equity_curve[-1].equity) if res.equity_curve else None,
    }


# --- 5m diagnostic (report-only) ---------------------------------------------------------


def five_minute_check(ambiguous: list[tuple[str, int, str, str, str]]) -> dict[str, int]:
    profile = build_profile("PAPER")
    store = ParquetStore(profile.parquet_root)
    out: Counter[str] = Counter()
    cache: dict[str, BarSeries] = {}
    for inst, ts, side, stop_s, tgt_s in ambiguous:
        if inst not in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            out["no_5m_data"] += 1
            continue
        if inst not in cache:
            cache[inst] = load_research_bars(store, inst, "5m", 0, FROZEN.holdout_start_ms)
        s, stop, tgt = cache[inst], Decimal(stop_s), Decimal(tgt_s)
        lo_i = s.ts_open_ms.index(ts) if ts in s.ts_open_ms else None
        if lo_i is None:
            out["missing_5m"] += 1
            continue
        verdict = "neither"
        for j in range(lo_i, min(lo_i + 12, len(s.ts_open_ms))):
            if s.ts_open_ms[j] >= ts + H:
                break
            st = s.low[j] <= stop if side == "LONG" else s.high[j] >= stop
            tg = s.high[j] > tgt if side == "LONG" else s.low[j] < tgt
            if st and tg:
                verdict = "both_in_one_5m_bar"
                break
            if st or tg:
                verdict = "stop_first" if st else "target_first"
                break
        out[verdict] += 1
    return dict(out)


# --- main --------------------------------------------------------------------------------


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in runs:
        t = r["task"]
        groups.setdefault((t["candidate"], t["target_atr"], t["config"], t["mode"]), []).append(r)
    out = {}
    for key, rs in sorted(groups.items()):
        means = [r["mean_r"] for r in rs]
        n = len(means)
        mu = statistics.fmean(means)
        se = statistics.stdev(means) / math.sqrt(n) if n > 1 else math.nan
        exits = Counter()
        for r in rs:
            exits.update(r["exits"])
        tot = sum(exits.values())
        ev = sum(r["gate"]["evaluated"] for r in rs)
        rc13 = sum(v for r in rs for k, v in r["gate"]["refusals"].items() if "RC-13" in k)
        out["|".join(key)] = {
            "seeds": n,
            "mean_r": mu,
            "se": se,
            "required_edge_r": -mu,
            "trades_per_seed": statistics.fmean(r["trades"] for r in rs),
            "exit_mix": {k: v / tot for k, v in exits.items()} if tot else {},
            "ambiguous_share_of_stops": (
                sum(len(r["ambiguous"]) for r in rs) / exits["stop"] if exits["stop"] else 0.0
            ),
            "rc13_refusal_rate": rc13 / ev if ev else 0.0,
            "gate_evaluated": ev,
            "gate_approved": sum(r["gate"]["approved"] for r in rs),
        }
    return out


def main() -> int:
    actual = hashlib.sha256(SPEC.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != PINNED:
        print(f"REFUSED: {SPEC.name} changed after pinning ({actual})")
        return 2
    profile = build_profile("PAPER")
    ensure_dirs(profile)
    n0 = TrialLog(profile).stats().n_trials
    tasks = [
        (c, str(t), "LB", GateMode.OBSERVE_HALTS.value, s)
        for c in CANDIDATES
        for t in TARGETS
        for s in DECISION_SEEDS
    ]
    tasks += [
        (c, str(t), cfg, mode.value, s)
        for c in CANDIDATES
        for t in TARGETS
        for cfg, mode in (("LB", GateMode.ENFORCE), ("SV2", GateMode.OBSERVE_HALTS))
        for s in REPORT_SEEDS
    ]
    with Pool(4) as pool:
        runs = pool.map(run_one, tasks, chunksize=1)
    if TrialLog(profile).stats().n_trials != n0:
        print("REFUSED: the trial log changed during a signal-free baseline")
        return 2
    summary = summarise(runs)
    decision_amb = [
        a
        for r in runs
        if r["task"]["config"] == "LB" and r["task"]["mode"] == GateMode.OBSERVE_HALTS.value
        for a in r["ambiguous"]
    ]
    five = five_minute_check(decision_amb)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "baseline_runs.json").write_text(json.dumps(runs, indent=1, default=str), "utf-8")
    lines = [f"Track 1 lower-bound baseline; spec {actual[:12]}; trials unchanged at {n0}", ""]
    verdicts: dict[str, list[bool]] = {c: [] for c in CANDIDATES}
    for key, s in summary.items():
        cand, tgt, cfg, mode = key.split("|")
        decides = cfg == "LB" and mode == GateMode.OBSERVE_HALTS.value
        fails = s["required_edge_r"] > CUTOFF_R
        if decides:
            verdicts[cand].append(fails)
        lines.append(
            f"{cand:<22} tgt {tgt} ATR {cfg:<3} {mode:<13} seeds {s['seeds']:>2}  "
            f"mean R {s['mean_r']:+.4f} +/- {s['se']:.4f}  "
            f"required edge {s['required_edge_r']:.4f}R"
            + (f"  {'FAILS' if fails else 'passes'} the 0.15R rule" if decides else "  (report)")
        )
        mix = ", ".join(f"{k} {v:.0%}" for k, v in sorted(s["exit_mix"].items()))
        lines.append(
            f"{'':<24}trades/seed {s['trades_per_seed']:.0f}; exits {mix}; ambiguous "
            f"{s['ambiguous_share_of_stops']:.1%} of stops; RC-13 refusals "
            f"{s['rc13_refusal_rate']:.1%} of {s['gate_evaluated']} evaluated"
        )
    lines.append("")
    for cand, v in verdicts.items():
        disc = len(v) == len(TARGETS) and all(v)
        lines.append(
            f"VERDICT {cand}: "
            + ("DISCARDED (both brackets need > 0.15R)" if disc else "NOT STOPPED by the baseline")
        )
    lines.append(f"5m diagnostic (ambiguous stop exits, decision runs): {five}")
    text = "\n".join(lines)
    (OUT / "baseline_summary.txt").write_text(text + "\n", "utf-8")
    print(text)
    AuditChain(profile.audit_log).append(
        "c2_baseline",
        {
            "spec_sha256": actual,
            "summary": summary,
            "five_minute": five,
            "verdicts": {c: all(v) and len(v) == len(TARGETS) for c, v in verdicts.items()},
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
