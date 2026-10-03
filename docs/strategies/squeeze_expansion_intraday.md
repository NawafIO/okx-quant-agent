# squeeze_expansion_intraday: STOPPED (advisor B-3, review of f78d051); not to be pinned or run

> **STOPPED.** The case for re-testing this family rested on a false premise: that funding dominated M4's loss. In the M4 run, funding was −31.0k of −73.2k; fees were −31.2k and slippage −25.2k; gross before slippage was only +14.2k. Without funding it still loses about −42k. This would be the family's last grid, and that evidence does not justify spending it. See CYCLE2_DESIGN §9.6. The draft below is kept for the record, **including its false sentence**.

**Status:** draft for advisor review. **Family question first:** this is a Bollinger squeeze → band breakout, the **same signal family as M4's vol_compression_breakout** (cycle-1 grid 1, DISCARDED; most of its loss, −25.5k of −31.0k in funding, was SAND short funding). Under the two-grids-per-family rule this would be that family's **second and last grid**. That needs advisor sign-off, and I recommend **holding it** behind session_orb and impulse_continuation.

## Hypothesis

- Volatility clusters and mean-reverts. A period of unusually low realised range tends to end in a range expansion, and the first close outside the band gives the direction.
- Evidence: the volatility-clustering literature, which is solid (GARCH-type persistence in crypto returns is well documented). The *direction* claim is practitioner lore (Bollinger, Carter's "squeeze"). [Certain] I have no peer-reviewed source that a band breakout's direction predicts the expansion's direction in crypto.

**What differs from M4's version, and why that matters.**
- Holds are ≤ 6 h and flat at every settlement, so **funding is zero by construction**. That removes the cost that dominated M4's loss.
- That difference is the whole case for re-testing the family. The signal has no other new evidence behind it.

## Rules (1h bars)

- **Squeeze.** The percentile rank of `bw = σ_20 / SMA_20` (no band multiple, so the rank is defined without `bb_k`; the M4 dead-parameter lesson) over the last `rank_n` bars is ≤ `q`.
- **Trigger.** While squeezed, a 1h close outside `SMA_20 ± bb_k·σ_20` gives a trade in that direction at the next open, if ≥ 2 bars remain before the forced exit. One trade per instrument per block.
- **Stop and target.** 1.0 ATR stop; `target_atr` × ATR maker target (§9.1).
- **Priority.** |close − band| / σ_20.
- `bb_k` is **live** here: it sets the trigger, not the squeeze measure. A pre-registered test checks that each declared parameter changes a decision at ±20%.

## Parameters

| param | default | grid |
|---|---|---|
| q | 0.2 | 0.1, 0.2 |
| target_atr | 3.0 | 2.0, 3.0 |
| bb_n | 20 | — |
| bb_k | 2.0 | — |
| rank_n | 120 | — |
| stop_atr | 1.0 | — |
| atr_n | 24 | — |
| warmup_bars | 240 | — |

**Warmup.** rank_n × 1.2 + bb_n × 1.2 = 144 + 24 = 168 bars, and 240 × 0.8 = 192, which covers it.

## Expected trades and falsification

- [Guessing] At q = 0.2, about 20% of bars are squeezed, and only a minority of squeezes trigger inside an eligible window. Expect 2,000–10,000 signals before the RC caps, and far fewer after RC-13.
- **Falsified by:** the §9.3 baseline, or G-1..G-9.
- **Survivorship (V-14):** breakout-style payoffs are flattered by survivors-only universes.
