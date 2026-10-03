# M4 research log: first research cycle

**Outcome of cycle 1: no candidate passed the frozen protocol under conservative costs.** All three testable candidates were DISCARDED; funding_carry was not evaluable; the holdout was not read, because it is read only for a survivor. **M4 acceptance is met; the Phase-2 exit is NOT met** (no passing strategy), so M6 stays blocked (closing audit, §5).

This is not proof of "no edge". Every bias in this setup runs AGAINST the strategies: spot-rate fees, a slippage model that probably overstates spreads (M2 finding M-2), and the adverse funding bound.

Common basis for every run:

| Setting | Value |
|---|---|
| Gates | `9066dca9…` |
| Sizing | `e32046ed…` (0.5% risk, 3x) |
| Cost config | `546f4fbc…` (fees 0.08/0.10% `UNMEASURED_ASSUMPTION`, slip-v2, funding-bound-v1) |
| Span | 2020-07-01..2025-09-30, 17 walk-forward folds of 12/3/3 months |
| Universe | 8 instruments (`docs/universe_m4.json`) |
| Strategy code version | `8dec2735…` |
| Trial log | N = 0 before M4, **1,302 after** (383 + 17 diagnostic + 451 + 451) |

> SURVIVORSHIP (V-14): the universe contains only instruments still listed in 2026; delisted
> perpetuals (e.g. LUNA, FTT) cannot be obtained from OKX. Results are biased UPWARD by an unknown
> amount, so a pass is weaker evidence than the gates imply. See the per-instrument breakdown and the
> BTC+ETH-only line.

## Results

| candidate | verdict | failing gates | OOS trades | OOS net | OOS PF | BTC+ETH net | output |
|---|---|---|---|---|---|---|---|
| trend_breakout | DISCARD | G-3 INVALID (98), G-4 0.93, G-6 0/12, G-7 INVALID, G-8 0, G-9 | 329 | −7.6k | 0.93 | +4.3k (PF 1.17) | `m4_runs/trend_breakout_run1.txt` |
| keltner_reversion | DISCARD | G-1 63%, G-2 0.40, G-4 0.53, G-6 0/16, G-7 INVALID, G-8 0, G-9 | 969 | −87.8k | 0.53 | −24.2k | `m4_runs/keltner_reversion_run1.txt` |
| vol_compression_breakout | DISCARD | G-1 32%, G-2 0.81, G-4 0.76, G-6 0/16, G-7 INVALID, G-8 0, G-9 | 1,666 | −73.2k | 0.76 | −29.4k | `m4_runs/vol_compression_breakout_run1.txt` |
| funding_carry | NOT EVALUABLE | — | — | — | — | — | M4_DESIGN §4 |

## Cost attribution (OOS, all folds)

"Gross + |slippage|" is **not** a before-cost figure. The modelled stop overshoot (`engine.py`, k·σ beyond the stop) and the stop-first rule when stop and target hit in the same bar both land inside gross P&L, not in the slippage line.

| candidate | gross + \|slippage\| | slippage | fees | funding (all bound) | of which SAND shorts | net |
|---|---|---|---|---|---|---|
| trend_breakout | +29.0k | −1.8k | −2.6k | −32.2k | −26.1k | −7.6k |
| keltner_reversion | **−36.7k** | −19.3k | −24.2k | −7.5k | −6.3k | −87.8k |
| vol_compression_breakout | +14.2k | −25.2k | −31.2k | −31.0k | −25.3k | −73.2k |

## What the results say

1. **Nothing passed out of sample under conservative costs.**
   - Only trend_breakout looked good in-sample: PF 2.27 and max drawdown 5.3%, so G-1 and G-2 pass.
   - Out of sample it fell to PF 0.93. This is risk R-1 exactly.
2. **keltner_reversion: −36.7k gross + |slippage| over 969 trades,** about −38 per trade, or about −11 bps on roughly $33k notional.
   - [Guessing] That is close to the M2 random-entry result on real data (−11.0 ± 0.7 bps before costs, M2_DESIGN), which is mostly the stop drag.
   - **No claim is made that it is "worse than random" or that breaches "continue".** It shows no evidence of edge.
3. **trend_breakout's out-of-sample loss is mostly the funding bound on SAND shorts** (−26.1k).
   - Its gross-positive longs, including BTC (+4.7k) and ETH (+4.6k), which have no survivorship issue, are **long market exposure in a 2020–25 bull sample**, not demonstrated breakout edge.
   - [Likely] G-8 fails regardless (0 at N=383).
   - **Whether G-6 fails without the bound is NOT shown:** every G-6 run carries the SAND bound cost, which alone is drawdown-sized on $100k.
4. **vol_compression_breakout: gross + |slippage| +14.2k, buried by the modelled costs.**
   - [Likely] With measured perp fees (0.02/0.05%) and zero SAND bound funding it would still lose about **−24k to −32k**. Take-profits fill as maker (`engine.py`), so not every fill pays taker.
   - The modelled slippage alone (−25.2k) exceeds the gross. Slippage is probably overstated (M2 finding M-2: Abdi-Ranaldo is about 500× the live BTC spread).
5. **The funding bound is the largest single cost** in two of three candidates, and one −0.01 print on SAND decides most of it. The real fix is a measured funding archive, which is the owner's decision.

## Recorded against the design (my errors, verdict-neutral)

- **Trade-count estimates were wrong.**
  - keltner_reversion: 323 full-span trades against a pre-registered 1,500–15,000, an overestimate of about 5–15×.
  - vol_compression_breakout: 442 against 500–6,000.
  - trend_breakout: 98 against 120–400. Its G-3 INVALID was pre-registered as possible.
- **`bb_k` in vol_compression_breakout was a dead parameter.** The bandwidth percentile rank is invariant to the band multiple, so ±20% left PF identical (G-6 rows `bb_k x0.80` and `x1.20`). A declared parameter that cannot matter is a design error. It did not affect the verdict, because G-6 failed on every other parameter.
- **Research start was corrected at N=0** (crash log kept).

- **Stop drag counted as edge loss** (closing audit). The "gross ex-slippage" column first presented here as before-cost included stop overshoot. Corrected above.
- **Strategy defects found by the closing audit, now tests with STRICT xfails** (`tests/unit/strategy/test_strategy_layer.py`):
  - `bb_k` is dead: the bandwidth percentile rank is invariant to it.
  - trend_breakout's `warmup_bars` is LIVE only because its 20-day Wilder ATR has not converged in 150 days. That breaks M4_DESIGN §2 ("decisions from the buffer equal decisions from full history"). The original bounded-buffer test could not catch it: both copies used the same bounded view.
  - Every other declared parameter changes a decision at ±20%.
  - `target_atr` is BINDING in trend_breakout (PF 0.940–1.031 across ±20%). The rationale presented it as a contract formality, but in practice it is an un-hypothesised profit-taking rule.
- **Trade counts came in low for all three.** The runner now prints fills, closed trades and refused entries by reason, so suppressed entries are visible in future runs. Strategy-side "no signal" decisions are not counted.

## Owner evidence (M4_DESIGN §0)

- **U-1: MEASURED.** Perpetual maker 0.02%, taker 0.05%, read from the owner's OKX account fee & tier page (Regular user) on 2026-10-03; record 1867e2a2c00c. The same page lists spot 0.08% / 0.10%.
  - These are `costs.FEES_MEASURED_PERP`. Cycle 1 ran on the higher spot rates, which is the conservative direction.
- **U-2: accepted on substitute evidence.** `Get-ScheduledTaskInfo` showed result 0 and 0 missed runs; the script's own `-Status` output (stamp age) was not supplied. Record a93fc9ceea8a.
- **U-3: accepted** in the owner's words. Record 27d3491f0e89.
- **No re-run on measured fees now** (closing audit). With fees alone, trend_breakout is about −6.3k, keltner about −75.7k and vol_compression about −57.6k: all stay negative. A re-run would add about 1,285 trials for no decision value.
  - A funding archive is the one change that could flip trend_breakout's G-4. Because that change was prompted by seeing the SAND result, it may run only if justified by the archive data alone, fixed and hashed before any run, applied to all three candidates, with N continuing from 1302.
