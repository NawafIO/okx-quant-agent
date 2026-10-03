"""Validate the regime classifier against the PRE-REGISTERED answer keys (roadmap M3).

    python scripts/validate_regime.py                 # BTC key
    python scripts/validate_regime.py --open-sealed   # ETH key, only after BTC is scored
    python scripts/validate_regime.py --risk SOL      # CRISIS/HIGH_VOL-only keys (SOL, XRP)

Order fixed by the Chief Advisor (M3 checkpoint 1): thresholds committed (d50abc4), then
these keys committed, then this first run. Each key file is verified against the SHA-256
pinned here, so an answer key edited after the fact is refused. Every validation - and the
opening of the sealed key - is recorded on the PAPER audit chain. A threshold revision is
a new trial; the revised set must then pass the sealed key, and that result binds.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from okxq.analysis.regime import FROZEN_REGIME, Regime, classify
from okxq.analysis.ta import Bars
from okxq.audit.chain import AuditChain, read_chain
from okxq.backtest.gates import FROZEN
from okxq.backtest.holdout import load_research_bars
from okxq.data.store import ParquetStore
from okxq.env.profiles import build_profile, ensure_dirs

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    "BTC": (
        ROOT / "docs/regime/key_btc.json",
        "903fcf739caec3847e5725ad7aeb5127f05786d3ea0d73fb7b484e5db6cbd1d5",
    ),
    "ETH": (
        ROOT / "docs/regime/key_eth_SEALED.json",
        "ce67696ee0ec87887b841d7bfd1da0e8891ec06c66f3bce998a5b4c54108f0dc",
    ),
    # Risk-only keys (Chief Advisor ruling after the BTC FAIL): CRISIS/HIGH_VOL transfer only.
    "SOL": (
        ROOT / "docs/regime/key_sol_risk.json",
        "98a80eb80b9747d2c1c1648e045933e96850327d67ae8e983c332537cd58a047",
    ),
    "XRP": (
        ROOT / "docs/regime/key_xrp_risk.json",
        "e660c8632c3b5fc1f1bbb449ccc44e68e18bbc73ea2ff76d3809e79afc883b28",
    ),
}


def d(s: str) -> date:
    return date.fromisoformat(s)


def days(a: str, b: str) -> list[date]:
    start, end = d(a), d(b)
    return [start + timedelta(n) for n in range((end - start).days + 1)]


def evaluate(key: dict[str, Any], by_day: dict[date, Regime]) -> tuple[bool, list[str]]:
    lines, ok = [], True
    for w in key["trend_windows"]:
        span = days(w["from"], w["to"])
        c = Counter(by_day.get(x, Regime.UNDEFINED) for x in span)
        n = len(span)
        exp = Regime(w["label"])
        opp = Regime.TREND_DOWN if exp is Regime.TREND_UP else Regime.TREND_UP
        share, opp_share = c[exp] / n, c[opp] / n
        hv = (c[Regime.HIGH_VOL] + c[Regime.CRISIS]) / n
        good = share >= 0.60 and opp_share <= 0.10 and hv <= 0.25
        ok &= good
        lines.append(
            f"[{'PASS' if good else 'FAIL'}] {exp} {w['from']}..{w['to']}: "
            f"{share:.0%} expected (>=60%), {opp_share:.0%} opposite (<=10%), "
            f"{hv:.0%} HIGH_VOL+CRISIS (<=25%); {dict(c)}"
        )
    for e in key["crisis_events"]:
        ev = d(e["date"])
        hits = [
            x
            for x in sorted(by_day)
            if by_day[x] is Regime.CRISIS and ev - timedelta(7) <= x <= ev + timedelta(2)
        ]
        inside = [x for x in hits if ev <= x <= ev + timedelta(2)]
        early = [x for x in hits if x < ev]
        good = bool(inside)
        ok &= good
        lines.append(
            f"[{'PASS' if good else 'FAIL'}] CRISIS {e['date']} ({e['why']}): fired on "
            f"{[x.isoformat() for x in inside] or 'none in [event, +2d]'}"
            + (f"; EARLIER (finding): {[x.isoformat() for x in early]}" if early else "")
        )
    span = key["universe_crisis_rate_span"]
    allc = [by_day[x] for x in by_day if d(span["from"]) <= x <= d(span["to"])]
    rate = sum(1 for x in allc if x is Regime.CRISIS) / len(allc)
    ok &= rate <= 0.03
    lines.append(
        f"[{'PASS' if rate <= 0.03 else 'FAIL'}] CRISIS rate {rate:.2%} of {len(allc)} days (<=3%)"
    )
    for w in key["calm_windows"]:
        n_c = sum(1 for x in days(w["from"], w["to"]) if by_day.get(x) is Regime.CRISIS)
        ok &= n_c == 0
        lines.append(
            f"[{'PASS' if n_c == 0 else 'FAIL'}] calm {w['from']}..{w['to']}: "
            f"CRISIS on {n_c} days (0)"
        )
    for w in key["range_windows"]:
        span_d = days(w["from"], w["to"])
        c = Counter(by_day.get(x, Regime.UNDEFINED) for x in span_d)
        share = c[Regime.RANGE] / len(span_d)
        ok &= share >= 0.60
        lines.append(
            f"[{'PASS' if share >= 0.60 else 'FAIL'}] RANGE {w['from']}..{w['to']}: "
            f"{share:.0%} (>=60%); {dict(c)}"
        )
    return ok, lines


def evaluate_risk(key: dict[str, Any], by_day: dict[date, Regime]) -> tuple[bool, list[str]]:
    """CRISIS/HIGH_VOL only. Kept apart from :func:`evaluate` so the sealed ETH key is scored
    by exactly the code that existed when it was sealed."""
    lines, ok = [], True
    first = min(by_day)
    avail = [e for e in key["crisis_events"] if d(e["date"]) >= first + timedelta(5)]
    hit: list[str] = []
    miss: list[str] = []
    for e in avail:
        ev = d(e["date"])
        fired = [
            x
            for x in days(e["date"], (ev + timedelta(2)).isoformat())
            if by_day.get(x) is Regime.CRISIS
        ]
        (hit if fired else miss).append(e["date"])
    na = len(key["crisis_events"]) - len(avail)
    share = len(hit) / len(avail) if avail else 0.0
    good = bool(avail) and share >= 0.80
    ok &= good
    lines.append(
        f"[{'PASS' if good else 'FAIL'}] CRISIS on BTC-rule events: {len(hit)}/{len(avail)} "
        f"({share:.0%}, >=80%); {na} N/A before data; missed: {miss or 'none'}"
    )
    span = key["universe_crisis_rate_span"]
    allc = [by_day[x] for x in by_day if d(span["from"]) <= x <= d(span["to"])]
    rate = sum(1 for x in allc if x is Regime.CRISIS) / len(allc)
    ok &= rate <= 0.03
    lines.append(
        f"[{'PASS' if rate <= 0.03 else 'FAIL'}] CRISIS rate {rate:.2%} of {len(allc)} days (<=3%)"
    )
    for w in key["calm_windows"]:
        n_c = sum(1 for x in days(w["from"], w["to"]) if by_day.get(x) is Regime.CRISIS)
        ok &= n_c == 0
        lines.append(
            f"[{'PASS' if n_c == 0 else 'FAIL'}] calm {w['from']}..{w['to']}: "
            f"CRISIS on {n_c} days (0)"
        )
    for w in key["high_vol_windows"]:
        c = Counter(by_day.get(x, Regime.UNDEFINED) for x in days(w["from"], w["to"]))
        rest = sum(c.values()) - c[Regime.CRISIS]
        hv = c[Regime.HIGH_VOL] / rest if rest else 0.0
        good = rest > 0 and hv >= 0.50
        ok &= good
        lines.append(
            f"[{'PASS' if good else 'FAIL'}] HIGH_VOL {w['from']}..{w['to']}: {hv:.0%} of "
            f"{rest} non-CRISIS days (>=50%); {dict(c)}"
        )
    return ok, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-sealed", action="store_true")
    ap.add_argument("--risk", choices=["SOL", "XRP"])
    a = ap.parse_args()
    which = a.risk or ("ETH" if a.open_sealed else "BTC")
    path, pinned = KEYS[which]
    # LF-normalised: a Windows (CRLF) checkout must not read as an edited answer key.
    actual = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual != pinned:
        print(f"REFUSED: {path.name} changed after pre-registration ({actual})")
        return 2
    profile = build_profile("PAPER")
    ensure_dirs(profile)
    chain = AuditChain(profile.audit_log)
    if which == "ETH":
        prior = [
            r
            for r in read_chain(profile.audit_log)
            if r.kind == "regime_validation" and r.payload.get("instrument") == "BTC-USDT-SWAP"
        ]
        if not prior:
            print("REFUSED: the sealed key opens only after the BTC key has been scored")
            return 2
        chain.append("regime_sealed_key_opened", {"key_sha256": actual, "after": prior[-1].hash})
    key = json.loads(path.read_text(encoding="utf-8"))
    store = ParquetStore(profile.parquet_root)
    s = load_research_bars(store, key["instrument"], "1d", 0, FROZEN.holdout_start_ms)
    bars = Bars.of(
        *(list(map(float, col)) for col in (s.open, s.high, s.low, s.close, s.volume_base))
    )
    labels = classify(bars)
    by_day = {
        datetime.fromtimestamp(t / 1000, tz=UTC).date(): lab
        for t, lab in zip(s.ts_open_ms, labels, strict=True)
    }
    risk = key.get("kind") == "risk_only"
    ok, lines = evaluate_risk(key, by_day) if risk else evaluate(key, by_day)
    print(
        f"{key['instrument']} 1d, {len(labels)} days {min(by_day)}..{max(by_day)}; "
        f"thresholds {FROZEN_REGIME.sha256()[:12]}; key {actual[:12]}"
    )
    print("overall label mix:", dict(Counter(labels)))
    for ln in lines:
        print("  " + ln)
    print("REGIME VALIDATION:", "PASS" if ok else "FAIL")
    chain.append(
        "regime_validation",
        {
            "instrument": key["instrument"],
            "kind": "risk_only" if risk else "full",
            "key_sha256": actual,
            "thresholds_sha256": FROZEN_REGIME.sha256(),
            "passed": ok,
            "lines": lines,
        },
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
