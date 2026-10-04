# Cycle 2 research log

Trial log **N = 1,302, unchanged**. No cycle-2 trial has run.

## 2026-10-03: intraday_momentum DISCARDED on feasibility (fees-only bound)

- **Pre-registration.**
  - Rationale pinned at SHA-256 `14d217cc8a62…` (docs/strategies/intraday_momentum.md, "Feasibility computation").
  - Script `scripts/c2_feasibility.py` committed and pushed in d83c08c **before** its first run.
  - Rule and cutoff set by the advisor (CYCLE2_DESIGN §5): stop if min(M_u, M_k) > 0.55. The 0.55 is a **judgment**: the advisor's [Guessing] mapping of one equity paper's R² to a hit rate, not a measured bound. The discard is valid because the rule was pre-registered, not because 0.55 is known to be right.
- **Run.** One run. Output in `docs/c2_runs/feasibility_fees_only.txt`, result on the PAPER audit chain (`c2_feasibility`). It is signal-free: magnitudes only, nothing selected, no trial logged.
  - The audit record is `a43efba32fea…`, on the **cloud container's** chain. That chain is git-ignored and is deleted with the container, so the committed output file is the durable evidence. A correction: an earlier draft of this line said the owner records live on the owner's machine. **They do not.** U-1..U-3 and the trial log were on this container's chain as well. Both are now preserved in `docs/ledger/` (see its README).

| instrument | days | E\|r\| 22→23h (bp) | break-even hit rate p* (all days / k = 0.5 days) |
|---|---|---|---|
| BTC | 1,918 | 39.4 | 0.627 / 0.624 |
| ETH | 1,918 | 53.9 | 0.593 / 0.588 |
| XRP | 1,918 | 62.5 | 0.580 / 0.580 |
| UNI | 1,838 | 74.1 | 0.567 / 0.565 |
| DOGE | 1,907 | 75.6 | 0.566 / 0.564 |
| SAND | 1,641 | 78.7 | 0.564 / 0.560 |
| SOL | 1,711 | 79.4 | 0.563 / 0.561 |
| NEAR | 1,743 | 85.5 | 0.558 / 0.557 |

Median p*: M_u = 0.5668, M_k = 0.5648. min = 0.5648 > 0.55, so **DISCARDED**.

**Reading.**
- This is the **lower** bound: fees only, with no spread, no impact and no stop overshoot. The full check could only raise p*.
- **Not one instrument** clears 0.55. The most favourable, NEAR, needs 55.7% directional accuracy at a one-hour horizon just to pay the 10 bp taker round trip.
- p* assumes **equal-sized wins and losses** (both E|r|). Under that assumption it judges the cost structure rather than the hypothesis, and says nothing against Gao et al. or Shen et al. The exact condition is about expected edge per trade, (2p − 1)·E|r| > c; a signal whose wins are larger than its losses could clear it at a lower hit rate. The pinned rule did not allow for that, and the one-hour hold with a 1–2 ATR stop makes large asymmetry unlikely. [Likely]

**Volume disclosure (report-only, as pre-registered).**
- Mean share of daily base volume by UTC hour: the peak is 14:00 (6.3%), with 13:00–16:00 all ≥ 5.6%. **22:00 is 3.1%, the second-lowest hour of the day**, after 21:00 at 2.8%.
- The window was chosen to avoid funding settlements. It sits in the session the cited mechanisms say should be **weakest**.
- A high-volume variant would be this family's second grid, which needs advisor sign-off and a new rationale. It would face the same fee arithmetic with a larger E|r|. It was **not** run, and it is not proposed on the strength of this table alone.

**Lesson for candidate design (advisor's restatement).**
- Under measured taker fees, a candidate's expected edge per round trip must exceed about 10 bp plus spread.
- Short-hold designs with symmetric payoffs need a directional hit rate that we judge implausible. That is a judgment, not a published bound.
- Asymmetric-payoff designs (trend followers win at hit rates below 0.5) are not excluded by this arithmetic.
- Longer holds move the burden onto funding. In M4, funding-bound-v1 was the dominant cost of multi-day holds, and it is still a bound, not a measurement.
- Maker execution would halve the fees, but the engine does not model it (no queue, no fill probability).

## 2026-10-04: Track 1 lower-bound baseline. session_orb and impulse_continuation DISCARDED

- **Pre-registration.** Spec `docs/c2_baseline_spec.md`, SHA-256 `924be378…`, pinned in `scripts/c2_baseline.py` and pushed in 8d3a68a **before** the first run. Timing rules were checked on synthetic data only.
- **What ran.** Signal-free random-entry brackets through the engine and the D3 risk gate. 120 runs:
  - decision runs: lower-bound costs (LB), OBSERVE_HALTS, 20 seeds;
  - reported alongside: LB under ENFORCE, and current slip-v2 (SV2) under OBSERVE_HALTS, 5 seeds each.
- **Not trials.** N is still **1,302**; the script checks the trial log before and after.
- **Evidence.**
  - Output: `docs/c2_runs/baseline_summary.txt` and `baseline_runs.json`.
  - Audit record `ce5bf6f80268…` on the container's chain, preserved in `docs/ledger/`.
  - Gate commits: 6c202e9, c212d0a, 0b66d8e, bd3342d, 0e01817; each advisor-reviewed, PROCEED.

### Decision runs (LB, OBSERVE_HALTS, 20 seeds). Rule: discard if the required edge exceeds 0.15 R for both brackets

| candidate | target | mean R per trade | SE | required edge | trades/seed | rule |
|---|---|---|---|---|---|---|
| session_orb | 1.5 ATR | −0.1941 | 0.0022 | **0.194 R** | 8,068 | fails |
| session_orb | 2.0 ATR | −0.1980 | 0.0024 | **0.198 R** | 7,790 | fails |
| impulse_continuation | 1.5 ATR | −0.2255 | 0.0048 | **0.226 R** | 3,172 | fails |
| impulse_continuation | 2.0 ATR | −0.2328 | 0.0056 | **0.233 R** | 3,098 | fails |

**Both candidates are DISCARDED with no trials.**
- **Robust to sampling noise.** The closest bracket is 0.044 R above the cutoff, about 20 standard errors. But the standard error covers only random-draw noise. **The margin is about the size of one cost-model term, the stop overshoot (~0.045 R)**, which is frozen from measurement, not tuned (advisor wording).
- It is a **lower bound**: 1-tick spread, random entries that avoid the high-range bars real signals trade after, and halts not enforced. Under the current slip-v2 the requirement is 0.25–0.29 R.

### What the edge requirement is made of (reading; [Likely])
- Fees are 10 bp on a stopped round trip and 7 bp when the maker target fills. ATR_1h is 0.76–1.68%, so that is about 0.06–0.13 R per trade.
- The stop overshoot of 0.2 σ is about 0.125 R on each stop exit, and about 36% of exits are stops.
- Impact slippage applies to the entry and to every taker exit.
- [Guessing] **One illustrative path** to 0.19 R in hit-rate terms: moving one trade from a stop (about −1.1 R) to the target (about +1.5 R) gains about 2.6 R. A signal would therefore have to turn roughly 7–8% of all trades from stops into targets, lifting the target rate from about 18% to about 25%, just to break even. Improving the time exits is another path.

### Exit mix, with an erratum
- **Erratum.** The pinned script labelled strategy exits (the 6-bar cap and the pre-settlement forced exit, engine reason `signal`) as **"other"** instead of "time". A patch meant to fix that did not apply, and I missed it.
- A seed-0 re-run lists every reason: `signal` 3,820, `stop` 2,767, `stop_residual` 23, `stop_gap(_residual)` 2, `take_profit` 1,447. There were no liquidations and no window or series ends. The time exits held 2–5 h, as designed.
- So **read "other" in `baseline_summary.txt` as "time".** The decision metric, mean R, never uses exit labels and is unaffected. The script is corrected in the next commit; the pinned spec is unchanged.
- **Corrected exit mix (decision runs):**
  - session_orb at 1.5 / 2.0 ATR: time 47 / 54%, stop 35 / 36%, target 18 / 11%;
  - impulse_continuation: time 45 / 52%, stop 36 / 37%, target 19 / 11%.
- Against the driftless estimate (CYCLE2_DESIGN §9.2a): stops as predicted, targets a little lower, time exits a little higher.

### Same-bar ambiguity and the 5m check (report-only)
- Ambiguous stop exits, where the stop bar also traded through the target, are **0.5–1.2%** of stops in the decision runs.
- For the 793 ambiguous exits on BTC, ETH and SOL, the 5m path shows:
  - target first: 349 (44%);
  - stop first: 388 (49%);
  - both inside one 5m bar: 56;
  - 654 more were on instruments with no 5m data.
- So the engine's stop-first rule mis-scores **349 of the 737 resolvable ambiguous trades, about 47%**. At about 1% of stops, with about 2.6–3.1 R per mis-scored trade, this moves mean R by about 0.004 R (advisor-checked). It cannot change either verdict. It covers only the years where 5m data exists.

### Risk-gate measurements (context for the owner's RC-13 decision)
- **OBSERVE_HALTS.** With halts not enforced, **RC-13 refused 41% of evaluated entries for session_orb and 28–29% for impulse_continuation** at zero edge. A low-win-rate bracket hits three losses in a row constantly, as CYCLE2_DESIGN §9.4 predicted.
- **ENFORCE.** At zero edge, the RC-11 drawdown latch fires within the first **58–71 trades per seed**, out of 3,000–8,000 opportunities, and trading stops for the rest of the five years. RC-13 then shows only 0.3–0.4% refusals, because the RC-01 latch blocks first.
- RC-16 (no regime label) and the other refusals are recorded per run in `baseline_runs.json`.

### Consequence
- Track 1 has no surviving candidate. Cycle 2 has run **no** trials; N = 1,302.
- **The RC-13 decision is deferred, not moot.** The feasibility baseline itself runs under RC-13. The count-versus-size policy question (CYCLE2_DESIGN §9.4) is to be decided **before the next candidate's baseline spec is pinned** (advisor wording).

### Disclosures added after the advisor's closing check

**1. Funding was NOT zero, despite "zero by construction".** [Certain]
- The pinned spec requires any nonzero funding to be reported. It is nonzero in **all 120 runs**: about −6 (ENFORCE) to −310 (OBSERVE) per run.
- **Diagnosed** (seed 0, report-only re-run; `scripts` diagnostics not committed):
  - every funded trade was on **XRP-USDT-SWAP**, mostly in 2020, when it traded only about 0.3–1.4 M XRP per hour;
  - the engine's participation cap (10% of the previous bar's volume) let a 33k–458k XRP position leave only in pieces;
  - so a forced exit decided at 07:00 spilled into the 08:00 bar and paid that settlement, and one stop took 6 hours to fill;
  - 29 of 2,002 XRP trades in seed 0;
  - there are no missing bars in any series (checked), so the gap hypothesis is ruled out.
- **Not a bug:** this is the liquidity model working. "Zero funding by construction" holds only when exits are not capped.
- About 1e-4 R per trade. **The verdict is unaffected.**
- **Before any Track-1 trial:** the rationale and CYCLE2_DESIGN §9.1 must say "zero by construction except liquidity-capped exits". The planned zero-funding test must assert exactly that, not a blanket zero. It did not exist for this run.

**2. Equity collapsed in the OBSERVE_HALTS runs.** [Certain] With halts not enforced, a zero-edge bracket keeps trading as equity bleeds:
- final equity was **$10–$81 of $100k for session_orb** and **$1.2k–$4.6k for impulse_continuation**;
- most decision-run trades were sized from a small fraction of the intended equity, and lot and minimum-size refusals (SZ-3, RC-12d) appear at small equity.

**Does that flatter the requirement? It understates it.** Seed-0 mean R by equity at entry (report-only re-run):

| candidate | equity ≥ $50k | $5k–50k | $500–5k | < $500 | all |
|---|---|---|---|---|---|
| session_orb | **−0.301** (n=566, se 0.04) | −0.180 (2,493) | −0.247 (1,847) | −0.136 (3,153) | −0.194 |
| impulse_continuation | **−0.217** (n=709, se 0.04) | −0.213 (2,140) | −0.095 (338) | — | −0.226 |

- Trades sized at full equity need **more** edge than the average.
- Caveat: the full-equity trades are also the earliest (2020–21), so equity level and calendar period are confounded here.
- Either way, the discard is if anything **conservative** (advisor, [Likely]: impact shrinks with size, and high-priced lots such as BTC, with the highest fee-in-R, drop out at small equity).

**3. Deviation from CYCLE2_DESIGN §9.5.**
- §9.5 required both rationales to be pinned before their baseline. Both still read "Not yet pinned".
- **What was pinned, before the run, was the bracket set itself**, via the baseline spec hash `924be378`. So no result could steer the brackets.
- The spec's sentence "its pinned grid contains both targets" was therefore inaccurate; the rationales' grids were drafts. Recorded as a deviation that the spec pin covers (advisor ruling).
