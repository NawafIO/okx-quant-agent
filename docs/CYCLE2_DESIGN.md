# Cycle 2 design (Revision 1, after the advisor's review; no trial runs yet)

Owner decisions of 2026-10-03:
- **D1:** adopt measured perpetual fees, and re-run the M4 baselines.
- **D2:** collect real order-book spreads.
- **D3:** enforce the M5 risk limits in research.
- **Candidates:** intraday_momentum (22:00–23:00 UTC) now; a design review for btc_lead_lag.

Status: trial log **N = 1,302**. Holdout sealed. PHASE 2. No cycle-2 trial has run.

Advisor review of 7ae7250: **PROCEED WITH CHANGES** (§8). The four blocking findings are folded in below. btc_lead_lag at 1h: **STOP**, agreed.

## 0. The owner's decision still open: the D1 re-run

**My recommendation: do not re-run the M4 baselines in cycle 2.** It is the owner's call (advisor B-4): the M4 closing-audit ruling was advice on trial budget, not a veto. The predictable outcome, stated plainly:
- Cycle 2 changes fees, spreads and risk limits. It does **not** change funding-bound-v1, which is the dominant cost of the baselines. [Likely] No baseline can turn positive:
  - trend_breakout: about +29.0k gross, −1.3k fees at measured rates, about 0 slippage, −32.2k funding ≈ **−4.5k**;
  - keltner_reversion: **−36.7k** gross before slippage;
  - vol_compression_breakout: about **−24k to −32k** even at zero slippage.
- Options for the owner:
  1. **Skip** (recommended): 0 trials.
  2. **Re-pricing screen**: re-price the stored cycle-1 OOS trades under cycle-2 fees and spread. 0 trials. It is a screen, not evidence: it ignores sizing path and the D3 cluster cap, which would change which SAND shorts are taken.
  3. **One full re-run** under the complete cycle-2 config, never under fees alone: about **1,350 trials**, for an outcome predicted above.

## 1. D1: fees

- `costs.FEES_MEASURED_PERP` (maker 0.02%, taker 0.05%; MEASURED, audit record 1867e2a2c00c) is the fee schedule of the **cycle-2 cost config**, a new `cost_config_sha` recorded on every cycle-2 trial.
- The engine charges taker on market entries, stop exits and signal exits, and maker only on a resting take-profit (engine.py `_fee`). The cost a strategy faces is therefore about **0.10% per round trip plus slippage**.
- `FEES_USER_SPOT_SCHEDULE` stays because cycle-1 records reference it. It is not used for new trials.

## 2. D3: the M5 risk limits inside the backtest, with M5's exact semantics

**Mechanism.**
- An optional `RiskGate` is consulted in `BacktestEngine._accept` before an entry is queued.
- It builds the M5 input types (`OpenPosition` with the current **mark**, a portfolio snapshot) from engine state. It then calls **the same functions** in `okxq.risk.sizing` and `okxq.risk.checks`, with no re-implementation; an equivalence test runs the M5 fixtures through the gate's adapter.
- A refusal is an engine `Rejection` (`risk:RC-07`, `risk:SZ-2`, and so on).
- `risk_gate=None` is the default, so the golden digest and every cycle-1 result are unchanged; a test pins that.
- The pinned `RiskPolicy` sha (b4e030fc) is part of the research config and is recorded on every Trial and GateReport.

**What M5 does, and therefore what research does.** The draft had departed from M5 in three places, now corrected (advisor B-3):
- **Heat (RC-06, RC-07)** is `open_risk`: qty × (**mark** − stop), not fill − stop. A position whose mark is beyond its stop makes `open_risk` None, so every entry is refused until it closes. A failed stop is an incident, not freed heat.
- **Sizing** is M5's sizer, which works from conservative equity (realised P&L plus **unrealised losses only**). This replaces ResearchSizing's plain-equity sizing for cycle-2 trials.
- **SZ-2** (stop ≥ 0.5 × ATR(14) Wilder) and **SZ-3** (stop ≥ 10 ticks) apply. A strategy whose stop is tighter is refused, not resized.

| check | in research | how |
|---|---|---|
| SZ-1..4 | yes | M5 sizer |
| RC-01 kill switch | yes, as the halt latch | see halts |
| RC-02 env / PHASE | N/A | no environment in a backtest |
| RC-03 freshness | already enforced | `no_bar_closed_at_decision_time` |
| RC-04 tradable | N/A | historical venue state unknown (V-14) |
| RC-05..RC-08 | yes | M5 functions; cluster CRYPTO, so **at most 3 full-size positions** |
| RC-09 | already enforced | `already_positioned`, `entry_already_pending` |
| RC-10, RC-11 | yes | halts below |
| RC-12a..f | yes | M5 functions (3×, margin, liquidation ≥ 1.5× stop distance) |
| RC-13 3-loss cooldown 24 h | yes | from closed-trade outcomes |
| RC-14 ≤ 3 entries per hour | yes | at most 3 entries per decision bar |
| RC-15 API breaker | N/A | no venue |
| RC-16 | CRISIS yes, sentiment N/A | regime from daily bars closed before T. M5 Q2 ruled that the block stays, with the label's FAIL recorded |

**Halts (advisor ruling on Q1).**
- **RC-11:** the high-water mark is carried **across the stitched OOS folds**, and a ≥ 10% drawdown latches **permanently**: no entries for the rest of the run. Live, nothing resets the high-water mark, so this is not harsher than live; it is live.
- **RC-10:** entries resume at the **first 00:00 UTC at least 24 h after the halt**. With "next 00:00", RC-10 would do nothing for a strategy that only trades at 22:00.
- Open positions keep their stops and exits. M5 only blocks entries; flattening is M6, interface only.
- Selection (in-sample) runs each start with fresh gate state. Only the OOS chain is stitched.
- **For the owner:** M5 has **no procedure for resetting the high-water mark**. Live, a 10% drawdown ends trading until one is designed, reviewed and pinned.

**Simultaneous entries (advisor ruling on Q2).**
- The adapter visits instruments in sorted order (`strategy/base.py` `on_bar`), and `_accept` takes intents in that order. Under the cap of 3, BTC, DOGE and ETH would always win.
- The default order becomes a neutral shuffle keyed on `sha256(ts, inst_id)`. This covers the M4 baselines too.
- A strategy may declare a `priority` only in its pinned rationale, as part of its code version. Ties always break by the hash, never by inst_id.

**Consequence to expect.**
- At 0.5% risk per trade, about 20 consecutive full losses reach RC-11, and the stitched high-water mark makes that final.
- Strategies with losing runs will trade less, and G-3 becomes easier to fail. That is D3 doing its job.

## 3. D2: spread collection and the calibration rule

**Collector (built).**
- `python -m okxq.data.spreads` polls public `/api/v5/market/books` (5 levels) for the 8 research instruments every 60 s on a fixed grid. It appends to `data/<env>/spreads/YYYY-MM-DD.jsonl` with exact venue strings, the venue timestamp, and the local request and receive times.
- A failed or invalid book is a **gap line**, never dropped or repaired.
- A run is stamped successful only if **every instrument** has samples and no more gaps than samples (fixed after review: the first version judged this globally).
- `scripts/collect_spreads.ps1 -Install` registers an hourly task. `-Status` exits 1 if the last success is more than 2 h old.
- First live check from the cloud host: 2 polls, 16 samples, 0 gaps. Full spreads in bp: BTC 0.012, ETH 0.037, XRP 0.67, SOL 0.84, DOGE 1.07, UNI 1.10, NEAR 2.11, SAND 2.7–4.0.
- **The `-Install` and `-Status` paths have not run on Windows.** The 14-day clock starts only when the owner's `-Status` output (result 0, fresh stamp) is recorded on the audit chain, as U-2 was.

**Minimum before calibration reads the files:**
- 14 complete UTC days after that record;
- at least 90% non-gap polls per instrument per day.

**Calibration rule (fixed and hashed before the files are read):**
- `half_spread_t = max(1 tick, k_i · σ_1h,t · price)`.
- `k_i` is the **75th percentile** over the collection of (measured half-spread / σ_1h,mark), per instrument.
- σ comes from 1h **mark** candles through the calibration door. The collection lies inside the sealed holdout, and that door serves only mark and index there. This reads holdout-period volatility, not direction; the read is recorded.
- **Mark vs last.** Research applies k to σ from **last-price** bars. The ratio σ_mark / σ_last is measured per instrument on **pre-holdout** data and multiplies k_i.
- **Staleness.** A sample whose `venue_ms` is more than **5 s** before its `req_ms` is dropped as stale. The share dropped is reported.
- **Coverage.** The range of σ actually covered is recorded. A high-volatility day is not required, because none can be guaranteed.

**What this rule cannot fix.**
- 2026 books are deeper than 2020–2021 alt books, so the calibrated spread will **understate** early-period alt costs.
- G-9 doubling a near-zero spread protects almost nothing there. A candidate that passes is therefore **also reported under the old slip-v2** (reported, not gated).

**Impact (`y_impact`)** stays UNCALIBRATED. Depth is recorded for M7.

## 4. Sequencing and trial budget

| step | what | trials |
|---|---|---|
| 1 | Owner decides §0; owner installs the collector and supplies Windows `-Status` evidence | 0 |
| 2 | Build the RiskGate, the hash-shuffle order and the priority field (reviewed); golden digest unchanged with `risk_gate=None`; 100% branch coverage on the gate | 0 |
| 3 | Pin the intraday_momentum rationale (hash). Then run its **fees-only** feasibility bound (§5), which can only stop it | 0 |
| 4 | 14 days of collection; hashed calibration; pin the cycle-2 config; full feasibility check | 0 |
| 5 | intraday_momentum: grid 4, plus the pre-registered window-shift check (two fixed-parameter OOS re-runs, counted) | about 225 + the shift runs |
| 6 | Baselines only if the owner chooses option 2 or 3 in §0 | 0 or about 1,350 |

Expected N after cycle 2, without a baseline re-run: about 1,530 plus the window-shift trials. I have not yet measured how many trials one fixed-parameter OOS re-run logs, so I am not giving a number.

## 5. intraday_momentum feasibility check (signal-free, run only after the rationale is pinned)

- Break-even hit rate: `p* = ½ + c / (2·E|r|)`, where `c` is the round-trip cost.
- E|r| is computed two ways, using **magnitude only**, never direction:
  - the unconditional mean absolute 22:00–23:00 UTC return per instrument;
  - the same mean on the days the k = 0.5 filter would pass.
- The **more generous** (lower) p* of the two is used.
- **Rule (advisor ruling on Q4):** if the universe-median p* is **above 0.55**, the candidate is DISCARDED on feasibility with no trials. [Guessing] Gao et al.'s R² of about 1.6% maps to a hit rate of about 0.54.
- **Step 3 (fees only):** c = 10 bp, with no spread. This lower bound can only stop the candidate, never start it.
- **Step 4 (full):** c = 10 bp + 2 × calibrated half-spread.
- **Also reported, signal-free:** the research-data volume profile by UTC hour. The cited mechanisms are strongest in high-volume sessions, and 22:00 UTC was chosen to avoid settlements, not for the mechanism.

## 6. Venue fact V-15: the funding interval changes at runtime

- [Certain, archived data] SAND-USDT-SWAP settled at 00/08/16 UTC until 2026-10-02 16:00, when its rate reached −1%, the floor. From 20:00 it settled **every 4 h**. The public `funding-rate` endpoint confirms a 4 h gap (14,400,000 ms).
- The other seven instruments are on 00/08/16 across all 289 archived settlements.
- [Likely, one event] A capped rate shortens the interval. The general rule is not established from a single case.
- Research funding (funding-bound-v1) uses a fixed 8 h grid. So **research understates SAND-short funding while it sits at the floor**: 1% per 4 h live against 1% per 8 h modelled.
- Live code must read `nextFundingTime`. 22:00–23:00 UTC is clear of both the 8 h and the 4 h grids.

## 7. Questions: answered by the advisor

- **Q1 (halts):** see §2.
- **Q2 (entry order):** hash shuffle by default; see §2.
- **Q3 (calibration):** 14 days, 75th percentile, no volatility-day requirement; with the mark/last correction and staleness rule in §3.
- **Q4 (feasibility cutoff):** 0.55.
- **Q5 (re-pricing screen):** acceptable only as a recommendation the owner accepts; see §0.
- **Q6 (btc_lead_lag):** do not build at 1h. A daily variant would need its own rationale and the reference view.

## 8. Revision 1: advisor review of 7ae7250 (PROCEED WITH CHANGES)

| finding | disposition |
|---|---|
| B-1: the clock window escapes G-6 (`entry_lead_h` rounds back to itself; `gates.py` only reports unperturbable parameters) | Window-shift check pre-registered in intraday_momentum.md |
| B-2: warmup of 48 bars cannot hold σ at −20% warmup; Wilder ATR(24) unconverged | warmup_bars 480, rationale updated |
| B-3: D3 departed from M5 (fill-to-stop, plain equity, no SZ-2/3, lenient RC-11, a dead RC-10) | §2 rewritten to M5's semantics |
| B-4: replacing the owner's re-run is the owner's decision | §0 |
| Collector: global ok; non-integer seqId crashed the run | Fixed, with tests: per-instrument judgement; seqId → BookError gap line |
| Collector: Windows paths unverified | The 14-day clock waits for the owner's `-Status` evidence |
| Over-claims: G-8 "well-powered", trade count ignoring RC-13, the SAND/NEAR cap-size claim, V-15 certainty, r_first "log" with a simple formula, the N estimate | Corrected in each document |
