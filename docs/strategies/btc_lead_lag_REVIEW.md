# btc_lead_lag: design review. Verdict: **do not build at 1h** (advisor: STOP, agreed, review of 7ae7250)

The owner asked for this review "in parallel" with intraday_momentum. Nothing here has been built or run.

## Proposed rule (from the cycle-2 candidate list)

- **Trigger.** On a 1h bar T, BTC's return exceeds `z·σ_BTC`.
- **Lag condition.** An alt's same-bar return is below `β_i · r_BTC`, where β_i is a trailing beta.
- **Trade.** Trade the alt in BTC's direction at T+1's open, hold `h` hours, and exit before the next settlement.
- **Grid.** z ∈ {2, 3} × h ∈ {2, 4}.

## Why I recommend not building it on 1h bars

1. **The documented lag is minutes, not hours, and only for small caps.**
   - Recent high-frequency evidence on BTC→altcoin price transmission ([Asia-Pacific Financial Markets, 2026](https://link.springer.com/article/10.1007/s10690-026-09589-z)) reports that **large and medium caps react to BTC simultaneously**, with strong unlagged correlation. Only small caps show lagged responses, and those last **several minutes**.
   - Our universe is ETH, SOL, XRP, DOGE, NEAR, UNI and SAND. [Guessing] All of them fall in the paper's large or mid tier; SAND and NEAR may not (advisor). The 1h verdict does not depend on it: even small-cap lags are minutes long, gone by T+1's open.
   - The lag the strategy needs has, on that evidence, closed inside the bar we would detect it in.
   - The Granger-causality evidence for BTC→alts is mostly at daily frequency (the same search turned up several studies). A daily lag is a different strategy, with a different cost profile.
   - [Certain] The full texts were egress-blocked, so this rests on abstracts.
2. **The engine fills at T+1's open, which is the earliest point an hourly strategy can act.** Any catch-up that happens within the remainder of hour T, which is exactly where the papers place it, is invisible to us and untradeable.
3. **The cross-instrument change is not free.**
   - The strategy layer gives a strategy only its own instrument's view (M4 design). Seeing BTC's closed bars means a new `MarketContext.reference` view, a T-1 property test proving the reference view never contains a bar closing after T, and a new code version.
   - That is reviewed infrastructure built for one candidate whose premise the literature already undercuts.
4. **Funding interaction.** A hold of h = 4 h from an arbitrary hour crosses a settlement about half the time. "Exit before settlement" therefore truncates many holds to fewer than h hours, so h stops meaning what the grid says. With V-15 (SAND moved to 4 h settlements), the clean windows shrink further.
5. **The cluster cap.** A BTC shock triggers on every lagging alt at once, but RC-07 admits 3 positions. The priority rule then decides most of the outcome.

## What would make it worth building

- **1m bars and a minute-scale cost model.**
  - Neither exists: slip-v2 is calibrated on 1h only (M2), and the cycle-2 spread collection polls every 60 s, which is too coarse for queue-level costs.
  - [Likely] At minute horizons the 10 bp taker round trip is larger than any plausible catch-up on a mid-cap, so the strategy would need maker execution. The engine does not model that (no queue position, no fill probability).
- **A daily variant** (BTC's day-T return predicting alts' day T+1). This is a different hypothesis, with daily-frequency literature behind it. It is a candidate in its own right that would need its own rationale. It still needs the cross-instrument view.

## If the owner overrides this

Build only:
- the `reference` view and its T-1 property test, as one reviewed change;
- then the strategy exactly as the rule above states, with grid 4 (z × h);
- with `h` replaced by "hold until min(T + h, next settlement − 1 h)", and the truncation share reported per fold.

Pre-registered expectation: PF below the G-2 floor, because the lag is gone by T+1's open.
