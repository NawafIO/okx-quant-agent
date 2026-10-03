# impulse_continuation: rationale (DRAFT, not pinned; Track 1, grid 1 of at most 2)

**Status:** Revision 1, after the advisor's review of f78d051 (PROCEED WITH CHANGES). Not yet pinned. **The direction is the open question:** the crypto literature reports **both** intraday momentum and intraday reversal. This candidate pre-registers **continuation**. If reversal is the truth it fails, and flipping the sign afterwards is a new hypothesis and a new grid, never a fix.

## Hypothesis and evidence

- A large hourly move on abnormal volume reflects informed or forced flow (liquidations, stop cascades, news). Continuation can come from:
  - order splitting by large participants;
  - liquidation cascades on leveraged perpetuals.
- Evidence, from abstracts only:
  - Wen, Bouri, Xu & Zhao (2022, NAJEF 62, 101733): intraday crypto predictability includes **both momentum and reversal**, with price jumps and liquidity mattering.
  - Shen, Urquhart & Wang (2022, Financial Review): BTC intraday momentum is strongest in high-volume, high-volatility sessions. **This is session momentum, not post-impulse continuation**, so it is only indirect support (advisor).
- [Guessing] Continuation after volume-confirmed impulses is plausible, and reversal after impulses on low volume (exhaustion) is equally plausible. The volume filter is the hypothesis's bet on which is which.

## Rules (1h bars)

- **Impulse bar t.**
  - `|ln(close_t / close_t−1)| > z·σ`, where σ is the sample std of the 24 hourly close-to-close log returns **before** bar t.
  - **and** `volume_t > v × median(volume over the 24 bars before t)`.
- **Entry.** In the impulse's direction at the next open, inside an eligible window (≥ 2 bars before the forced exit). One trade per instrument per block.
- **Stop and target.** 1.0 ATR stop; `target_atr` × ATR maker target (§9.1). The hold is ≤ 6 bars.
- **Priority.** |r_t| / σ.

## Parameters

| param | default | grid |
|---|---|---|
| z | 2.5 | 2.0, 3.0 |
| target_atr | 2.0 | 1.5, 2.0 |
| v | 2.0 | — |
| sigma_n | 24 | — |
| vol_n | 24 | — |
| stop_atr | 1.0 | — |
| atr_n | 24 | — |
| warmup_bars | 96 | — |

**Warmup.** σ, volume and ATR windows at × 1.2 need 30 bars, and 96 × 0.8 = 77, which covers them.

**Estimated exit mix** (signal-free, driftless model, §9.2a; holds up to 6 h):
- target 1.5 ATR: about 45–49% stop / 26–29% target / 22–29% time;
- target 2.0 ATR: about 45–49% / 14–17% / 33–41%.

An entry right after an impulse bar sits in elevated volatility. The SMA-ATR bracket lags it, so [Likely] stops are hit **more** often than this model says. The baseline will show it.

## Expected trades

- [Guessing] A 2σ hourly move with 2× median volume occurs on about 2–4% of bars. That is 8 instruments × about 46,000 hours ≈ 7,000–15,000 impulses, about half in eligible windows.
- Impulses cluster in BTC-driven market-wide moves, so the cluster cap (3) and RC-14 bind exactly when signals are densest.
- **Range: 1,000–7,000** before RC-13.

## Falsification

- The §9.3 baseline, or G-1..G-9.
- **Report-only:** the per-instrument split, and whether losses concentrate in the bars right after the impulse (the reversal signature). This is recorded for the record and never acted on within this grid.
