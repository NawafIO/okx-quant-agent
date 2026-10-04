# Cycle 2 design (Revision 1, after the advisor's review; no trial runs yet)

Owner decisions of 2026-10-03:
- **D1:** adopt measured perpetual fees, and re-run the M4 baselines.
- **D2:** collect real order-book spreads.
- **D3:** enforce the M5 risk limits in research.
- **Candidates:** intraday_momentum (22:00–23:00 UTC) now; a design review for btc_lead_lag.

Status: trial log **N = 1,302**. Holdout sealed. PHASE 2. No cycle-2 trial has run.

**FROZEN 2026-10-04 by the owner for 14 days** while the D2 spread collection runs (CYCLE2_RESEARCH_LOG, "State at the freeze").

**Update 2026-10-03:** intraday_momentum is **DISCARDED on the fees-only feasibility bound** (min median p* 0.565 > 0.55; docs/CYCLE2_RESEARCH_LOG.md). N stays 1,302. Cycle 2 has no live candidate. §4 steps 4–5 do not run for it.

Advisor review of 7ae7250: **PROCEED WITH CHANGES** (§8). The four blocking findings are folded in below. btc_lead_lag at 1h: **STOP**, agreed.

## 0. D1 re-run: DECIDED by the owner, SKIP (0 trials), 2026-10-03

**Owner's decision:** "Skip re-running the M4 baselines (0 trials). Funding costs remain the bottleneck." It is recorded on the PAPER audit chain (`owner_decision`) and in the ledger snapshot. The analysis that informed it follows.

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
| SZ-1..4 | yes | M5 sizer. SZ-2's Wilder ATR(14) is computed by the gate, so the strategy convergence test does not cover it. The gate needs its own fixed-buffer definition and an exactness test |
| RC-01 kill switch | yes, as the halt latch | see halts |
| RC-02 env / PHASE | N/A | no environment in a backtest |
| RC-03 freshness | already enforced | `no_bar_closed_at_decision_time` |
| RC-04 tradable | N/A | historical venue state unknown (V-14) |
| RC-05..RC-08 | yes | M5 functions; cluster CRYPTO, so **at most 3 full-size positions** |
| RC-09 | already enforced | `already_positioned`, `entry_already_pending` |
| RC-10, RC-11 | yes | halts below |
| RC-12a..f | yes | M5 functions (3×, margin, liquidation ≥ 1.5× stop distance). RC-12e's max size is `maxMktSz × ctVal` from `docs/instrument_specs.raw.json` (MEASURED 2026-10-03), applied to every year. [Guessing] Historical limits, and `ctVal` itself, may have differed. In base units: BTC 350, ETH 9,000, SOL 39,000, NEAR 78,000, UNI 32,000, SAND 430,000, XRP 1.6M, DOGE 24M. It can bind at 300k notional only where the price is low, e.g. NEAR near $2.5; RC-12e refusals are reported |
| RC-13 3-loss cooldown 24 h | yes | from closed-trade outcomes |
| RC-14 ≤ 3 entries per hour | yes | at most 3 entries per decision bar |
| RC-15 API breaker | N/A | no venue |
| RC-16 | CRISIS yes, sentiment N/A | regime from daily bars closed before T. M5 Q2 ruled that the block stays, with the label's FAIL recorded. With no label yet (classifier warm-up), M5 rejects, so the first folds lose entries; the count is reported |

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
- per instrument per day, at least 90% of the **expected** 1,440 polls are non-gap samples (≥ 1,296).
  - Missing polls write no gap line (nothing runs while the machine sleeps), so coverage is counted against 1,440, **not** against the lines present.
  - The hourly 59-minute runs cap coverage at about 98%.
- Days that fail are excluded, not repaired. The 14 qualifying days need not be consecutive. Their dates are listed in the calibration record.

**Owner evidence so far (2026-10-04):**
- First Windows run 2026-10-03 22:04:47 → 23:02:55Z: 59 polls, 472 samples, 0 gaps, empty stderr.
- The next run (23:04:48Z) never logged an end: the machine slept, and **WakeToRun did not wake it**. Nothing ran until 2026-10-04 10:21:05Z, about 11 h lost.
- So 2026-10-04 cannot qualify. The clock record waits for the owner's `-Status` after the 10:21Z run.

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
| B-2: warmup of 48 bars cannot hold σ at −20% warmup; Wilder ATR(24) unconverged | First fix (Wilder, warmup 480) **rejected** at the confirmation round: still about 1e-6 off with an exact-equality test. Second fix, the advisor's preferred option: simple-mean ATR, warmup 96, convergence test pre-registered as passing at the +20% / −20% corner |
| B-3: D3 departed from M5 (fill-to-stop, plain equity, no SZ-2/3, lenient RC-11, a dead RC-10) | §2 rewritten to M5's semantics. Confirmation round: RC-12e would refuse every entry with max size unset; fed from the M2-measured `maxMktSz` |
| B-4: replacing the owner's re-run is the owner's decision | §0 |
| Collector: global ok; non-integer seqId crashed the run | Fixed, with tests: per-instrument judgement; seqId → BookError gap line |
| Collector: Windows paths unverified | The 14-day clock waits for the owner's `-Status` evidence |
| Over-claims: G-8 "well-powered", trade count ignoring RC-13, the SAND/NEAR cap-size claim, V-15 certainty, r_first "log" with a simple formula, the N estimate | Corrected in each document |

## 9. Track 1: intraday session framework (Revision 1, after the advisor's review of f78d051)

**Outcome 2026-10-04: both Track-1 candidates DISCARDED on the pinned lower-bound baseline. Required edge 0.194–0.233 R per trade against a 0.15 R cutoff, at 20 seeds (CYCLE2_RESEARCH_LOG). No trials; N = 1,302.**

**Owner direction:**
- short-term intraday only, with holds of 2–6 h;
- strictly flat before every funding settlement;
- asymmetric setups with tight stops and a 1.5–3.0% target;
- funding zero by construction.

**Advisor verdict: PROCEED WITH CHANGES.**
- Run `session_orb` and `impulse_continuation`, each only after the lower-bound baseline (§9.3).
- **STOP `squeeze_expansion_intraday`** (§9.6).
- Nothing is pinned until the blocking findings below are reflected.

### 9.1 Shared rules

- **Bars.** 1h only. slip-v2 is calibrated on 1h, and 5m exists for BTC, ETH and SOL only.
- **Sessions.** 8 h blocks between settlements: 00–08, 08–16, 16–24 UTC.
- **Forced exit.** Decided at T = block end − 1 h and filled at that bar's open (07:00, 15:00, 23:00).
- **Entry window.** No entry unless ≥ 2 bars remain. `max_hold_bars` = 6 and `min_room_bars` = 2 are **structural owner constraints**, recorded as untested by G-6.
- **`entry_ttl_bars` = 1, pinned.** A larger value could fill an entry remainder after the forced exit.
- **Funding: zero by construction, with known holes** (advisor, verified against `engine._funding` / `_fill_pending_at_open`).
  - The engine charges the 08:00 settlement only if a position exists before or after the 08:00 open, so a 07:00 exit pays nothing.
  - A position can still be open at a settlement if:
    1. a forced exit partly fills under the 10% participation cap, and the remainder fills at the settlement open;
    2. a 06:00 or 07:00 bar is missing (none found at M1, so the hole is latent).
  - The run **reports and charges** any such funding event. It never crashes and never hides one.
  - **Live divergence (V-15):** SAND on 4 h settlements means a 16–24 block crosses a 20:00 settlement live, which research models as zero. Recorded.
- **ATR.** The simple mean of 24 true ranges (finite memory). **Measured** (signal-free, research window, median): ATR_1h / σ_1h = 1.58–1.63 on every instrument.

| | BTC | ETH | XRP | DOGE | SAND | SOL | UNI | NEAR |
|---|---|---|---|---|---|---|---|---|
| ATR_1h (median) | 0.76% | 1.01% | 1.11% | 1.30% | 1.40% | 1.47% | 1.49% | 1.68% |
| 1.5 ATR target | 1.14% | 1.52% | 1.67% | 1.95% | 2.10% | 2.21% | 2.24% | 2.52% |
| 2.0 ATR target | 1.52% | 2.02% | 2.22% | 2.60% | 2.79% | 2.94% | 2.98% | 3.36% |
| 3.0 ATR target | 2.28% | 3.03% | 3.33% | 3.90% | 4.19% | 4.41% | 4.46% | 5.05% |

- **Stop.** 1.0 ATR. It clears the SZ-2 floor (0.5 × Wilder ATR(14)) in the usual case. After an impulse, Wilder ATR rises faster than the 24-bar mean, so the margin over the floor is **less than 2×**, and M5 may refuse some entries; refusals are reported.
- **Target: grid {1.5, 2.0} ATR, a maker order** (changed from {2, 3}; see §9.2a). This is 1.1–3.4%, mostly inside the owner's 1.5–3.0% band. On BTC at 1.5 ATR it is below the band.
- **Priority** (declared): signal strength in ATR or σ units; ties broken by the hash shuffle.

### 9.2 Cost arithmetic in R (corrected)

- **Stop overshoot.** 0.2 σ_1h ÷ stop. At 1.0 ATR ≈ 1.6 σ that is **≈ 0.125 R**; at 0.5 ATR it would be 0.25 R.
- **Fees in R.** Round-trip bp ÷ stop bp: **10 bp when stopped** (taker, taker), **7 bp when the target fills** (taker in, maker out). On BTC (stop ≈ 76 bp): 0.13 R and 0.09 R.
- **Not yet counted:** taker slippage on the entry and on the time exit (spread plus impact), and the stop-first resolution of ambiguous bars. The baseline (§9.3) measures all of it.
- **Owner, please note:** under this model a **tighter stop is more expensive**. Halving the stop doubles the overshoot and the fee drag in R, and raises the share of ambiguous bars.

### 9.2a The time cap removes most of the asymmetry (advisor B-1)

Simulated exit mix of a **driftless** Gaussian path (no edge, no fat tails, no costs, continuous monitoring), with a 1 ATR stop and ATR = 1.6 σ:

| hold | target 1.5 ATR | target 2.0 ATR | target 3.0 ATR |
|---|---|---|---|
| 4 h | stop 41% / target 21% / time 38% | 41 / 10 / 50 | 41 / **1** / 58 |
| 6 h | 49 / 29 / 22 | 49 / 17 / 33 | 50 / **5** / 46 |

- [Likely] A 3 ATR target is reached in 1–5% of trades. It is decorative, and most trades end on the time exit. The payoff "shape" the owner wants therefore exists only for targets of about 1.5–2 ATR. Even there, a third or more of exits are time exits of small size.
- The win rate is not the 25–35% assumed in the first draft. With time exits it is about 35–45%, made of many small time-exit P&Ls.
- Each rationale states this estimated mix, and the baseline measures the real one.

### 9.3 Lower-bound feasibility baseline (rules set by the advisor)

- **What runs.** Random-entry brackets: entries at uniformly random eligible bars, with each candidate's exact bracket, time cap and forced exit. Run through the real engine on research data, with the D3 gate on.
  - **≥ 20 fixed seeds;** the mean and the standard error are reported.
  - The spec is pinned (hash) before the run.
- **Cost config for the stop rule: a LOWER BOUND.**
  - measured fees;
  - the frozen stop overshoot (k = 0.2);
  - a 1-tick spread, and **no** Abdi-Ranaldo spread (it is overstated, finding M-2);
  - impact as in slip-v2.

  The current slip-v2 result is reported alongside, but it is not used for the decision. The baseline can therefore only **stop** a candidate, never start one.
- **Rule:** if the required edge per trade (minus the baseline's mean net R) is **> 0.15 R**, the candidate is DISCARDED with no trials.
  - [Judgment, advisor] The p* = 0.55 cutoff on a symmetric ±1 R bet amounts to about 0.10 R of skill edge; 0.15 R leaves some room for asymmetry.
- **Not trials.** No signal and no selection. It has its own log (`docs/c2_runs/`), with the seeds and the pinned spec.
- **It is a lower bound.** Real breakout and impulse entries follow high-range bars, where σ-scaled slippage and overshoot are larger, and they cluster across instruments, so the D3 caps and RC-13 bind harder. The real required edge is higher.
- **Not run:** random direction at the signal bars. That would test the volatility half of the hypothesis, and would count as trials.
- **The 5m diagnostic** (report-only; BTC, ETH and SOL): in ambiguous 1h bars, the share where the 5m path reached the target first. Never used to change the engine.

### 9.4 RC-13: an owner decision before any trial

[Certain, `portfolio.py` / `checks.rc13`, advisor-confirmed]
- One portfolio-wide counter: it goes up on net P&L < 0 and resets on P&L ≥ 0.
- At ≥ 3 losses, entries are blocked until 24 h after the last loss. Every further loss restarts the cooldown until a win arrives.
- **A defect independent of Track 1:** a count ignores size. A −0.02 R time-exit loss counts the same as a −1.2 R stop loss.
- With the many small time-exit losses that §9.2a predicts, [Likely] Track-1 strategies starve.
- Options:
  - **(a) keep RC-13 frozen;**
  - **(b) revise it before any candidate trial,** argued from risk principles. For example, a cooldown triggered by **R lost** in 24 h instead of a loss count.
- Conditions for (b), from the advisor:
  - the blocking rate the baseline measures is context, **not** the justification;
  - the decision and the re-pin come before any candidate trial;
  - it applies live too;
  - M5 acceptance re-runs (100% branch coverage, property tests).
- Measuring the blocking rate with a random baseline is not fitting the policy to a strategy. Loosening the policy because Track 1 "wants trades" would be.

### 9.5 Sequencing and trial budget

| step | what | trials |
|---|---|---|
| 1 | The owner decides RC-13 (a) or (b). If (b): a reviewed policy revision, a re-pin, and M5 acceptance re-run | 0 |
| 2 | Build the D3 gate (§2) and the shared intraday harness; reviewed | 0 |
| 3 | Pin both rationales and the baseline spec; run the lower-bound baseline per candidate | 0 |
| 4 | Survivors run under the full cycle-2 config (after the spread calibration): grid 4 each, plus session_orb's `or_bars` + 1 check (counted) | about 225 each |

N after Track 1: about 1,750 if both survive the baseline.

### 9.6 squeeze_expansion_intraday: STOPPED (advisor B-3)

[Certain, `docs/m4_runs/vol_compression_breakout_run1.txt`] The draft's premise, that funding dominated M4's loss, was **false**. In the M4 run:

| component | amount |
|---|---|
| net | −73.2k |
| funding | −31.0k |
| fees | −31.2k |
| slippage | −25.2k |
| gross before slippage | +14.2k |

Without funding it still loses about −42k under cycle-1 costs, and intraday holds raise fees and slippage in R. It would be its family's **last** grid, and on this evidence it is not spent. The draft stays in the repository with a STOPPED header.
