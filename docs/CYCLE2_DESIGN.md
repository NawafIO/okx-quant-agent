# Cycle 2 design (DRAFT, for advisor review; no trial runs before sign-off)

Owner decisions of 2026-10-03:
- **D1:** adopt measured perpetual fees, and re-run the M4 baselines.
- **D2:** collect real order-book spreads.
- **D3:** enforce the M5 risk limits in research.
- **Candidates:** intraday_momentum (22:00–23:00 UTC) now; a design review for btc_lead_lag.

Status at drafting: trial log **N = 1,302**. Holdout sealed. PHASE 2. No cycle-2 trial has run.

## 0. Where this draft departs from the owner's instructions, and why

1. **The M4 baselines are not re-run under the new fees alone.** The M4 closing audit already ruled on this (M4_RESEARCH_LOG, "No re-run on measured fees now"). Re-pricing the stored trades with measured fees leaves all three candidates negative: trend_breakout about −6.3k, keltner about −75.7k, vol_compression about −57.6k. A re-run would add about 1,285 trials and could not change a verdict.
   - D2 changes slippage as well, and an honest re-run has to wait for it. Otherwise the baselines get re-run twice, about 2,600 trials, roughly doubling N and deflating G-8 for every future candidate.
   - §4 replaces the blanket re-run with a pre-registered re-pricing screen.
2. **btc_lead_lag is not recommended on 1h bars** (see `docs/strategies/btc_lead_lag_REVIEW.md`). The literature found in this pass puts the BTC→alt lag at minutes, and only for small caps; it does not support a 1h lag. The review is prepared as asked, and its verdict is "do not build at 1h".
3. **intraday_momentum faces a fee hurdle that I expect it to fail** (see its rationale, §Cost). The draft adds a signal-free feasibility check (§5) that can stop it before any trial is spent.

## 1. D1: fees

- `costs.FEES_MEASURED_PERP` (maker 0.02%, taker 0.05%; MEASURED, audit record 1867e2a2c00c) becomes the fee schedule of the **cycle-2 cost config**. That config is a new `cost_config_sha`, and every cycle-2 trial records it.
- The engine charges taker on market entries, stop exits and signal exits, and maker only on a resting take-profit (engine.py `_fee`). So the cost a strategy actually faces is about **0.10% per round trip plus slippage**, not 0.04%.
- `FEES_USER_SPOT_SCHEDULE` stays in the code because cycle-1 records reference it. It is no longer used for new trials.

## 2. D3: the M5 risk limits inside the backtest

**Mechanism.**
- An optional `RiskGate` is consulted in `BacktestEngine._accept` **after** sizing and before the entry is queued. It receives a read-only portfolio view:
  - equity and the equity at 00:00 UTC;
  - the high-water mark;
  - open positions (instrument, side, qty, fill price, stop);
  - pending entries;
  - closed-trade outcomes;
  - entry timestamps.
- It returns pass, or a rejection reason (`risk:RC-07` and so on), recorded as an engine `Rejection` like every other refusal.
- `risk_gate=None` is the default, so the golden digest and every cycle-1 result are unchanged; a test pins that.
- Where the M5 input types allow, the gate calls the **same pure functions** in `okxq.risk.checks`. Where a check needs a live-only input, it is listed as N/A below; nothing is faked as "passed".
- The pinned `RiskPolicy` sha (b4e030fc) becomes part of the research config and is recorded on every Trial and GateReport.

| check | in research | how |
|---|---|---|
| RC-01 kill switch | yes, as the halt latch of RC-10/11 | see halts below |
| RC-02 env / PHASE | N/A | no environment in a backtest |
| RC-03 data freshness | already enforced | engine rejects `no_bar_closed_at_decision_time` |
| RC-04 symbol tradable | N/A | historical venue state unknown (V-14 survivorship) |
| RC-05 per-trade risk 0.5% | yes | ResearchSizing already equals it; the gate re-checks |
| RC-06 heat 3% | **yes, new** | open risk = Σ qty·\|fill − stop\| over open positions and pending entries |
| RC-07 cluster 1.5% | **yes, new** | the whole universe is cluster CRYPTO: **at most 3 full-size positions** |
| RC-08 max 5 positions | yes | never binding while RC-07 binds at 3 |
| RC-09 one per symbol | already enforced | `already_positioned`, `entry_already_pending` |
| RC-10 day loss 2% | **yes, new** | halt |
| RC-11 drawdown 10% | **yes, new** | halt |
| RC-12a..f leverage, margin, liquidation | partly already | 3× and margin in the engine; RC-12f (liquidation ≥ 1.5× stop distance) added |
| RC-13 3-loss cooldown 24h | **yes, new** | from closed-trade outcomes |
| RC-14 ≤ 3 entries per hour | **yes, new** | on 1h bars: at most 3 entries per decision bar |
| RC-15 API error breaker | N/A | no venue in a backtest |
| RC-16 CRISIS / sentiment | CRISIS yes, sentiment N/A | regime from daily bars closed before T; M5 Q2 ruled the block stays, with the label's FAIL recorded |

**Halts.** Live, RC-10 and RC-11 latch the kill switch until a **human** disarms it. A backtest has no human, so this needs a proxy, and the proxy is a modelling choice:
- **RC-10:** no new entries until the next 00:00 UTC. This assumes a disarm within a day, which is optimistic.
- **RC-11:** no new entries for the rest of the test window. A fold that halts stays halted, and its later trades are simply absent.
- Open positions keep their stops and exits. Live, M5 only blocks entries; flattening is M6, interface only.

**Simultaneous entries (needs a ruling).** With the cluster cap at 3, entries that fire on the same bar compete for capacity. The engine visits instruments in sorted order, so BTC, DOGE and ETH would always beat SOL, UNI and XRP. That is deterministic but arbitrary, and it biases the universe. Proposal: an entry intent carries a strategy-declared `priority`, its signal strength in σ units, and ties break by inst_id. This is an `OrderIntent` field and an engine change, reviewed together with the gate.

**Consequence to expect.** At 0.5% risk per trade, about 20 consecutive full losses reach RC-11. Strategies with long losing runs will halt mid-fold and trade less, and G-3 (trade count) becomes easier to fail. That is the point of D3, not a side effect to tune away.

## 3. D2: spread collection and the calibration rule

**Collector (built in this change).**
- `python -m okxq.data.spreads` polls public `/api/v5/market/books` (5 levels) for the 8 research instruments every 60 s on a fixed grid. It appends one JSON line per instrument to `data/<env>/spreads/YYYY-MM-DD.jsonl`.
- Each line keeps the venue's exact price and size strings, the venue timestamp, and the local request and receive times.
- A failed or invalid book is written as a **gap line** with the reason, never dropped or repaired. A run with no samples, or with more gaps than samples, exits 1 and is not stamped.
- `scripts/collect_spreads.ps1 -Install` registers an hourly task (59-minute runs, S4U, wake-to-run, no overlap). `-Status` exits 1 if the last success is more than 2 h old.
- It must run on the **owner's machine**. A cloud container is deleted with everything it collected.
- First live check, from the cloud host on 2026-10-03: 2 polls, 16 samples, 0 gaps. Full spreads in bp: BTC 0.012, ETH 0.037, XRP 0.67, SOL 0.84, DOGE 1.07, UNI 1.10, NEAR 2.11, SAND 2.7–4.0.

**Pre-registered minimum before any calibration reads the files:**
- 14 complete UTC days;
- at least 90% non-gap polls per instrument per day;
- the start and end dates recorded on the audit chain.

**Proposed calibration rule (to be fixed and hashed before the files are read).**
- slip-v2's spread term changes from the Abdi-Ranaldo estimate to a volatility-scaled measured spread:
  `half_spread_t = max(1 tick, k_i · σ_1h,t · price)`
- `k_i` is the **75th percentile** of (measured half-spread / σ_1h) per instrument over the collection.
- σ_1h comes from **mark-price** 1h candles over the same hours. The collection period is inside the sealed holdout, and the calibration door serves only mark and index candles there, never `last`.
- Rationale: spreads widen with volatility, so a σ-scaled model carries some stress into 2020–2022, which a flat 2026 median would not.

**What this rule cannot fix (stated now so it is not discovered later).**
- 2026 order books are deeper than 2020–2021 alt books. A σ-scaling captures volatility-driven widening, not the secular improvement in liquidity.
- So the calibrated spread will **understate** early-period alt costs by an unknown amount. G-9's 2× slippage stress is the only protection, and the per-instrument and per-year attribution must be read with that in mind.

**Impact (`y_impact`)** stays UNCALIBRATED. The depth levels are recorded for M7 and not used now.

## 4. Sequencing and trial budget

One cycle-2 config: measured fees + calibrated spread + risk gate. Each piece is pinned, and together they give one cost/risk sha. Nothing runs under a half-built config.

| step | what | trials |
|---|---|---|
| 1 | Advisor reviews this design, the RiskGate and priority change, and the two strategy documents | 0 |
| 2 | Build the gate; golden digest unchanged with `risk_gate=None`; 100% branch coverage on the gate | 0 |
| 3 | Collect for ≥ 14 days; run the hashed calibration; pin the cycle-2 config | 0 |
| 4 | intraday_momentum feasibility check (§5); stop here if it fails | 0 |
| 5 | intraday_momentum, grid 4 (one grid; a second needs sign-off) | ≈ 225 |
| 6 | Re-pricing screen for the M4 baselines: re-price their **stored** cycle-1 OOS trades under the cycle-2 fees and spread (arithmetic, not a trial). Re-run a baseline under the full cycle-2 config only if its re-priced pooled OOS net is > 0 | 0, or ≈ 450 per baseline re-run |

Step 6 is a screen, not evidence. Re-pricing ignores path effects (sizing on changed equity, the risk gate), so a positive re-price earns a proper re-run, and a negative one ends the matter. Expected N after cycle 2: about 1,530 if only intraday_momentum runs.

## 5. intraday_momentum feasibility check (signal-free, pre-registered)

- Before any trial, compute, per instrument over the research window, the mean absolute 22:00–23:00 UTC return `E|r|` and the hit rate needed to break even:
  `p* = ½ + c / (2·E|r|)`, where `c` is the round-trip cost under the cycle-2 config (≈ 10 bp fees + 2 × half-spread).
- This uses no signal and selects nothing, so it can't choose a parameter. It only asks whether any plausible signal could pay the toll.
- **Rule:** if the universe-median `p*` is **above 0.56**, the candidate is not run, and the result is recorded as DISCARDED on feasibility, with no trials added.
- Why 0.56: [Guessing] published intraday-momentum effects are of the order of a few percent of explained variance, which corresponds to hit rates in the low 0.50s. A cutoff of 0.56 already gives the hypothesis generous room. The advisor should set this number, not me.

## 6. New venue fact: the funding interval changes at runtime (V-15)

- [Certain, archived data] SAND-USDT-SWAP settled at 00/08/16 UTC until 2026-10-02 16:00, when its rate hit the −1% floor. From 20:00 it settled **every 4 h** (20, 00, 04, 08, 12). The public `funding-rate` endpoint confirms 4 h (`nextFundingTime − prevFundingTime = 14,400,000 ms`).
- The other seven instruments are on 00/08/16 across all 289 archived settlements.
- Consequences:
  - research funding (funding-bound-v1) is on a fixed 8 h grid, so **research cannot see this**;
  - a strategy that is "flat across settlements" by clock arithmetic is wrong live, and live code must read `nextFundingTime`;
  - 22:00–23:00 UTC is clear of both the 8 h and the 4 h grids, so intraday_momentum's window survives this case;
  - a 1 h or 2 h interval, if OKX uses one, would not be clear. [Guessing] I have not seen one.

## 7. Questions for the advisor

1. Is the halt proxy right (RC-10 resumes at the next 00:00, RC-11 latches for the rest of the window)? Or should RC-10 also latch for the window, which is harsher?
2. Strategy-declared `priority` for simultaneous entries, or a neutral deterministic shuffle keyed on hash(ts, inst)?
3. Calibration: the 75th percentile and σ from mark candles. Is 14 days enough? Should a high-volatility day be required (which can't be guaranteed)?
4. The feasibility cutoff `p* > 0.56`: set the number.
5. Is the re-pricing screen in step 6 acceptable in place of the owner's blanket re-run?
6. Do you agree btc_lead_lag at 1h should not be built (review document)?
