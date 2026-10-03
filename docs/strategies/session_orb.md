# session_orb: rationale (DRAFT, not pinned; Track 1, grid 1 of at most 2)

**Status:** Revision 1, after the advisor's review of f78d051 (PROCEED WITH CHANGES). Not yet pinned. Shared rules are in CYCLE2_DESIGN §9. The only precedent is an SSRN equity paper with no out-of-sample test, no spread or slippage, and a net-zero replication. That is **weak** support, not a precedent.

## Hypothesis and evidence

- Zarattini & Aziz (2023), "Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading Strategy vs. Benchmark in the US Stock Market" ([SSRN 4416622](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622)), studies an opening-range breakout on QQQ:
  - take the direction of the first 5-minute candle;
  - stop on the other side of that candle;
  - target 10× the risk, or the session close.
- Its edge came from the **shape** of the payoff, not the hit rate. That is the asymmetry the owner wants, and **this adaptation cannot reproduce it** within ≤ 5 h holds (see Stop and §9.2a).
- **Caveats, from the search summaries:**
  - US equities, with a real session open; not crypto.
  - Commission charged, but **no spread and no slippage**.
  - **No out-of-sample period.**
  - A replication posted on 2026-09-25 ([mql5 blog](https://www.mql5.com/en/blogs/post/776235)) reports the gross result reproduced and the **net result at zero** on five indices.
- I found **no crypto ORB study**. The full texts were not read (egress-blocked).

**Mechanism, adapted.**
- A crypto perpetual has no open. A funding settlement is the closest thing to one: positioning resets around it, and the 8 h blocks are the venue's own calendar.
- [Guessing] Order flow released after a settlement is directional for a few hours.
- This is an **adaptation**. Its failure says nothing against the equity result.

## Rules (1h bars, within each 8 h block)

- **Opening range (OR).** The high and low of the first `or_bars` bars of the block. With `or_bars` = 2, that is 00–02, 08–10 and 16–18 UTC.
- **Entry.**
  - The first bar after the OR that **closes** above the OR high gives a long at the next open; the first that closes below the OR low gives a short.
  - At most one trade per instrument per block.
  - No entry unless ≥ 2 bars remain before the forced exit (§9.1).
- **Stop.** 1.0 ATR from the entry reference (§9.1).
  - **This drops the cited design's own mechanism**: a tight stop at the range's far side against a 10R target. Two reasons:
    - on 1h bars the far side can fall under M5's SZ-2 floor;
    - a 10R target is unreachable within ≤ 5 h (§9.2a: even 3 ATR is reached about 1–5% of the time).
  - This is therefore an ORB **entry** with a volatility bracket. It is not the paper's payoff.
- **Target.** `target_atr` × ATR, as a maker order.
- **Exit.** Stop, target, or the forced exit before the settlement, whichever comes first. The hold is ≤ 5 bars by construction.
- **Priority.** (close − OR boundary) / ATR.

## Parameters

| param | default | grid |
|---|---|---|
| or_bars | 2 | 1, 2 |
| target_atr | 2.0 | 1.5, 2.0 |
| stop_atr | 1.0 | — |
| atr_n | 24 | — |
| min_room_bars | 2 | — |
| warmup_bars | 96 | — |

**G-6 (advisor ruling).** `or_bars` rounds back to itself at ±20%, so it is unperturbable.
- Pre-registered check: re-run each fold's selected parameters fixed, out of sample, with `or_bars` + 1 (no −1). G-6 thresholds apply: PF ≥ 1.2 and MaxDD ≤ 0.15. Count the runs as trials.
- `min_room_bars` and `max_hold_bars` are structural owner constraints, recorded as untested.

**Estimated exit mix** (signal-free, driftless model, §9.2a; holds ≤ 5 h, mostly 3–5):
- target 1.5 ATR: about 43% stop / 24% target / 33% time;
- target 2.0 ATR: about 43% / 12% / 45%.

The baseline measures the real mix.

## Expected trades

- 8 instruments × 3 blocks × about 1,917 days ≈ 46,000 instrument-blocks.
- [Guessing] A breakout occurs in about half of them, so about 23,000 signals.
- RC-07 caps **open positions** at 3, not entries per block: a slot freed by a stop or target can take a later breakout in the same block. RC-14 caps entries at 3 per hour.
- Breakouts cluster at the bars after the OR, so in practice the cap is about 3–5 per block, roughly 17,000–29,000 at most.
- RC-13 (§9.4) could cut this by an order of magnitude.
- **Range: 1,500–15,000. No point estimate**, because RC-13's effect depends on the outcome.

## Cost and falsification

- Per round trip: entry taker 5 bp; exit 2 bp maker at the target or 5 bp taker plus overshoot at a stop or time exit.
- The required edge per trade comes from the §9.3 baseline.
- **Falsified by:**
  - the §9.3 lower-bound baseline (> 0.15 R needed);
  - the `or_bars` + 1 check;
  - any of G-1..G-9. Likeliest: G-2 and G-4 (PF after costs), and G-9 (2× fees).
