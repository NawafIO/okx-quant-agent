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
