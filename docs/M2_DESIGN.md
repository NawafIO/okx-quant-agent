# M2 - Backtest Engine & Acceptance Gates: Design Record

Status (2026-10-03, after the Chief Advisor's closing audit): **M2 COMPLETE PENDING USER EVIDENCE - not
signed off.** Waiting on the user for: measured perpetual-swap fee rates, and evidence that P-11 ran.
M3 may begin under the advisor's conditions (§2f); M4 is blocked until both items land.

Earlier status: Real data is
backfilled and independently verified. On it, the D1 funding model FAILED validation and was replaced
by an adverse bound (§2c). The cost model was recalibrated twice (§2d). The engine's stop path turned
out to be optimistic and now carries a frozen overshoot term (§2e). Remaining blockers are in the
[Sign-off checklist](#sign-off-checklist): measured perp fees (user) and P-11 run evidence.

Code: `src/okxq/backtest/`. Tests: `tests/unit/backtest/`, plus two new guard modules in
`tests/guards/`. Guard tests: **57** (the original 35 are unchanged, 22 added, none removed or weakened).

---

## 1. What exists

| Module | Role |
|---|---|
| `history.py` | Right-truncated view. A bar enters a strategy-reachable buffer only on CLOSE. Nothing reachable holds an unclosed bar, accessors return copies, and asking for the next bar raises |
| `engine.py` | Chronological loop. At each bar-close instant T: execution, then publication, then MTM, then one decision. Fills, stops, gaps, liquidation, partial fills, funding |
| `types.py` | Venue parameters with **provenance** (MEASURED / SYNTHETIC / UNMEASURED); `G9_STRESS` |
| `sizing.py` | **Provisional** fixed-fractional sizer. Not M5; labelled in every result |
| `metrics.py` | Gate-bearing numbers are exact `Decimal`; distribution statistics are float; undefined is `None` |
| `gates.py` | G-1..G-9 thresholds **and definitions**, frozen, with the SHA-256 pinned by a guard test |
| `trials.py` | Trial counter on the hash-chained audit trail (G-8 denominator) |
| `holdout.py` | The only sanctioned data path: research door (sealed), calibration door (logged), holdout door (once per strategy) |
| `walkforward.py` | `ResearchProtocol`: folds, selection, OOS, final fit, perturbation, stress, then `GateInputs` |
| `funding_model.py` | D1 reconstruction from the mark/index premium plus its validation gate |
| `reference_strategies.py` | `RandomEntry`, the cost-model sanity check |
| `cli.py` | `funding-validate`, `funding-model`, `sanity-random` |

Frozen gate definitions: `FrozenGates().sha256() = 48908f93c671a07134f1852e09f3773f99a7f2e8f3b58d07932d7c69591cbd0f`
(pinned in `gates.py` and again in `tests/guards/test_gates_frozen.py`; a `FrozenGates` with any other
definitions raises `GateTamperError` on construction).

---

## 2. Chief Advisor checkpoint 1 (plan review), 2026-10-03

Verdict: **PROCEED WITH CHANGES.** The advisor numbered its findings D1..D12; they are recorded here as A-1..A-12 to avoid
clashing with deliverable D1.

| # | Finding | Resolution |
|---|---|---|
| A-1 | Instruments without funding coverage would accrue **zero** funding, understating cost in the profitable direction | **Adopted.** The engine refuses any window its funding series does not cover, including internal gaps over 1.5 intervals. The CLI drops such instruments loudly |
| A-2 | Partial fills would fragment "trades", inflating G-3 and diluting G-7 | **Adopted.** A trade is one position lifecycle, open to flat |
| A-3 | Realised funding (2026-06-29 onward) lies entirely inside the holdout (2025-10-01 onward) | **Adopted.** A separate logged calibration door reads mark/index/funding only, never last price. The holdout is open-ended |
| A-4 | G-9 left funding *receipts* unstressed | **Adopted.** Paid x2, received x0.5. G-9 also re-checks G-4 |
| A-5 | "Exits at T pay, entries at T don't" is venue behaviour asserted from memory | **Adopted.** Boundary settlements are resolved adversely and recorded as an assumption. The per-instrument interval is assumed to hold historically; also recorded |
| A-6 | Wall-clock timestamps in the audit chain would break the bit-identical test | **Adopted.** `BacktestResult` holds no wall-clock time; the trial log lives outside it |
| A-7 | Same-T cross-sectional ordering | **Adopted.** All bars closing at T are published before any decision at T |
| A-8 | MaxDD basis was undefined | **Adopted.** G-1 is measured on the MTM curve at every bar close |
| A-9 | A take-profit that fills on a touch is optimistic | **Adopted.** Needs a strict trade-through; never fills at a better gapped price |
| A-10 | Contract volume needs the unmeasured `ctVal` | **Adopted.** All sizes are in base units |
| A-11 | Holdout enforcement would be convention only | **Adopted.** A guard test scans research/strategy packages for direct data imports |
| A-12 | PF with no losing trades is infinite | **Adopted.** Undefined PF is INVALID, never a pass |

Rulings adopted: G-5 uses the trade-weighted IS PF of the selected config (pooling overlapping IS windows
double-counts); G-6 keeps G-1's 15% MaxDD; integer params that round to themselves are reported
"unperturbable", not passed; G-8 includes the skew and kurtosis terms; the holdout run is subject to G-3;
D1's fit/validate split is time-ordered with an absolute floor; walk-forward selection objective and
flat-at-fold-boundary are frozen.

**One finding disputed, recorded rather than silently overruled:** the advisor asked that when a stop
and the liquidation price are both inside one bar, liquidation (the worse outcome) should always be
assumed first. The engine instead takes the level **nearer the open** first. For a long whose stop sits
above liquidation, a continuous price path must cross the stop before it reaches liquidation. Assuming
otherwise would model a jump inside the bar, which OHLC data cannot show. Stop-vs-target is different:
the two lie in opposite directions, so OHLC really cannot order them, and stop-first is used there. The
gap case is already pessimistic: an open beyond both levels is a liquidation at the open. **The user may
overrule this;** changing it would also change the golden digest.

Advisor note not adopted: register a cloud P-11 routine now. A schedule inside an ephemeral container
(or a routine that runs in one) loses everything it archives when the container is reclaimed. P-11 has
to run on a persistent host; see `scripts/archive_funding.sh`.

---

## 2b. Chief Advisor checkpoint 3 (audit of the built code), 2026-10-03

Verdict: **FIX BEFORE REPORTING.** The advisor confirmed three findings by running counter-examples. All ten are fixed, each
with a regression test.

| # | Defect | Fix |
|---|---|---|
| 1 | **Engine look-ahead across instruments.** An entry on B at T_open was margin-checked against A's close and intrabar exits at T, so results depended on inst_id order | All open fills at T run before any intrabar event, and publication follows all execution. One timeframe per run (mixed timeframes refused). Regression: the advisor's counter-example under both orderings fails on the old engine and passes now |
| 2 | Gates and the holdout boundary were replaceable parameters; the unseal and trial-log paths were caller-chosen | Every `gates` parameter removed. `FrozenGates` refuses non-pinned construction. Unseal goes on the environment's audit chain; the trial log path comes from the profile |
| 3 | G-6 perturbed the full-span fit over a span it was fit on (in-sample) | G-6 perturbs each fold's **selected** params on that fold's **OOS** window, pooled |
| 4 | Modelled funding was written for instruments validation never scored | New `PARTIAL` verdict; the model is written only for instruments scored and passed |
| 5 | The isolation guard was vacuous and had holes | Scans `reference_strategies.py` too (never empty); catches `from okxq.backtest import holdout`, `importlib`, `__import__`, `FrozenGates` |
| 6 | A number hidden in a string or tuple dodged G-6 | Such parameters make G-6 INVALID |
| 7 | Slippage arithmetic had no asserting test | Hand-computed fixture with nonzero coefficients (1.8479 rounds up to 19 ticks) |
| 8 | Trials can go unrecorded when the engine is called directly | **Not fixable in Python**: `BacktestEngine.run` is public. Recorded as open issue 8 below |
| 9 | Re-entry onto a partially exited position kept the old stop | No entry while a position exists, except continuation of the same order |
| 10 | Holdout evaluation omitted G-7 and cost stress | Holdout must pass G-1, G-3, G-4, G-7 and G-9 (pin changed accordingly) |

**Disputed point ruled:** the advisor **accepted** the stop-vs-liquidation rule (the level nearer the
open triggers first). The residual risk is a flash move through both levels within one bar. That is
what the slippage volatility term and the M7 PAPER reconciliation are for.

Process note: the first two attempts at a multi-instrument future-corruption test **did not** catch
defect 1 on the old engine. Corrupting the other instrument fired its own stops, which freed margin and
masked the leak. That test is kept as a general invariant, with that limitation stated in its docstring.
The targeted counter-example is the regression for defect 1.

---

## 2c. D1 escalation ruling: funding model failed, adverse bound adopted (2026-10-03)

`premium-linear-v1` on the real 95-day overlap: 3/20 instruments within tolerance (BTC 3.0%, ETH 1.5%,
HYPE 9.3%), 17 failed (e.g. SNDK 358%, MU 243%, NIGHT and CL with the wrong sign). The advisor's
additions:
- The 3 passes are near-vacuous. The fit (a = 0.099) is essentially a constant of ~0.00005/8h.
- A 95-day single-regime overlap cannot validate any model across 6.8 years.

**All 20 instruments are unvalidated for history.**

Diagnostics:
- 13 crypto instruments pile up at an apparent cap: max realised exactly 0.0001/8h (0.00005/4h).
- The TradFi-like perps (MU, SNDK, XAU, CL) sit at a modal 0 with a two-sided range.

Ruling:
- **`funding-bound-v1`** for every settlement without a realised or forward-validated rate. A long
  pays the 95-day max realised rate, a short pays −min, never a receipt, and G-9 doubles it.
- The bound is frozen in `docs/funding_bound_v1.json` and also used for pre-2026-06-29 holdout funding.
  It is NOT a bound on 2020–2021 funding; that caveat travels in every result.
- A revised model family is allowed only with **forward** validation on P-11 data: an instrument
  needs ≥ 30 days of post-2026-10-03 settlements passing.
- Every validation look is a D1 trial on the audit chain.
- **Standing M4 rule:** a candidate must pass on the bound, and pass again when a validated model
  arrives. A candidate that passes only under the model is discarded.
- The vendor route (VENUE_FACTS §6) is the only way to get regime coverage. That decision is the user's.

## 2d. Cost model: slip-v2 and the random-entry check (2026-10-03)

**The first real-data random-entry run passed vacuously.**
- The account was ruined: final equity 6.57 of 100,000.
- Uncalibrated slip-v1 charged 12–63 bps per side.
- Measured order books show **every instrument at a 1-tick spread** (`docs/spreads_snapshot_2026-10-03.json`).

Ruling: **slip-v2**, with all inputs from closed bars only:
`max(1 tick, Abdi-Ranaldo half-spread over 168 closed bars) + price·Y·σ_1h·√(q/V_prev)`
- Y = 1, an UNCALIBRATED prior.
- The spread estimator is property-tested for no look-ahead (T-1).
- `costs.py` is the single source of cost config; results record its sha256.

Fees: 0.08/0.10 are labelled `UNMEASURED_ASSUMPTION`. They match OKX's en-gb **spot** schedule, and
perp rates are unmeasured. Research may use them; promotion may not.

**The rebuilt check then failed with power:** pre-cost −3.58 ± 0.69 bps over 193,490 trades.
- A/B with stops that never trigger: +0.17 ± 0.87. The drag is the stop rule meeting real dynamics,
  not engine fills.
- The advisor called the original criterion mis-specified, and called my "slip-v2 covers overshoot"
  claim rationalising.

Ruling:
- Real data tests **costs** only; the pre-cost mean there is reported, not gated.
- The ruin floor becomes "zero entries resized or refused", with starting equity sized so that
  cost < 25%.
- Engine bias is gated by the martingale invariant (§2e).
- **Design fact for M4:** a 3% stop costs about 3.6 bps per trade on this hourly data before costs.

## 2e. Martingale invariant and the stop-overshoot term (2026-10-03)

Invariant (permanent: smoke test in the suite, powered run in CI via
`scripts/verify_engine_martingale.py`): on a fat-tailed price martingale (Student-t ν = 3, 48 sub-steps
per bar), zero fees, research slippage on, random entry may not make money through the engine. The gate
is mean net ≤ +0.5 bps with stderr ≤ 0.3, at ~1M trades per configuration.

First powered run, before the fix:
- Net PASSED: −6.29 ± 0.25 bps with stops, −10.13 ± 0.29 without.
- **Pre-cost was +3.76 ± 0.25 bps with stops** (t ≈ 15), against −0.04 ± 0.29 without.

The engine filled intrabar stops exactly at the level, and the net gate passed only because the
slippage, mostly the spread estimator's volatility bias on spread-free synthetic bars, exceeded the
overshoot. That is the double-booking the advisor rejected, and the residual was far above 0.5 bps.
So, per the ruling:
- **Intrabar stops now fill `max(1 tick, k·σ_1h·price)` beyond the level, k = 0.2, frozen.**
- k comes from the measurement (~14.9 bps per stop exit against σ_1h = 80 bps, i.e. 0.19), rounded
  up. Not tuned on real data.
- Gap stops still fill at the real open.
- The pre-cost overshoot is now gated at +0.5 bps alongside net.

## 2f. Chief Advisor closing audit (checkpoint 3), 2026-10-03

Verdict: **complete pending user evidence**, with three items of mine. All three are done:

| # | Finding | Resolution |
|---|---|---|
| M-1 | The holdout (= promotion) run could be priced on assumed fees | **Fixed** (own commit): `evaluate_holdout` is INVALID unless fees are `MEASURED`; every `GateReport` records fee provenance and the cost-config sha. Gate pin 48908f93 → 9066dca9 |
| M-2 | The spread estimator's weight was invisible | **Fixed**: `sanity-random` prints modelled vs measured half-spread per instrument. Finding below |
| M-3 | P-11 is untested PowerShell and the task has never run | The checklist now asks the user to run `-RunNow` now, not wait for Sunday |
| M-4 | Golden-digest moves were bundled into feature commits | Rule kept **from here on**: any further digest move gets its own commit with the stripped-label proof. The earlier moves were each proven to be label or schema only, in their commit messages |
| M-5 | Mixed timeframes are refused | Added to open issues (13) |
| M-6 | SAND short bound is 1%/8h from a single print | Stated below |

**M-2 finding: the Abdi-Ranaldo estimator is on-off noise, heavy on average.** Daily samples over the
3-year research window, 168-hour lookback:
- Its **median is 0** on 13 of 15 instruments; negative means are clamped.
- It reads positive 35–79% of the time, and then large: BTC mean 3.0 bps, p90 8.8 bps, against a live
  half-spread of 0.006 bps (about 500×); HYPE 14.6 bps against 0.057.
- It tracks volatility and return autocorrelation, not spread.

So the cost model is **conservative on average and noisy**, but it does **not** track historical spread
regimes as hoped. Consequences:
- M4: a candidate that fails only on spread cost is flagged in the research log, not silently dropped.
- M7: the PAPER reconciliation (§11.4, one-sided 1.5×) is expected to find the modelled spread far too
  heavy. That is where it gets recalibrated, against real fills.

**M-6:** SAND's 95-day history contains one -0.01 settlement, so `funding-bound-v1` charges SAND shorts 1%
per 8h. SAND shorts are effectively untradable in research. That is honest, not a bug.

**Sequencing ruling: M3 may begin** (pure TA, quant and regime engines; no strategy, no gate
evaluation). Conditions:
- M2 is recorded as complete pending user evidence, not signed off.
- M3 does not touch `src/okxq/backtest/`.
- M-1 lands first (done).
- M4 stays blocked on both user items.
- If the P-11 evidence shows the task did not run, fixing that comes before any further M3 work.

---

## 3. Assumptions recorded in every result

`BacktestResult.assumptions` carries all of these, so they travel with every number:

- the slippage model and its id (`slip-v1` in the CLI: k_vol 0.25, k_impact 0.1, 24-bar volatility)
- fee rates and their provenance
- the cost-stress multipliers and the participation cap
- the adverse funding-boundary rule; the funding notional price is last price, not mark (mark 1h exists
  for 5 instruments only)
- isolated-margin liquidation on last-price high/low, with the whole margin lost
- intrabar ordering rules
- instruments without a bar at T are marked at their last close

---

## 4. Open issues, NOT fixed silently

1. **Survivorship bias.** M1 collected *today's* top 20, with no delisted instruments. Architecture §11.2
   requires delisted symbols for their live period. The research universe is biased in the profitable
   direction until delisted instruments are collected. Whether OKX serves history for delisted instIds is
   **unmeasured**.
2. **Funding coverage covers 5 of 12 walk-forward instruments.** Mark/index 1h exist for 5 instruments.
   The other 7 have no funding before 2026-06-29, so the engine will refuse them. Fix: backfill mark/index
   for every walk-forward instrument (`--symbols-mark-index 20`).
   **RESOLVED 2026-10-03:** mark/index 1h backfilled for all 20 instruments (600,249 joinable pairs). Funding before 2026-06-29 now comes from `funding-bound-v1` (§2c), not a model.
3. **No 4h-funding ground truth for D1.** All 5 mark/index instruments are 8h. The 4h instruments need
   mark/index before the model can be validated on them.
   **SUPERSEDED 2026-10-03:** the 4h instruments now have mark/index, but D1 failed on every class and was replaced by the bound (§2c).
4. **Universe discrepancy.** `VENUE_FACTS.md` V-13 lists 3 instruments at 4h (CL, PUMP, TRUMP) and
   includes AAVE. The final M1 coverage report (2026-10-03 08:30) lists **4** at 4h (adds NIGHT) and no
   AAVE. The universe changed between measurements; V-13 has a dated note added.
5. **Venue parameters unmeasured.** Fee rates (private endpoint; measurable at M6 with the demo key, or
   read by the user from their OKX fee page), tick/lot/min size and MMR (public; probe in
   `scripts/recon_instrument_specs.py`, **whose field names are unverified**). The engine refuses
   UNMEASURED parameters outside tests.
   **PARTLY RESOLVED 2026-10-03:** tick/lot/min size and tier-1 MMR are measured (`docs/instrument_specs.json`, probe field names verified against the raw response). Perp **fee** rates are still unmeasured (sign-off checklist).
6. **Gap-repair tool** (carried forward from M1) is not built. Gaps stay quarantined and the engine
   cancels entries across them.
7. **Sizer is provisional.** The real sizer is M5.
8. **The trial count is honest only through `ResearchProtocol`.** `BacktestEngine.run` is public, so a
   run made outside the protocol is never counted, and G-8's N is understated in the lenient direction.
   M4 research must run only through `ResearchProtocol`. M4's review must check that the trial log
   count matches the research log.
9. **Simultaneous entries at one T_open** compete for margin in inst_id order. That is capacity
   allocation at one instant, deterministic, and not look-ahead, but it is an ordering choice.
10. **2020–2022 spread regime is unmeasured.** The Abdi-Ranaldo estimate is the only historical spread
    signal. M7 PAPER spread telemetry is the first real calibration point.
11. **Tier-1 MMR only** (`docs/instrument_specs.json`). That understates maintenance margin above the
    tier-1 size.
12. **The backfill's summary line is misleading.** It compares manifest rows (OHLCV + funding, closed
    months) with Parquet OHLCV rows (including the in-progress month). On 2026-10-03 that showed a
    "244-row" difference which reconciles exactly (7,183 − 6,939). Not a data defect.
13. **One timeframe per engine run** (closing audit M-5). Mixed timeframes are refused, because bars closing
    at the same T would open at different instants. A strategy wanting a 1d regime with 1h entries must
    resample inside the strategy from its 1h view.
14. **The research slippage model is 1h-only** (Chief Advisor, M4 checkpoint 2). `RESEARCH_SLIPPAGE`
    counts its lookbacks in bars: a 168-bar spread estimate and a 24-bar sigma for impact and stop
    overshoot. All of it was calibrated on 1h bars. On 1d bars the overshoot term would be about √24
    times too large. A 1d run needs its own calibration first, so every M4 candidate runs on 1h bars.

---

## Sign-off checklist

| M2 acceptance criterion | Status |
|---|---|
| Look-ahead test: a peeking strategy is unable to | **PASS** for the strategy view. The engine's own cross-instrument leak (checkpoint-3 #1) is fixed with a targeted regression |
| Corrupting all future data leaves results bit-identical | **PASS** (digest equality, a prefix test, and a two-instrument prefix test; see the process note in §2b about what the latter can and cannot catch) |
| Oracle strategy P&L to the cent incl. fees and funding | **PASS** (19.667 baseline, 19.334 under G-9 stress, hand-computed) |
| Metrics engine vs hand-computed fixtures | **PASS** |
| Gate evaluator vs synthetic curves with known MaxDD/PF | **PASS** (exact boundaries at 15% and 1.5) |
| Holdout test: research code reading the holdout raises | **PASS**. The boundary is not a parameter, and a lenient `FrozenGates` cannot be constructed. Direct Parquet access is a guard-scanned tripwire, not a sandbox |
| Golden-fixture regression test | **PASS** (digest pinned) |
| **Random entry on REAL data has negative expectancy after costs** | **PASS** (2026-10-03, `logs/sanity_random_v3.log`). 15 instruments, 3y to the holdout, 20 seeds, 193,490 trades at $1k. Net −56.2 ± 0.7 bps (user fees) and −46.2 ± 0.7 (low sensitivity). All 30 instrument/side cells lose. Costs reconcile exactly. Impact rises 8.5→167 bps across $1k→$10M. 0 sizing refusals. 44 liquidity outcomes, all on 2022-12-18 (VENUE_FACTS §9). Reported, not gated: pre-cost −11.0 ± 0.7 |
| **D1 funding model validated on the realised overlap** | **FAILED → re-scoped** per ruling (§2c): `funding-bound-v1` in use; model validation is forward-only on P-11 data |
| **Martingale invariant (engine cannot be generous on a martingale)** | **PASS** with the k = 0.2 overshoot term, about 1M trades per configuration. 3% stop: net −10.27 ± 0.26, pre-cost −0.22 ± 0.26 bps. No stop: net −10.13 ± 0.29, pre-cost −0.04 ± 0.29. Independently PASS in CI run #21 |
| **P-11 weekly archive verified to have run** | **USER**: pull, then in an ELEVATED PowerShell run `.\scripts\archive_funding.ps1 -Install` and `.\scripts\archive_funding.ps1 -RunNow` **now**; do not wait for Sunday. Report the `-Status` output: `result: 0 = success` plus a fresh `last success`. The task's 267011 / 1999 values mean "never run", not "failed". The hardened script (b8104c7) is untested PowerShell |
| **Measured perpetual-swap fee rates** | **USER**: read the perp maker/taker from the OKX account fee page with a date (or from the demo key's trade-fee endpoint at M6). Required for M2 sign-off and for any promotion |
| Chief Advisor checkpoint 3 on the built code | **Done**: 10 defects, all addressed (§2b). A final M2 audit is still due after the blocked items run on real data |

### Once data is reachable, in order

```bash
python -m okxq.data.cli backfill --env PAPER --symbols 20 --years 7 --timeframes 1d 1h 5m --symbols-mark-index 20
python -m okxq.data.cli report --env PAPER
python scripts/verify_data_quality.py
python scripts/recon_instrument_specs.py            # then inspect docs/instrument_specs.raw.json
python -m okxq.backtest.cli funding-validate --env PAPER   # exit 3 = ESCALATE to the Chief Advisor
python -m okxq.backtest.cli funding-model --env PAPER
python -m okxq.backtest.cli sanity-random --env PAPER --specs docs/instrument_specs.json \
    --maker <rate> --taker <rate> --fee-evidence "<where/when read>"   # exit 5 = cost model suspect
```
