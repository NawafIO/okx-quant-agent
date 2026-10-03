# M2 - Backtest Engine & Acceptance Gates: Design Record

Status (2026-10-03): **engine, gates, trial counter, holdout and funding model are built and pass
every acceptance test that can run on synthetic data. M2 is NOT signed off.** The remaining criteria
need real market data, and this cloud environment can't reach OKX (`www.okx.com` returns 403 from the
network policy). See [Sign-off checklist](#sign-off-checklist).

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
3. **No 4h-funding ground truth for D1.** All 5 mark/index instruments are 8h. The 4h instruments need
   mark/index before the model can be validated on them.
4. **Universe discrepancy.** `VENUE_FACTS.md` V-13 lists 3 instruments at 4h (CL, PUMP, TRUMP) and
   includes AAVE. The final M1 coverage report (2026-10-03 08:30) lists **4** at 4h (adds NIGHT) and no
   AAVE. The universe changed between measurements; V-13 has a dated note added.
5. **Venue parameters unmeasured.** Fee rates (private endpoint; measurable at M6 with the demo key, or
   read by the user from their OKX fee page), tick/lot/min size and MMR (public; probe in
   `scripts/recon_instrument_specs.py`, **whose field names are unverified**). The engine refuses
   UNMEASURED parameters outside tests.
6. **Gap-repair tool** (carried forward from M1) is not built. Gaps stay quarantined and the engine
   cancels entries across them.
7. **Sizer is provisional.** The real sizer is M5.
8. **The trial count is honest only through `ResearchProtocol`.** `BacktestEngine.run` is public, so a
   run made outside the protocol is never counted, and G-8's N is understated in the lenient direction.
   M4 research must run only through `ResearchProtocol`. M4's review must check that the trial log
   count matches the research log.
9. **Simultaneous entries at one T_open** compete for margin in inst_id order. That is capacity
   allocation at one instant, deterministic, and not look-ahead, but it is an ordering choice.

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
| **Random entry on REAL data has negative expectancy after costs** | **BLOCKED**: needs data, measured specs, fee rates |
| **D1 funding model validated on the realised overlap** | **BLOCKED**: needs data |
| **P-11 weekly archive verified to have run** | **BLOCKED**: needs a persistent host |
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
