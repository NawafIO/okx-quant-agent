# M4 research log: first research cycle

**Outcome: NO CANDIDATE SURVIVED.** All three testable candidates were DISCARDED by the frozen gates. The fourth, funding_carry, was not evaluable. Under the roadmap that is the result, and it is reported as such. There is no holdout run, because the holdout is read only for a surviving candidate.

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
| trend_breakout | DISCARD | G-3 INVALID (98), G-4 0.93, G-6 0/12, G-7, G-8 0, G-9 | 329 | −7.6k | 0.93 | +4.3k (PF 1.17) | `m4_runs/trend_breakout_run1.txt` |
| keltner_reversion | DISCARD | G-1 63%, G-2 0.40, G-4 0.53, G-6 0/16, G-7, G-8 0, G-9 | 969 | −87.8k | 0.53 | −24.2k | `m4_runs/keltner_reversion_run1.txt` |
| vol_compression_breakout | DISCARD | G-1 32%, G-2 0.81, G-4 0.76, G-6 0/16, G-7, G-8 0, G-9 | 1,666 | −73.2k | 0.76 | −29.4k | `m4_runs/vol_compression_breakout_run1.txt` |
| funding_carry | NOT EVALUABLE | — | — | — | — | — | M4_DESIGN §4 |

## Cost attribution (OOS, all folds)

| candidate | gross ex-slippage | slippage | fees | funding (all bound) | of which SAND shorts | net |
|---|---|---|---|---|---|---|
| trend_breakout | +29.0k | −1.8k | −2.6k | −32.2k | −26.1k | −7.6k |
| keltner_reversion | **−36.7k** | −19.3k | −24.2k | −7.5k | −6.3k | −87.8k |
| vol_compression_breakout | +14.2k | −25.2k | −31.2k | −31.0k | −25.3k | −73.2k |

## What the results say

1. **No edge survived out of sample.** Only trend_breakout looked good in-sample (PF 2.27, max drawdown 5.3%, so G-1 and G-2 pass). Out of sample it fell to PF 0.93. This is risk R-1 exactly.
2. **keltner_reversion loses before costs.** Fading 1h Keltner breaches was worse than random: the breaches tended to continue.
3. **trend_breakout's out-of-sample loss is mostly the funding bound on SAND shorts.**
   - [Likely] Its gross-positive longs are survivorship beta, not breakout edge: longs are gross-positive on 7 of 8 instruments, and shorts are gross-negative on the majors.
   - G-6 (0/12) and G-8 (0 at N=383) fail regardless of the bound.
4. **vol_compression_breakout has a small pre-slippage gross (+14.2k) and modelled costs bury it.** [Likely] Measured perpetual fees (0.02/0.05%) and zero SAND bound funding would still leave it about −32k. The modelled slippage alone (−25.2k) exceeds the gross. (M2 finding M-2: the Abdi-Ranaldo spread runs about 500× the live spread on BTC. Slippage is probably overstated, but not by enough to matter here.)
5. **The funding bound is the largest single cost** in two of three candidates, and one −0.01 print on SAND decides most of it. The real fix is a measured funding archive, which is the owner's decision.

## Recorded against the design (my errors, verdict-neutral)

- **Trade-count estimates were wrong.**
  - keltner_reversion: 323 full-span trades against a pre-registered 1,500–15,000, an overestimate of about 5–15×.
  - vol_compression_breakout: 442 against 500–6,000.
  - trend_breakout: 98 against 120–400. Its G-3 INVALID was pre-registered as possible.
- **`bb_k` in vol_compression_breakout was a dead parameter.** The bandwidth percentile rank is invariant to the band multiple, so ±20% left PF identical (G-6 rows `bb_k x0.80` and `x1.20`). A declared parameter that cannot matter is a design error. It did not affect the verdict, because G-6 failed on every other parameter.
- **Research start was corrected at N=0** (crash log kept).

## Owner evidence (M4_DESIGN §0)

- **U-2 and U-3 accepted.**
- **U-1 fees received** (0.02/0.05%, read 2026-10-03). MEASURED provenance is pending the source.
- Adopting the measured fees in research is a Phase-2 cost-model change. It applies to every candidate and runs as new trials, so it cannot rescue a candidate retroactively.
