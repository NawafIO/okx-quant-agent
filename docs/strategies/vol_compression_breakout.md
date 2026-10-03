# vol_compression_breakout: rationale (written BEFORE any backtest; grid 1 of at most 2)

**Hypothesis.** Volatility clusters and mean-reverts. Periods of unusually low realised range tend to end in range expansion, and the first break of a tight consolidation carries directional information, because accumulated orders and stops sit just outside the range. Trading the break of a compression range after the Bollinger bandwidth sits in the bottom of its own trailing distribution aims to catch the expansion with a tight, well-defined stop.

**Rules (1h bars).**
- **Squeeze:** the PREVIOUS bar's Bollinger bandwidth, (upper − lower)/mid with `bb_n` bars and `bb_k` deviations, has a percentile rank at or below `squeeze_q` among the preceding `rank_bars` bandwidths (90 days).
- **Entry long:** close > highest high of the previous `range_bars` bars. Short is the mirror image.
- **Stop:** the opposite side of that range.
- **Target:** `target_r` × the initial risk.
- **Time stop:** `time_stop_bars`.

**Parameters**

| param | default | in grid |
|---|---|---|
| bb_n | 20 | — |
| bb_k | 2.0 | — |
| rank_bars | 2160 | — |
| squeeze_q | 0.10 | 0.05, 0.10 |
| range_bars | 24 | 12, 24 |
| target_r | 2.0 | 1.5, 3.0 |
| time_stop_bars | 48 | — |
| warmup_bars | 3000 | — |

The grid has 8 configurations.

**Expected trade count (pre-registered).** About one qualifying break per instrument per week or two, so **500–6,000 closed trades**, with a point estimate of about 2,000.

**Cost expectation.** Compression ranges are tight by construction, so the initial risk is small. Fees plus spread, impact and overshoot may be a large fraction of a 1R move, and stop overshoot on a tight stop is proportionally expensive. G-9 is the likely failure point, and false breakouts are the likely edge failure.

**What falsifies it.** Any gate failing. Most likely: G-2/G-4 from false breakouts, G-9 from cost stress, or G-6 from sensitivity to `squeeze_q`.
