# intraday_momentum: rationale (DRAFT, not yet pre-registered; grid 1 of at most 2)

**Status:** submitted for advisor review on 2026-10-03. No code and no trial run yet. The grid and the expected numbers become binding only when the advisor signs off and this file's hash is pinned.

## Hypothesis and what the literature actually says

- **Equities.** Gao, Han, Li & Zhou (2018), "Market intraday momentum", *Journal of Financial Economics* 129(2), 394–414 ([doi:10.1016/j.jfineco.2018.05.009](https://doi.org/10.1016/j.jfineco.2018.05.009)): the S&P 500 ETF's **first half-hour return, measured from the previous day's close**, predicts the **last half-hour return**. The effect is stronger on high-volatility days, high-volume days, in recessions, and on major-news days.
  - Proposed mechanisms: infrequent portfolio rebalancing, and late-informed traders.
- **Bitcoin.** Shen, Urquhart & Wang (2022), "Bitcoin intraday time-series momentum", *Financial Review* 57(2), 319–344 ([doi:10.1111/fire.12290](https://onlinelibrary.wiley.com/doi/10.1111/fire.12290)): with data from Bitfinex, Bitstamp, CEX.IO, Coinbase and Kraken, the first half-hour predicts the last half-hour, in sample and out of sample.
  - Bitcoin has no open or close, so the paper **defines its trading day by volume** ("trading volume as a proxy for the market trading time").
  - Predictability is strongest in the highest-volume or highest-volatility sessions.
- **Crypto, both directions.** Wen, Bouri, Xu & Zhao (2022), *North American Journal of Economics and Finance* 62, 101733: intraday predictability in crypto includes **both momentum and reversal**.

**What I could not verify.** The full texts were unreachable from this host (egress-blocked). I have only the abstracts and summaries, so I do **not** know Shen et al.'s exact UTC session boundaries, their coefficients, or whether their trading gains survive costs.

**The window used here is not the published window.**
- Our "day" opens at **00:00 UTC**, which is the daily-candle boundary and a funding settlement.
- Our "late session" is **22:00–23:00 UTC**, chosen so that the position is flat across every settlement (V-13, V-15).
- So this is an **adaptation of the hypothesis to the venue's calendar, not a replication**. If it fails, that says nothing against the papers.

## Rules (1h bars, as for every candidate, because slip-v2 is calibrated on 1h)

- **Signal.**
  - `r_first` is the log return of the 1h bar opening at 00:00 UTC that day, (close − open) / open.
  - `σ` is the standard deviation of the 24 hourly log returns closed by 01:00 that day.
  - The signal is active if `|r_first| > k·σ`; with k = 0, any nonzero `r_first` qualifies.
- **Entry.** At the decision point T = 22:00 UTC (the 21:00 bar has closed), enter in the direction of `r_first`. The entry fills at the open of the 22:00 bar.
- **Exit.** At T = 23:00 the strategy exits, and the exit fills at the open of the 23:00 bar. The hold is one hour, with no settlement inside it.
- **Stop.** `stop_atr` × ATR_1h(24) from the entry reference.
- **Target.** `target_atr` × ATR_1h(24). This is a contract formality, because the Signal contract requires a take-profit. I expect it to bind rarely in a 1h hold; if G-6 shows it binding, that is recorded the way trend_breakout's was.
- **Priority** (if the advisor accepts CYCLE2_DESIGN §2): `|r_first| / σ`. Under the cluster cap only the 3 strongest signals enter.

## Parameters

Every constant is declared; G-6 perturbs each by ±20%.

| param | default | in grid |
|---|---|---|
| entry_lead_h | 2 (entry 2 h before the 00:00 settlement) | — |
| sigma_n | 24 | — |
| atr_n | 24 | — |
| k | 0.5 | 0, 0.5 |
| stop_atr | 2.0 | 1.0, 2.0 |
| target_atr | 4.0 | — |
| warmup_bars | 48 | — |

The grid is 4 configurations.

**G-6 cannot meaningfully test the clock (needs a ruling).** `entry_lead_h` = 2 rounds back to 2 at ±20%, so G-6 will report it as unchanged. A clock time cannot be perturbed by a percentage without leaving the day or crossing a settlement. Two options:
- accept that the window is a pre-registered structural choice that G-6 does not test; or
- add a pre-registered **window-shift check**: run the selected configuration with the window moved to 21–22 and 20–21 UTC, count those runs as trials, and require PF ≥ the G-2 floor in both.

I recommend the second. An edge that exists in one clock hour and no neighbouring one is more likely noise than mechanism.

## Expected trades (pre-registered once signed)

- At k = 0, all 8 instruments signal on every day they have data. RC-07 caps the book at 3 positions and RC-14 caps entries at 3 per hour, so there are **at most 3 entries per day**.
- Full span 2020-07-01..2025-09-30 is about 1,917 days, so about 5,700 trades at k = 0. At k = 0.5, perhaps 60–80% of that. Point estimate 4,500, range **2,500–5,800**.
- RC-10 and RC-11 halts can only lower this.
- Pooled OOS from 2021-06: about 80% of the total.
- G-3 will not bind. That, in turn, means G-8's Sharpe estimate is well-powered, so a weak edge cannot hide behind a small sample.

## Cost: this is where I expect it to die

- Round trip: 2 × 0.05% taker = **10 bp in fees**, plus 2 × half-spread (cycle-2 calibrated), plus impact.
- [Guessing, to be measured by the signal-free feasibility check in CYCLE2_DESIGN §5] if the mean absolute 22:00–23:00 return is about 40–60 bp on these instruments, the break-even hit rate is about 0.58–0.63.
- [Likely] No published intraday-momentum effect I know of implies a directional hit rate near 0.6 at a one-hour horizon.
- The feasibility check exists so that, if the arithmetic says this, it can be confirmed **before** spending about 225 trials.

## What falsifies it

- Failing the feasibility check, which means no trials are run.
- Failing any of G-1..G-9. The most likely failures:
  - G-2 and G-4: PF below the floor once fees are paid;
  - G-9: doubled fees;
  - G-7: profit concentrated in the 2020–2021 high-volume regime.

## Survivorship

V-14 applies. The effect is a short-horizon directional one, so survivorship bias is plausibly smaller than for multi-week momentum. [Guessing] It is still not zero, because delisted coins had the thinnest books.
