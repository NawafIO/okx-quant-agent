# M4 design record: strategy engine and first research cycle

## 0. BLOCKERS (owner's items; they block PROMOTION, not research runs)

| ID | Needed from the user | Blocks |
|---|---|---|
| **U-1** | Measured perpetual-swap fee rates, with a date. The 0.08% / 0.10% supplied are OKX's regular-user SPOT rates (docs/VENUE_FACTS.md); research uses them as `UNMEASURED_ASSUMPTION` | every holdout run (`PROMOTION-FEES` is INVALID until fees are MEASURED) |
| **U-2** | P-11 evidence: `.\scripts\archive_funding.ps1 -Status` showing `result: 0 = success` and a fresh last-success time | promotion |
| **U-3** | Written acceptance of **V-14** (survivorship cannot be repaired on OKX), recorded on the audit chain | promotion |

**Status as of 2026-10-03** (owner evidence on the PAPER audit chain):
- **U-1: MEASURED.** Perpetual maker 0.02%, taker 0.05%, read from the owner's OKX account fee & tier page (Regular user) on 2026-10-03; record 1867e2a2c00c, superseding a5a9e9d2a94e, which had no source. Now `costs.FEES_MEASURED_PERP`. Adopting them in research is a Phase-2 cost-model change (§5): applied to every candidate, as new trials.
- **U-2: ACCEPTED on the script's own evidence.** The task was re-registered from the current `archive_funding.ps1`. `-Status` shows: task Ready, S4U logon, wake on; last run 2026-10-03 21:59:55 local with result 0 = success; last success stamp 2026-10-03 19:01:50Z (0.0 days old); next run 2026-10-04 03:00. Record bab8cfe11f8f. It supersedes a93fc9ceea8a (substitute evidence from the 23796ff-era script).
- **U-3: accepted** in the owner's words. Record 27d3491f0e89.

**The expected outcome of M4 is that most or all candidates FAIL.** That is the gates working.

## 1. Survivorship caveat (verbatim on every GateReport entry in the research log)

> SURVIVORSHIP (V-14): the universe contains only instruments still listed in 2026; delisted
> perpetuals (e.g. LUNA, FTT) cannot be obtained from OKX. Results are biased UPWARD by an unknown
> amount, so a pass is weaker evidence than the gates imply. See the per-instrument breakdown and the
> BTC+ETH-only line.

§11.2 ("universe includes delisted symbols for their live period") is **unmeetable on this venue**.
The roadmap's Phase-2 exit, "a strategy passes all gates including the holdout", is read with that
deviation and is not quietly satisfied.

## 2. Frozen research rules (set BEFORE any strategy exists)

| Rule | Value | Why |
|---|---|---|
| Sizing | `ResearchSizing`, pinned `e32046ed…`: 0.5% risk per trade (RC-05), 3x leverage (RC-12). The same for every candidate, never in a grid; its sha is on every trial and GateReport | Sizing is a G-1 lever (commit 4ba9295) |
| Research span | **2020-07-01 .. 2025-09-30**, project-wide. The earliest first 1h bar (BTC, 2019-12-16) plus 180 days of warm-up is 2020-06-13, rounded up to the month boundary because folds step in months. **Corrected at N = 0:** it was first written as 2020-06-01, computed from "2019-12". The first run crashed with an empty first window before recording any trial (`docs/m4_runs/trend_breakout_run0_CRASH.txt`), so no result informed the change | The start is not a per-candidate degree of freedom |
| Instrument entry | An instrument takes part in a window only if its first bar is at least 180 days before the window start | Instruments enter when their data begins plus warm-up |
| Warm-up | A strategy's declared warm-up is at most 180 days at its timeframe. Its decisions from a buffer of exactly that length must equal its decisions from full history (bounded-buffer test) | M3 OBV lesson: infinite-memory indicators |
| Universe (dual filter) | (a) first 1h bar on or before 2022-09-30 (at least 3 years before research end) **and** (b) median daily quote turnover (close x base volume) over the instrument's research-window days of at least $5M (`MIN_24H_QUOTE_VOLUME`, the existing live gate) | Handover §3.6 |
| Constants | **Every numeric constant in a strategy module is a declared parameter**, with its default in the params dict, so G-6 perturbs it even when the grid does not vary it. Each rationale lists them all | A literal escapes G-6 |
| Grids | At most 8 configurations. **At most two grids per family** in Phase 2. The second needs Chief Advisor sign-off BEFORE the run and a rationale stating what the first failure taught and why it is not a re-tune. Every trial counts toward N | "A new grid is a new hypothesis" is an escape hatch |
| Timeframe | Fixed in the rationale commit. Changing it after results counts as a second grid | — |
| Trade counts | Each rationale pre-registers its expected closed-trade count. An INVALID G-3 is a result, never a reason to shorten lookbacks | Stops "shorten N to get trades" |
| N | One trial log per environment, shared across candidates | The honest, conservative denominator |
| Version | sha256 over every source file in `src/okxq/strategy/` and `src/okxq/analysis/`, recorded on every trial | The module alone misses its dependencies |
| Regimes | `allowed_regimes` must be empty until a strategy declares a regime dependency, which spends the revision tripwire (M3_DESIGN §2) | regime.py labels are descriptive |
| Holdout | Exactly once per surviving candidate, after U-1, through `unseal_holdout` on the audit chain. A failure kills the strategy | — |

The universe as measured under this rule goes into `docs/universe_m4.json` before the first run. The
handover's "12 walk-forward instruments" counted data inside the sealed holdout. With the holdout
sealed, the history filter leaves **8**: BTC, ETH, DOGE, NEAR, SAND, SOL, UNI, XRP.

## 3. Strategy layer (`src/okxq/strategy/`, inside the research-isolation guard)

- **Protocol** (§10): `id`, `version`, `timeframe`, `warmup_bars`, `params`, `allowed_regimes`, plus two pure functions:
  - `generate(ctx) -> Signal | None`. `Signal` is `contracts.Signal`, so a stop-loss is required at contract level and stop/target geometry is validated.
  - `exit(ctx) -> bool`. Accepted by the Chief Advisor: pure, with every parameter declared and perturbed. It executes at the next open as a taker. Time stops follow the same rule.
- **Adapter** to the engine's `on_bar`: `Signal` becomes an `OrderIntent` carrying the signal's stop and target.
- **Purity tests:** an identical context gives an identical result. `generate` and `exit` must run with the clock, randomness, `open` and sockets patched to raise. Bounded-buffer equality must hold.
- **Registry:** `(id, version)` maps to a factory. A second registration of an id under a different version raises.

## 4. Candidates

Each gets `docs/strategies/<id>.md`, committed BEFORE its first backtest. It covers the economic
rationale, the timeframe, every parameter, the grid, the expected trade count, the cost expectation
and the outcome that would falsify it.

| id | timeframe | hypothesis |
|---|---|---|
| `trend_breakout` | 1d | Time-series momentum (Moskowitz-Ooi-Pedersen; Liu & Tsyvinski for crypto) |
| `keltner_reversion` | 1h | Short-horizon overreaction. Its ADX filter is a **locally implemented regime gate** (ADX < 20 is roughly RANGE), so the 2-4 week lag fact applies to it |
| `vol_compression_breakout` | 1h | Volatility clustering: range expansion after compression |
| `funding_carry` | — | **NOT EVALUABLE.** funding-bound-v1 never pays a receipt, so the strategy fails by construction outside the ~95-day realised window. A vendor funding archive is what would make it testable. Recorded, not run |

## 5. Chief Advisor checkpoint 2 (after trend_breakout run 1)

- **trend_breakout: DISCARD stands** (`docs/m4_runs/trend_breakout_run1.txt`).
  - The funding bound and the universe stay frozen for the remaining candidates.
  - A bound revision is allowed only as a **Phase-2 cost-model change**. It would be decided before any further candidate runs, apply to all candidates, and carry a new cost-config sha.
  - Logged trials are immutable. trend_breakout would be re-evaluated as NEW trials under the new sha, with run 1 kept beside them.
- **The funding bound is the dominant cost in the research programme.**
  - Attribution (`docs/m4_runs/trend_breakout_run1_attribution.txt`): funding −32.2k, all of it funding-bound-v1, against fees −2.6k and slippage −1.8k.
  - SAND shorts alone account for −26.1k over 15 trades. That traces to a single −0.01 print (M2 finding M-6).
  - This is the concrete cost of having no measured funding history (VENUE_FACTS §6). **Whether to buy an archive is the user's decision.**
- **Attribution comes from stored results.** The runner now prints per-instrument and per-side gross, fees, slippage, funding and net; long-only, short-only and BTC+ETH lines; and the bound's share.
  - The 17 `diagnostic` trials of the one-off re-run stay in N.
  - Any future diagnostic re-run needs a recorded reason.
- **Longs gross-positive on 7 of 8 instruments, shorts gross-negative on the majors.** This reads as survivorship beta (V-14), not breakout edge.
- **Deviations accepted:**
  - All candidates run on 1h bars, with a T-1 test on trend_breakout's daily aggregation (M2 open issue 14).
  - The research start was corrected at N=0.
  - `target_atr = 10` is a declared parameter.
- **Contract-design point for the user:** `contracts.Signal` requires at least one take-profit (§4). That forces a nominal target onto strategies that have none, such as trend following. M4 does not patch the contract; it declares the target as a perturbed parameter instead.

## 6. M4 closing audit: CLOSED as "cycle 1: no candidate passed"

The original advisor was unavailable (API credit error), so the audit was done by a replacement Chief Advisor working from the committed records.

- **Rulings:**
  - M4 acceptance is met; the **Phase-2 exit is NOT met**, so M6 stays blocked.
  - **M5 proceeds now.** It is offline and strategy-independent. It must not change the frozen research sizing.
  - Phase-2 hypotheses may run alongside M5, under the two-grid cap.
  - With N at 1302, G-8 gets harder for every future candidate. **That is never a reason to revisit G-8.**
- **Verified:**
  - No protocol violation.
  - Rationales and code (ebc236c) precede the first trial.
  - No strategy or analysis file changed after the first trial.
  - The gates pin is unchanged since M2.
  - The crash run logged nothing.
  - N = 1302 on a single version and sizing.
  - The holdout was not read.
- **Corrections applied:** see `docs/M4_RESEARCH_LOG.md`.
- **Before any second grid:**
  - The live-parameter and buffer-convergence tests must pass without xfail for that candidate.
  - Removing `bb_k`, or fixing trend_breakout's ATR convergence, is a new version, and its runs are new trials.
