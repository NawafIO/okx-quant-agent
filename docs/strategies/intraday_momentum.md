# intraday_momentum: rationale (Revision 1, ready to pin; grid 1 of at most 2)

**Status:**
- Revised after the advisor's review of 7ae7250 (B-1 and B-2 fixed, over-claims corrected).
- Becomes binding when its SHA-256 is pinned (CYCLE2_DESIGN §4, step 3). After pinning, any edit is a new rationale.
- No code exists and no trial has run.

## Hypothesis and what the literature actually says

- **Equities.** Gao, Han, Li & Zhou (2018), "Market intraday momentum", *Journal of Financial Economics* 129(2), 394–414 ([doi:10.1016/j.jfineco.2018.05.009](https://doi.org/10.1016/j.jfineco.2018.05.009)): the S&P 500 ETF's **first half-hour return, measured from the previous day's close**, predicts the **last half-hour return**. The effect is stronger on high-volatility days, high-volume days, in recessions, and on major-news days.
  - Proposed mechanisms: infrequent portfolio rebalancing, and late-informed traders.
- **Bitcoin.** Shen, Urquhart & Wang (2022), "Bitcoin intraday time-series momentum", *Financial Review* 57(2), 319–344 ([doi:10.1111/fire.12290](https://onlinelibrary.wiley.com/doi/10.1111/fire.12290)): with data from Bitfinex, Bitstamp, CEX.IO, Coinbase and Kraken, the first half-hour predicts the last half-hour, in sample and out of sample.
  - Bitcoin has no open or close, so the paper **defines its trading day by volume**.
  - Predictability is strongest in the highest-volume or highest-volatility sessions.
- **Crypto, both directions.** Wen, Bouri, Xu & Zhao (2022), *North American Journal of Economics and Finance* 62, 101733: intraday predictability in crypto includes **both momentum and reversal**.

**What I could not verify.** The full texts were egress-blocked; I have abstracts and summaries only. I do **not** know Shen et al.'s session boundaries, their coefficients, or whether their gains survive costs.

**This is an adaptation, not a replication.**
- Our day opens at **00:00 UTC**, the daily-candle boundary and a funding settlement.
- Our late session is **22:00–23:00 UTC**, chosen to be flat across every settlement (V-13, V-15), **not** for volume. The signal-free volume profile by UTC hour is reported alongside the result (CYCLE2_DESIGN §5).
- A failure says nothing against the papers.

## Rules (1h bars)

- **Signal.**
  - `r_first = ln(close / open)` of the 1h bar opening at 00:00 UTC that day.
  - `σ` is the sample standard deviation (ddof = 1) of the `sigma_n` close-to-close hourly log returns `ln(close_t / close_t−1)` whose bar t closed by 01:00 that day; the last is the 00:00 bar.
  - The signal is active if `|r_first| > k·σ`. With k = 0, any nonzero `r_first` qualifies.
- **Entry.** At decision time T = 00:00 − `entry_lead_h` hours (22:00 at the default), enter in the direction of `r_first`. The entry fills at the open of the bar starting at T.
- **Exit.** One bar later: decided at T + 1 h, filled at that bar's open (23:00 at the default). The hold is one hour, with no settlement inside it.
- **Stop.** `stop_atr` × ATR_1h(`atr_n`) from the entry reference. **ATR is the simple mean of the last `atr_n` true ranges**, not Wilder's smoothing: finite memory, so it is exact after `atr_n` + 1 bars under every perturbation (advisor B-2).
- **Target.** `target_atr` × ATR. This is a contract formality, because the Signal contract requires a take-profit. If G-6 shows it binding, that is recorded.
- **Priority** (declared here, so pinned with the code version; CYCLE2_DESIGN §2): `|r_first| / σ`. Ties break by the hash shuffle. Under the cluster cap, the 3 strongest signals enter.

## Parameters

Every constant is declared; G-6 perturbs each by ±20%.

| param | default | in grid |
|---|---|---|
| entry_lead_h | 2 | — (tested by the window-shift check below, not by G-6) |
| sigma_n | 24 | — |
| atr_n | 24 | — |
| k | 0.5 | 0, 0.5 |
| stop_atr | 2.0 | 1.0, 2.0 |
| target_atr | 4.0 | — |
| warmup_bars | 96 (4 days) | — |

The grid is 4 configurations.

**Warmup (fixes B-2, second attempt).** It must cover every parameter at +20% while the warmup itself is at −20%.
- The first fix (Wilder ATR, warmup 480) was rejected. Wilder's oldest-bar weight (1 − 1/n)^m is still about 1e-6 at n = 29, m = 384, so stops differ in their low digits, and the convergence test compares them exactly.
- Hence the simple-mean ATR. Requirements:
  - σ at the 22:00 decision with sigma_n × 1.2 = 29 needs the bars from 19:00 two days back (29 returns, so 30 closes ending at the 00:00 bar) through 21:00 today: 51 bars;
  - ATR with atr_n × 1.2 = 29 needs 30 bars.
- The warmup at × 0.8 is 77 bars, which covers both.
- **Pre-registered test.** `test_decisions_at_warmup_equal_decisions_at_twice_warmup` runs as a **passing** test (not an xfail) for this strategy at the defaults, **and** at atr_n × 1.2 and sigma_n × 1.2 with warmup × 0.8.

**Clock window: the pre-registered window-shift check (fixes B-1, advisor ruling).**
- `entry_lead_h` = 2 rounds back to 2 at ±20%, so G-6 reports it as unperturbable and does not test it. The window is the hypothesis's main choice, so it is tested separately.
- **Check.** Each fold's selected parameters are held fixed, and the OOS windows are re-run with `entry_lead_h` = 3 (21:00–22:00) and = 4 (20:00–21:00).
- No +1 h shift: the exit would fill on the 00:00 settlement.
- **Pass:** both shifted runs meet the G-6 thresholds, PF ≥ 1.2 and MaxDD ≤ 0.15. Failing either is a FAIL of the candidate.
- The runs are counted as trials.
- If a fold selects k = 0, G-6 also cannot perturb k, because 0 × 1.2 = 0. That is recorded, not worked around: k = 0 is "no threshold", and there is nothing to perturb.

## Expected trades (pre-registered on pinning)

- The ceiling is 3 entries per day, from RC-07 and RC-14. The full span, 2020-07-01..2025-09-30, is about 1,917 days, so at most about 5,700.
- Below that ceiling:
  - k = 0.5 filters some days;
  - RC-13's 3-loss cooldown will be frequent, because the 3 daily positions mostly point the same way and lose together; [Likely] this alone could remove a third or more of the days;
  - an RC-11 halt ends the run.
- **Range 1,000–5,700. I am not offering a point estimate**, because RC-13's effect depends on the outcome being tested.
- **G-8 caveat.** The strategy is flat 23 of 24 hours, so its per-bar returns are mostly zeros, with extreme kurtosis. [Likely] The deflated Sharpe will be pushed down whatever the edge. A G-8 failure here is partly structural.

## Feasibility computation (pre-registered; runs only after this file's hash is pinned)

- **Data.** Research door only: 1h bars of the 8 members, 2020-07-01 up to the holdout start.
- **Days.** A UTC day counts for an instrument when its 00:00 bar, the 24 bars before it, and its 22:00 and 23:00 bars are all present (no gaps).
- **Late move.** `|ln(open_23:00 / open_22:00)|`: the fill-to-fill move of the trade, ignoring direction.
- **E|r|, two ways, per instrument:**
  - E_u: mean over all counted days;
  - E_k: mean over the days with `|r_first| > 0.5·σ` (σ as in the rules, sigma_n = 24). This uses the size of the 00:00 move, never its sign.
- **Break-even hit rate.** `p* = ½ + c / (2·E)`.
  - Fees-only bound: c = 0.0010 (2 × 0.05% taker).
  - Full check: c = 0.0010 + 2 × the calibrated half-spread, once the cycle-2 config is pinned.
- **Rule.** Let M_u and M_k be the medians of p* across the 8 instruments for each E. If **min(M_u, M_k) > 0.55**, the candidate is DISCARDED on feasibility and no trials run.
- **Recorded on the PAPER audit chain:** E_u, E_k, p* per instrument, M_u, M_k, the verdict, and this file's hash.
- **Also reported, signal-free:** the share of daily base volume by UTC hour: the mean over every instrument-day with all 24 bars present and nonzero total volume.

## Cost: where I expect it to die

- Round trip: 2 × 0.05% taker = **10 bp in fees**, plus 2 × half-spread, plus impact.
- [Guessing, measured by the feasibility check] if E|r| for 22:00–23:00 is about 40–60 bp, the break-even hit rate is about 0.58–0.63.
- [Likely] No published intraday-momentum effect implies more than about 0.54 at this horizon; the advisor's cutoff is 0.55.
- The fees-only bound runs right after pinning, and can stop the candidate before any trial.

## What falsifies it

- Failing the feasibility check, which means no trials are run.
- Failing the window-shift check.
- Failing any of G-1..G-9. The most likely failures:
  - G-2 and G-4: PF below the floor after fees;
  - G-9: doubled fees;
  - G-8: structural, as above;
  - G-7: profit concentrated in the 2020–2021 high-volume regime.

## Survivorship

V-14 applies. [Guessing] The bias is plausibly smaller than for multi-week momentum, but not zero, because delisted coins had the thinnest books.
