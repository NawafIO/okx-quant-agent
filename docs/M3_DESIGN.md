# M3 design record — analysis engines

Status: **CLOSED** by the Chief Advisor closing audit, with the corrections below applied. The regime classifier FAILED validation and its labels are DESCRIPTIVE. The threshold revision is UNSPENT and the ETH key is SEALED.
Scope: `src/okxq/analysis/` (ta.py, quant.py, regime.py) and the validation scripts. `src/okxq/backtest/` is untouched (a closing-audit condition of M2).

## 1. What was built

| Engine | File | Checks |
|---|---|---|
| TA | `analysis/ta.py` | `REGISTRY`: 26 indicators. Each is a pure function of closed bars and declares its `min_warmup`. Every registry entry gets the T-1 property test (`f(x[:n])[-1] == f(x)[n-1]`). An indicator that fails is removed, not patched. OBV passes T-1, because a cumulative sum is consistent with its own prefix. It FAILED the bounded-buffer / `min_warmup` property: its level depends on where the series starts, so a finite live buffer never agrees with the full history. It was removed. TA-Lib unstable periods are pinned to 0 and asserted at import. |
| Quant | `analysis/quant.py` | Every function is `KIND = "batch"`: it describes a caller-supplied sample and is not a per-bar causal feature. Correlations use pairwise-complete timestamps and never fill gaps. Average-linkage clustering is implemented in numpy because scipy is not installed. |
| Regime | `analysis/regime.py` | Thresholds are frozen with a pin (`260beabd…`) **before** any real-data run. Undefined inputs give `UNDEFINED`. `align_to_hourly` applies a daily label only from that day's close onward. `classify_with` exists for sensitivity analysis only, and the isolation guard keeps it away from research code. |

## 2. Regime validation — the pre-registration sequence and its results

All of these run through the research door (holdout sealed), and each is recorded on the PAPER audit chain.

| Step | Commit | Result | Audit |
|---|---|---|---|
| Thresholds frozen | d50abc4 | — | — |
| BTC key + sealed ETH key committed | fff4d38 | — | — |
| **BTC run** | 12befe9 (`docs/regime/run1_btc_FAIL.txt`) | **FAIL, 4/13 criteria** | 8347317015b5 |
| Chief Advisor ruling **B** | — | FAIL is binding; revision not spent | — |
| SOL and XRP risk-only keys committed | 30fcbd6 | — | — |
| **SOL run** | 9354296 (`docs/regime/run1_risk_sol_xrp_FAIL.txt`) | **FAIL**: events 7/9 (78% < 80%); CRISIS rate 4.03% > 3% | d4a939cd210b |
| **XRP run** | 9354296 | **FAIL**: CRISIS rate 3.43% > 3% (events 10/12 PASS) | 19c05263d511 |

**Binding consequences** (ruling B plus its pre-stated rule for the SOL/XRP check):

- **All labels are DESCRIPTIVE.** That covers TREND_UP, TREND_DOWN and RANGE, and also CRISIS and HIGH_VOL, because the out-of-sample check failed. No component of §8.3 has been shown to work as a trading signal. Its authority over the LLM (M8) is unaffected: the LLM still cannot change a label and may only reduce risk.
- **A strategy may consume a regime label only as a declared parameter set.** The ADX, EMA and crisis thresholds are then part of that strategy's parameters, subject to G-6 perturbation, and the strategy's rationale must state the label's lag. Consuming a label as a feature counts as gating on it.
- **The single threshold revision is UNSPENT.** The ETH key stays **SEALED**, so a future method-level revision still has a clean test.
- **The answer keys stay as committed.** Two BTC windows were drawn with hindsight: 2022-10-03..11-08 and 2022-12-21..31 are genuinely sideways, and there the classifier was right and the key wrong. Redrawing the key after the run would launder that finding, so it is not redrawn.

### Design facts for M4

1. **Trend labels lag.** They confirm 2–4 weeks after a turn and decay about 4 weeks after a trend ends. In July 2023, ADX took from 07-01 to 07-29 to fall from 34 to 20. The 2021-07-21 window begins at the low and carries 8 TREND_DOWN days. A strategy that enters on a fresh TREND label enters late by construction.
2. **The TREND/RANGE boundary is fragile.** A ±20% move in `adx_trend` relabels 12–15% of days on BTC, SOL and XRP (§4). The BTC failures pull the ADX threshold in opposite directions, so no threshold-only revision could pass without being fitted to the key.
3. **[Likely, not demonstrated] The absolute CRISIS thresholds do not transfer across volatility levels.** −10% in one day and −20% over five days fire on 0.95% of BTC days, but 3.4% of XRP days and 4.0% of SOL days. The relative HIGH_VOL percentile passed its positive window on both SOL (88%) and XRP (92%). The ≤3% rate criterion was anchored on BTC's 0.95% base rate, and the failures are marginal (3.4%, 4.0%). So this is a plausible reading, not a demonstrated mechanism. The hypothesis for the unspent revision is a crisis cut scaled to each instrument's own volatility. **It is written down here, before any consumer exists, so that it cannot be "discovered" later.**

**Revision tripwire** (closing audit): the sealed ETH key may be spent only when an M4 strategy declares a regime dependency in its written rationale BEFORE that strategy's first backtest. The revision is then made and scored against the ETH key before the strategy runs. No consumer, no revision.
4. **One-day CRISIS blips on BTC:** 2021-09-07, 2022-05-09 and 2022-08-19, plus 2021-05-12, which came before the 05-19 event. All four are BTC days at or below −10%, so they are real crash days. The original BTC event list was written from memory and was incomplete. Later keys generate their events by a rule applied to BTC, never to the instrument under test, because that would be tautological with the CRISIS rule itself.
5. **SOL's misses were measured decoupling.** On 2021-05-12 SOL closed −2.2% (5-day +0.9%) while BTC fell −12.5%. On 2021-09-07 SOL closed +5.7% (5-day +35.2%, its 2021 rally) while BTC fell −11.0%. The nearest SOL move was 2021-05-13: 5-day −10.2%, still short of the −20% cut.

## 3. R-8 clustering demonstration (finding M-5)

The expectations were pre-registered in `docs/r8_preregistration.json` (commit 2f16d5d, pinned by SHA-256). Output is in `docs/r8_run1.txt`, audit record c2f75acedf0b. **All four expectations were met.**

- **E1:** median alt–BTC correlation 0.609, against a threshold of 0.60. This passes **by a margin of 0.009**, so read it as "about 0.6", not as a robust pass.
- **E2:** the eight full-history names sit in one cluster at cut 0.5. In fact **13 of the 15** instruments do. Only PUMP (78 overlapping days) and XAU stay outside.
- **E3:** XAU–BTC correlation is −0.109 over 174 days.
- **E4:** in BTC's worst month (2025-02, −17.7%), the mean pairwise correlation of the full-history set was 0.751, against 0.631 over the whole window. Diversification shrinks exactly when it is needed.
- **Report-only:**
  - Rolling 90-day correlation with BTC is unstable. UNI fell to 0.113 (2024-03), SUI to 0.137 and WLD to 0.162, while every alt except HYPE and PUMP peaks above 0.78.
  - Within the worst month, PEPE, SUI, WLD and XRP join the BTC cluster; HYPE, TRUMP and PUMP stand alone.
- **Survivors only.** Coins delisted before 2026, such as LUNA and FTT, are absent, so real crash co-movement is likely understated.

**For RC-07 (M5):** treat the crypto universe as **one** exposure cluster under stress. Measure cluster membership on stress-month correlation, not full-window averages, because a low rolling correlation in calm periods does not survive a crash.

## 4. Threshold sensitivity (finding M-3)

Output: `scripts/regime_sensitivity.py`. Each threshold is moved by −20% and +20%, one at a time, and the table gives the share of research-window days relabelled.

| threshold | BTC | SOL | XRP |
|---|---|---|---|
| crisis_1d_return | 0.6 / 0.2% | 2.4 / 0.8% | 1.3 / 0.7% |
| crisis_5d_return | 0.6 / 0.1% | 2.3 / 1.0% | 1.9 / 0.7% |
| vol_window | 2.2 / 1.7% | 1.6 / 1.1% | 2.5 / 1.9% |
| vol_pctl_high * | 17.1 / 3.6% | 12.6 / 1.8% | 13.2 / 3.1% |
| vol_rank_lookback | 3.0 / 1.4% | 2.5 / 0.5% | 0.8 / 0.5% |
| vol_rank_min_history | 1.7 / 1.7% | 2.0 / 2.1% | 1.7 / 1.7% |
| ema_fast | 1.7 / 1.2% | 1.1 / 0.9% | 1.0 / 1.2% |
| ema_slow | 3.7 / 4.1% | 2.0 / 1.8% | 3.3 / 2.7% |
| slope_days | 1.4 / 1.0% | 1.2 / 0.4% | 1.0 / 1.0% |
| adx_period | 8.1 / 9.1% | 9.2 / 7.6% | 9.1 / 7.2% |
| **adx_trend** | **13.0 / 14.4%** | **12.6 / 14.9%** | **14.1 / 11.8%** |

\* The ±20% perturbation is mis-scaled for a percentile. Taking 0.95 to 0.76 widens the tail from 5% of days to 24%, which changes what the label means rather than nudging it. The +20% side is capped at 0.999. Read this row as an upper bound. It was **not** re-run with a different perturbation after the results were seen.

## 5. Deviations from the architecture (finding M-6)

| § | Specified | Done | Why |
|---|---|---|---|
| 8.3 | Regime labels authoritative | Labels descriptive | Pre-registered validation failed (§2) |
| 8.3 | Hurst, ATR percentile and correlation-collapse crisis inputs | Not used | Daily Hurst is too noisy to gate on. The ATR percentile duplicates the realised-volatility percentile. Correlation collapse needs a causal universe-wide feature, and quant.py is batch-only. |
| 9.2 | Swing highs/lows, pivots | Excluded | They need right-side bars, which is look-ahead by definition |
| 9.2 | CVD | Excluded | No trade-side data |
| 9.2 | Volume profile | Deferred | No consumer yet |
| 9.2 | OBV | Removed | Failed the bounded-buffer / `min_warmup` property (it passes T-1); see §1 |
| 9.1 | Rolling or causal quant features | Batch only (`KIND`) | A causal form needs its own T-1 test before any strategy may consume it |

## 6. T-2: own implementations against independent references

| Indicator | Reference | Tolerance met |
|---|---|---|
| Rolling VWAP | Naive per-window loop (`test_rolling_vwap_matches_a_naive_loop`) | rel 1e-9 |
| Keltner | Pure-Python EMA (SMA-seeded) and Wilder ATR (mean of TR[1..n], then smoothed), written without TA-Lib. 3 seeds, 300 bars. | rel 1e-9 |
| Supertrend | Pure-Python final-band formulation with its own ATR, written without TA-Lib. 5 seeds, at least 3 direction flips each. | line rel 1e-9; direction exact |

Before the closing audit, Keltner was checked only as an identity over TA-Lib's own EMA and ATR, and Supertrend only by its direction of flip. Neither was an independent reference. Both were added at closing. A mutation check showed the new tests catch a wrong ATR period in Keltner and a perturbed low in Supertrend. **Caveat:** the Supertrend reference and implementation share one author and one convention (start in an up-trend; flip on a strict cross), so a misconception common to both would not be caught. Agreement with a third-party chart is not tested.

TA-Lib wrappers are checked against hand-computed fixtures (SMA, EMA, RSI).

## 7. What M3 does not settle

- Whether a volatility-scaled crisis rule (§2, fact 3) would pass. That would spend the one revision, and the sealed ETH key would decide.
- Whether any regime label adds value to a strategy. That is an M4 question, tested as declared parameters under G-6, never assumed.
