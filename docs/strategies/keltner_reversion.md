# keltner_reversion: rationale (written BEFORE any backtest; grid 1 of at most 2)

**Hypothesis.** At short horizons, liquidity-driven overshoots in a non-trending market partly revert: forced flows and stop cascades push price beyond fair value, and liquidity providers are paid to absorb them. Fading 1h closes outside the Keltner bands back toward the EMA midline harvests that premium, but only when trend strength is low.

**The ADX filter is a locally implemented regime gate.** ADX below `adx_max` roughly means "range". It does NOT consume `okxq.analysis.regime`, so the M3 tripwire does not fire. But the M3 design fact applies to it: ADX lags turns by 2–4 weeks, so the filter will admit fades into a young trend.

**Rules (1h bars).**
- **Entry long:** close < EMA(`kc_n`) − `kc_k`·ATR(`atr_n`), and ADX(`adx_n`) < `adx_max`. Short is the mirror image.
- **Stop:** `stop_atr`·ATR beyond the entry close.
- **Target:** the EMA midline at entry.
- **Time stop:** `time_stop_bars` closed bars.

**Parameters**

| param | default | in grid |
|---|---|---|
| kc_n | 20 | — |
| atr_n | 10 | — |
| kc_k | 2.0 | 2.0, 2.5 |
| adx_n | 14 | — |
| adx_max | 20.0 | 20, 25 |
| stop_atr | 1.5 | — |
| time_stop_bars | 24 | 12, 24 |
| warmup_bars | 500 | — |

The grid has 8 configurations.

**Expected trade count (pre-registered).** Several band breaches per instrument per week, so **1,500–15,000 closed trades**, with a point estimate of about 5,000. G-3 is not the binding gate here.

**Cost expectation: this is the candidate most exposed to costs, and my prior is that it FAILS.** The target is roughly 2–2.5 hourly ATRs away, which is about 1–2%. A round trip costs about 20 bps in fees plus spread, impact and stop overshoot, roughly 15–25% of the gross move. On top of that, the stop is only 1.5 ATR away, so the strategy needs a high hit rate. G-9 (2× costs) is the expected point of failure.

**What falsifies it.** Any gate failing. Most likely: G-2 (profit factor), G-9 (cost stress), or G-4 (out-of-sample profit factor).
