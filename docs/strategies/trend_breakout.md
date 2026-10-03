# trend_breakout: rationale (written BEFORE any backtest; grid 1 of at most 2)

**Hypothesis.** Crypto returns show time-series momentum at multi-week horizons. Liu & Tsyvinski (2021) report that crypto returns are predicted by their own past returns, and Moskowitz, Ooi & Pedersen (2012) document time-series momentum across futures. The proposed mechanism is slow diffusion of information and herding among retail-dominated flows. A Donchian breakout is the classic implementation: buy a new N-day high, sell a new N-day low, and exit on an opposite M-day channel.

**Implementation.** The strategy runs on **1h bars**, because the slippage model's lookbacks are counted in bars and were calibrated on 1h bars. It aggregates completed UTC days inside the strategy and decides only on the bar that closes a UTC day, so an entry fills at the next day's first 1h open. Stops and targets are checked against 1h highs and lows. Daily-scale logic, hourly execution.

**Rules.**
- **Entry long:** the day's close is above the highest daily high of the previous `entry_days` days.
- **Entry short:** the day's close is below the lowest daily low of those days.
- **Stop:** `stop_atr` × daily Wilder ATR(`atr_days`) from the close.
- **Target:** `target_atr` × ATR. This is a far target, set only because the Signal contract (§4) requires one.
- **Exit:** at a day close, when the close crosses the opposite `exit_days` channel.

**Parameters.** Every constant is listed here; G-6 perturbs each by ±20%.

| param | default | in grid |
|---|---|---|
| entry_days | 55 | 20, 55 |
| exit_days | 20 | 10, 20 |
| atr_days | 20 | — |
| stop_atr | 2.0 | 2.0, 3.0 |
| target_atr | 10.0 | — |
| warmup_bars | 3600 (150 days) | — |

The grid has 8 configurations (2 × 2 × 2). The timeframe is 1h bars with daily decisions.

**Expected trade count (pre-registered).** About 6 trades per instrument-year. There are about 38 instrument-years under the entry rule, so I expect **120–400 closed trades over the full span**, with a point estimate of 230. Pooled out-of-sample from 2021-06 onward, about 170. G-3 needs 100. INVALID is possible at entry_days = 55, and if it happens it is a result.

**Cost expectation.** A round trip costs about 20 bps in taker fees, plus the modelled spread and impact, plus about 0.2 σ_1h of stop overshoot. The average winner in a trend system is several daily ATRs, so costs should be small relative to the gross move. **If this fails, it fails on edge, not on cost.**

**What falsifies it.** Any of G-1..G-9 failing. The most likely failures:
- G-2/G-4: profit factor below the floor, because 2022–2023 chop produces whipsaw.
- G-7: profit concentrated in the 2020–2021 bull run.
- G-6: fragile to channel length.

**Survivorship.** Momentum is the strategy most flattered by a survivors-only universe. Coins that trended to zero and were delisted would have produced short wins and long losses that we cannot see. The per-instrument breakdown and the BTC+ETH line are therefore essential reading for this candidate.
