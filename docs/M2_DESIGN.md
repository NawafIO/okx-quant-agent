# M2 - Backtest Engine & Acceptance Gates: Design Record

Status (2026-10-03): **engine, gates, trial counter, holdout and funding model are built and pass
every acceptance test that can run on synthetic data. M2 is NOT signed off.** The remaining criteria
need real market data, and this cloud environment can't reach OKX (`www.okx.com` returns 403 from the
network policy). See [Sign-off checklist](#sign-off-checklist).

Code: `src/okxq/backtest/`. Tests: `tests/unit/backtest/`, plus two new guard modules in
`tests/guards/`.

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

Frozen gate definitions: `FrozenGates().sha256() = 73cc903097ea6a44702be87481d96002c1dc36369a30e2b3d5d2088e34774ab2`.

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

---

## Sign-off checklist

| M2 acceptance criterion | Status |
|---|---|
| Look-ahead test: a peeking strategy is unable to | **PASS** (`test_a_strategy_that_peeks_at_the_next_bar_is_unable_to`) |
| Corrupting all future data leaves results bit-identical | **PASS** (digest equality, plus a stronger prefix test) |
| Oracle strategy P&L to the cent incl. fees and funding | **PASS** (19.667 baseline, 19.334 under G-9 stress, hand-computed) |
| Metrics engine vs hand-computed fixtures | **PASS** |
| Gate evaluator vs synthetic curves with known MaxDD/PF | **PASS** (exact boundaries at 15% and 1.5) |
| Holdout test: research code reading the holdout raises | **PASS** |
| Golden-fixture regression test | **PASS** (digest pinned) |
| **Random entry on REAL data has negative expectancy after costs** | **BLOCKED**: needs data, measured specs, fee rates |
| **D1 funding model validated on the realised overlap** | **BLOCKED**: needs data |
| **P-11 weekly archive verified to have run** | **BLOCKED**: needs a persistent host |
| Chief Advisor M2 audit (checkpoint 3) | pending until the above |

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
