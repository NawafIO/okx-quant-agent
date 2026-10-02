# OKX Quantitative Trading System - Development Roadmap

Date: 2026-10-02
Phase: **1 of 4 - design only.** This roadmap is a plan, not an authorisation.
Companion: `SYSTEM_ARCHITECTURE.md` (authoritative on design; its §2 rulings govern this document)
Status: **Chief Advisor verdict 2026-10-02: RECOMMEND TO APPLY**, conditional on P-1..P-9 and audit
findings F-1/F-2 (both applied). **Awaiting user authorisation.** See `SYSTEM_ARCHITECTURE.md` §24

> **Sequencing principle.** Data first, then the thing that tells us whether any of this works
> (the backtester with its gates), then strategies, then the safety middleware, and only then
> anything that can send an order. **Nothing that can place an order is built before the Risk Engine
> is complete and property-tested.** The ordering is deliberately hostile to the temptation to trade
> early.

---

## Phase map

| Phase | Name | Environments | Gate to exit |
|---|---|---|---|
| **1** | Inspect / Analyze / Design | none | Advisor verdict + user authorisation |
| **2** | Foundation & Research (M0-M5) | none (offline) | A strategy passes §11.3 G-1..G-9 + sealed holdout |
| **3** | Simulated Operation (M6-M9) | DEMO, then PAPER | §11.4 criteria met + 30 days stable |
| **4** | Live (NOT AUTHORISED; separate decision) | LIVE | Written human sign-off; `PHASE` constant raised |

`PHASE = 2` today (`src/okxq/phase.py`), and `LIVE_UNLOCK_PHASE = 4`. LIVE is unreachable until that
constant is deliberately raised in source, **and** LIVE credentials are provisioned, which does not
happen in Phase 2 or 3. See architecture §6.4.

---

## Prerequisites (blocking - must all be satisfied before M1)

| ID | Prerequisite | Why / status |
|---|---|---|
| **P-1** | **Install Python 3.12+** (`winget install Python.Python.3.12`) | **BLOCKING. No Python runtime exists on this host** (architecture W-3: `python.exe` on PATH is the Microsoft Store stub; no `pip`, no `py`, empty `PythonCore` registry). Ruling X-1 keeps the Python stack and makes this a prerequisite |
| **P-2** | Create and activate a project venv; pin every dependency; commit the lockfile | Reproducibility; also R-5 (CCXT pinning) |
| **P-3** | `git init` the project and add `.gitignore` (`.env*`, `state/`, `data/`, `audit/`, `__pycache__`) | Needed for strategy versioning by git sha (architecture §10). **Requires user's go-ahead** - Phase 1 initialises nothing |
| **P-4** | Secrets mechanism decided and tested with a dummy value; pre-commit secret scan installed | Architecture §20.3. Must work before any key exists |
| **P-5** | **OKX account with demo/simulated trading available; demo API key generated** (trade scope, demo only) | Needed at M6. **No LIVE key is created at any point in Phase 2 or 3** |
| **P-6** | Telegram bot created via BotFather; the single authorised user id recorded | Needed at M8 |
| **P-7** | Advisor verdict RECOMMEND TO APPLY + user authorisation | This roadmap is not self-authorising |
| **P-8** | Rulings on architecture §23 Q-4 (risk budget numbers) and Q-5 (HITL permanence) | **SATISFIED - both ruled by the Chief Advisor 2026-10-02; see architecture §23** |
| **P-9** | LLM API credential (for subsystems 3, 6, 15, 17 narration) | Needed at M8 (audit finding **F-2**). **Its absence never blocks trading** - the system degrades closed to deterministic rules (architecture §5.2) |
| **P-10** | **Host clock resync + adapter boot-time skew check** | **NEW, discovered at M1. BLOCKING FOR M6.** The host clock measured **~199 s (3m19s) ahead** of OKX's server, consistently across three latency-compensated samples (`docs/VENUE_FACTS.md` §5). Harmless for M1's unsigned public requests; **OKX rejects *signed* requests far below that drift**. Two parts: (a) `w32tm /resync` elevated plus Windows Time service set to automatic - **a change to the user's machine, their call**; (b) an adapter boot check refusing authenticated operations beyond the venue's tolerance, **that tolerance to be measured at M6, not assumed** |

---

## Phase 2 - Foundation & Research (no network order path exists)

### M0 - Environment & scaffold - **COMPLETE 2026-10-02** (commit `32ea0da`)

**Verified:** ruff clean · ruff format clean · `mypy --strict` clean (22 files) · **85 tests passing
(31 of them guards)** · **97% branch coverage** · `scripts\verify.ps1` green end to end.

**Environment as built:** Python 3.12.10 (installed via winget, satisfying P-1 - the host had no
Python runtime at all), uv 0.12.22, `.venv` with 53 packages, `uv.lock` committed (P-2), git
repository initialised with `.gitignore` and `.gitattributes` (P-3).

| Prerequisite | Status |
|---|---|
| P-1 Python 3.12+ | **DONE** - 3.12.10 |
| P-2 venv + pinned deps + committed lockfile | **DONE** - `uv.lock`, 53 packages; `uv sync --locked` in CI fails on drift (also R-5's control) |
| P-3 git init + `.gitignore` | **DONE** |
| P-4 secrets mechanism + redaction | **DONE** - env-var sourced, central redaction, 8 guard tests; **pre-commit secret scan still outstanding** |
| P-5 OKX demo account + demo key | **NOT STARTED** - not needed until M6 |
| P-6 Telegram bot + authorised user id | **NOT STARTED** - not needed until M8 |
| P-7 advisor verdict + user authorisation | **DONE** |
| P-8 rulings on Q-4/Q-5/Q-6 | **DONE** |
| P-9 LLM API credential | **NOT STARTED** - not needed until M8 |

**Deliverables:** P-1..P-4 satisfied; `pyproject.toml` with pinned deps; package skeleton per
architecture §7; `contracts.py` implementing §4 in full; `phase.py` with `PHASE = 1`;
`env/profiles.py` with the three profiles and **no default branch**; structured logging + audit
chain skeleton (§20.2); pytest + Hypothesis + coverage + ruff + mypy (strict) wired; CI script.

**Acceptance:**
- `python -c "import okxq"` succeeds; `mypy --strict` clean; `ruff` clean.
- Contract round-trip tests pass; `Decimal`-from-string enforced (D-1 asserted by test).
- **Guard test passes: constructing the LIVE profile raises `LiveTradingLockedError`.**
- Guard test passes: a record whose `env` mismatches raises `EnvironmentMismatch` (D-2).
- Audit chain verifier confirms a 3-record chain and **detects a deliberately tampered record**.
- Secret-redaction test: a known dummy secret cannot appear in any emitted log or audit record.

**Risk:** low. **Exit:** scaffold green in CI. **EXITED.**

> **Carried forward to M1:** the pre-commit secret scan (part of P-4) is not yet installed. It is
> listed here rather than quietly dropped. Nothing in M0 handles a real credential, so the gap is not
> yet load-bearing - but it must close before P-5 creates the first demo key.

---

### M1 - Historical Data Pipeline + empirical venue reconnaissance
*The first milestone that touches the network. Public endpoints only. No credentials used.*

**Deliverables:** OKX adapter read-only surface (`load_markets`, `fetch_ohlcv`, funding history) with
rate-limit governance and backoff; paginated backfill, idempotent and resumable; the full validation
battery (§12) with quarantine-not-repair semantics; Parquet writer (partitioned, atomic temp+rename);
DuckDB registration and coverage manifest; a gap report.

**Funding-rate history is in scope for M1** (audit finding **F-1**). M2's cost model accrues
perpetual funding at every real funding timestamp (architecture §11.2) and §20.1 stores it; without
historical funding rates **M2 cannot meet its own acceptance criteria**. Backfill historical funding
rates per symbol alongside OHLCV, validate them on the same expected-interval grid, and measure their
retrievable depth as part of Q-1.

**Reconnaissance tasks - the real purpose of M1 (resolve, do not assume):**
- **Q-1: measure actual OKX OHLCV history depth per timeframe and per symbol**, and the achievable
  throughput under rate limits, across both the recent-candles and history-candles endpoints and
  CCXT's pagination over them. **Record measured facts in a `docs/VENUE_FACTS.md`, with dates.**
- **Q-3: attempt a TA-Lib install on this host** (needs a C toolchain on Windows); if it fails,
  confirm `pandas-ta` covers the required indicator set.
- Measure clock skew vs. OKX server time; characterise the error-code taxonomy actually observed.

**Acceptance:**
- >= 20 liquid USDT-perp symbols backfilled on 1h and 1d, plus >= 5 on 5m.
- **Funding-rate history backfilled for every symbol in the research universe, gap-accounted on the
  funding-interval grid** (F-1).
- Zero validation failures silently passed; every gap present in the manifest.
- Re-running a completed backfill is a verified no-op (idempotency).
- Backfill survives a forced kill mid-run and resumes correctly from the manifest; **no torn Parquet
  file is produced** (tested by killing during a write).
- `VENUE_FACTS.md` answers Q-1 with numbers, **for OHLCV and funding rates both**. **Explicit decision
  recorded: is the available depth sufficient for the §11.3 walk-forward, or is a secondary backfill
  source now a required milestone?**
- A DuckDB query over the full store returns in a stated time budget.

**Risk:** **MEDIUM - this is where the project can discover a structural problem (R-9).** If OKX
depth is too shallow, §11.3's protocol is not executable as designed and the roadmap changes here.
Better to find that at M1 than at M5.

**Exit:** multi-year (or measured-maximum) data on disk, queryable, with honest gap accounting.

---

### M2 - Event-Driven Backtesting Engine + acceptance gates
*The self-testing core. Built before any strategy, so no strategy is ever judged by a lenient engine.*

**Added at M1 - funding reconstruction (advisor ruling, `docs/VENUE_FACTS.md` §2).** OKX retains only
**~3 months** of realised funding, but M2's cost model accrues funding across a multi-year
walk-forward. M1 collected mark and index 1h candles (6.76y depth) as the inputs. M2 must:
build a modelled funding series from the mark/index premium; **validate it against realised funding
over the ~3-month overlap** - reproducing *cumulative* funding drag within a stated tolerance
(provisional: 20% relative error plus sign agreement on the large majority of intervals, finalised
from the measured error distribution); and **escalate to the Chief Advisor if that tolerance cannot
be met**. Do not assert OKX's funding formula from memory - the empirical check is the gate.
**G-9's 2x cost stress applies to modelled funding too**, which is what backstops the model error.

**Deliverables:** chronological event loop with the **right-truncated history view** (look-ahead
prevented structurally, §11.1); realistic cost model (real fee tiers, funding accrual, scaled
slippage with a tick floor); intrabar stop/target resolution with the **pessimistic tie rule**;
gap-aware stop fills; partial fills; liquidation modelling; the §11.3 gate evaluator G-1..G-9; the
walk-forward harness; **the trial counter** (the denominator for G-8); the sealed-holdout mechanism
(enforced in code - the holdout range is unreadable to research code).

**Acceptance:**
- **Look-ahead test: a strategy that peeks at the next bar must be *unable* to do so** - the
  truncated view makes the data unavailable, and the test asserts the resulting failure.
- Corrupting all future data leaves a completed backtest's results **bit-identical**.
- A deliberately profitable oracle strategy on synthetic data produces the arithmetically expected
  P&L to the cent, including fees and funding.
- A random-entry strategy on real data produces **negative** expectancy after costs (if random
  trading looks profitable, the cost model is wrong - this is the engine's own sanity check).
- Metrics engine (§17) validated against hand-computed fixtures.
- Gate evaluator verified against synthetic equity curves with known MaxDD/PF.
- **Holdout test: research code attempting to read the holdout range raises.**
- Golden-fixture regression test established.

**Risk:** **HIGH severity if wrong, and silently so.** An over-optimistic backtester produces
confident garbage for the entire remainder of the project. Hence the oracle and random-strategy
tests - both must hold.

**Exit:** the engine is trusted. Every number after this point depends on this milestone being right.

---

### M3 - Analysis engines
**Deliverables:** TA engine as pure functions (§9.2); quant engine (§9.1) incl. the correlation
matrix and clustering that RC-07 consumes; deterministic regime classifier (§8.3).

**Acceptance:**
- **Every indicator passes the T-1 no-look-ahead property test** (`f(s[:n])[-1] == f(s)[n-1]`). An
  indicator that fails is removed, not patched around.
- Indicators match a reference implementation within a stated tolerance.
- Regime classifier labels known historical periods correctly (2021 bull = TREND_UP, 2022 = TREND_DOWN,
  Mar 2020 / known flash crashes = CRISIS) - a falsifiable check, not an eyeball.
- Correlation clustering demonstrates the R-8 point empirically on real data (alts cluster with BTC).

---

### M4 - Strategy Engine + first research cycle
**Deliverables:** `Strategy` protocol and versioned registry (§10); 3-4 candidate strategies, **each
with a written economic rationale before any backtest is run**; walk-forward research runs; a
research log recording **every configuration tried** (G-8 denominator).

**Acceptance:**
- Purity test: `generate()` is deterministic and I/O-free; identical context yields identical signal.
- **No `Signal` can be constructed without a stop-loss** (contract-level, asserted).
- Every candidate evaluated against G-1..G-9; all discards recorded with the failing gate.
- Sealed holdout run **exactly once** per surviving candidate, after all tuning is frozen.
- **Honest outcome recorded.** If nothing survives, that is the result and it is reported as such.

> **Expectation management.** Per architecture R-1 and R-14: the most likely outcome of M4 is that
> most or all candidates fail. That is the gates working, not the project failing. **The failure mode
> to guard against is loosening a gate to let a favourite strategy through** - which is why the
> thresholds and the trial counter are fixed in M2, before any strategy exists.

**Exit:** at least one strategy passes all gates **including the holdout**, or Phase 2 iterates on new
hypotheses. **No progression to M6 without a passing strategy** - there is nothing to validate
operationally otherwise.

---

### M5 - Risk Engine + Portfolio Manager
*The safety-critical milestone. Pure, offline, no network, no credentials.*

**Deliverables:** sizing formula §13.2 with all post-sizing validations; the RC-01..RC-12 battery
§13.3; qualitative-input gating §13.4 (risk-reducing only); Portfolio Manager §14 with open-risk,
cluster exposure, equity curve and high-water mark; kill-switch module §19.3 incl. the filesystem
sentinel and restart-persistence.

**Acceptance (the strictest set in the project):**
- **100% branch coverage on the Risk Engine.** Not negotiable.
- Property tests (Hypothesis) proving, over wide random input:
  - actual risk **never** exceeds the risk budget;
  - quantity is **never** rounded up;
  - `qty < min_size` yields **REJECT**, never an inflated size;
  - malformed / `None` / `NaN` / zero-stop-distance input yields **REJECT** (default deny);
  - a stop on the wrong side of entry yields REJECT;
  - estimated liquidation always sits beyond the stop, or REJECT.
- Every RC check has a passing and a failing fixture with asserted observed/limit values.
- **No-bypass test: there is no flag, config value or call path that skips a risk check.**
- Guard test (R-4): sizing and execution modules reference **no** LLM-sourced field.
- Kill switch: engages on each trigger; **engagement survives process restart**; fails safe when its
  own state is unreadable.
- Daily-loss and drawdown halts fire at the exact configured thresholds in UTC, with the day boundary
  unambiguous.

**Risk:** this module is the difference between a bounded loss and an unbounded one. **Exit:** full
green, reviewed line by line.

---

## Phase 3 - Simulated Operation

### M6 - OKX Adapter (full) + DEMO integration
*First milestone that places an order - against OKX's simulated venue, with a demo-only key.*

**Deliverables:** private REST and WebSocket surfaces; order placement/cancel/query; typed error
taxonomy; WS heartbeat and resubscribe; Execution Engine §15 with deterministic `client_order_id`,
the UNKNOWN-state query-before-act policy, protective-order confirm-or-close, and reconciliation.
**Resolve Q-2 empirically** (exact CCXT sandbox / OKX simulated-trading mechanism, demo key
requirements, demo symbol availability) and record it in `VENUE_FACTS.md`.

**Acceptance:**
- Full lifecycle exercised on DEMO: limit, market, stop, reduce-only; partial fill; cancel; reject.
- **Idempotency proven**: a forced duplicate send of the same `client_order_id` results in **one**
  position, verified at the venue.
- **Chaos suite passes**: network killed mid-send; process killed mid-send; venue 5xx storm; stale
  feed. Every case ends reconcilable with **zero duplicate positions and zero orphaned orders**.
- Protective-order failure path verified: a rejected stop causes the position to be closed
  immediately and a CRITICAL alert.
- Boot reconciliation detects an injected divergence and transitions to `HALTED` (no silent
  auto-correct).
- Guard test still passes: **LIVE remains unreachable.**

**Risk:** HIGH - first real order path. Mitigated by M5 completing first and by DEMO-only credentials.

---

### M7 - PAPER sandbox + the 50-trade gate
**Deliverables:** PAPER fill engine §6.3 (book-walking, trade-through limits, latency injection, real
fees and funding, adverse-selection penalty); live-feed paper runtime; slippage and latency telemetry;
cost reconciliation report §17.

**Acceptance - per architecture §11.4 (and read as an *infrastructure* gate, ruling X-5 / R-2):**
- **>= 50 closed paper trades.**
- p99 decision-to-order latency within budget; **zero missed bars.**
- Realised paper slippage within **1.5x** of the backtest assumption - **or the backtest is re-run
  with corrected costs and must still pass all of §11.3.**
- 100% of orders reach a terminal state; **zero** unknown/orphaned.
- **Zero** instances of a position without a confirmed protective stop.
- **Zero** unexplained reconciliation divergences; **zero** unhandled exceptions.
- Kill-switch drill executed live, flattening verified.

> **This gate does not establish profitability and must never be reported as doing so** (R-2). Paper
> P&L at n=50 is noise. The gate establishes that the machine is correct and that our cost
> assumptions are not fantasy.

---

### M8 - Interactive interface (dashboard + Telegram HITL) + News & Sentiment
**Deliverables:** Streamlit dashboard §19.1 with the mandatory colour-coded environment banner;
Telegram bot §19.2 - read-only conversational queries plus the trade-approval gate with all five R-3
controls; alert classes with dedup and rate limiting; LLM query layer bound to **read-only tools only**.

**Subsystem 6 (News & Sentiment, architecture §9.3) is built here** (audit finding **F-2** - it
previously had no milestone home, while M8's acceptance criteria assumed it existed). Deliverables:
news and exchange-announcement ingestion; the S-1 prompt-injection quarantine heuristic; the LLM
scoring path emitting a schema-clamped `SentimentScore`; the **deterministic** operational-override
rule of S-2 (maintenance windows and delisting notices parsed from the structured announcement API,
never from an LLM's reading); and the §13.4 risk-reducing-only wiring into the Risk Engine.

**Acceptance:**
- Dashboard is localhost-bound and read-only; env banner correct in all three profiles.
- **R-3 controls each tested independently:** a non-allowlisted sender is ignored and logged; a
  forged/unsigned callback is discarded; a replayed or double-tapped callback is a no-op; an expired
  proposal cannot execute; on approval the full risk battery re-runs against current prices and a
  moved market causes the trade to be dropped with a reason given to the user.
- **Penetration check: possession of the bot token alone is insufficient to cause a trade.**
- LLM tool registry contains **no** execution, sizing or kill-switch tool - asserted by test.
- Prompt-injection probe in ingested news produces no capability invocation (S-1); a quarantined
  article is excluded from scoring and logged.
- `SentimentScore` is clamped to `[-1,+1]` and schema-validated; an out-of-range or unparseable LLM
  response is **discarded, never retried into compliance**, and the deterministic default is used.
- **Degrade-closed test: with every LLM provider unreachable, the system continues trading on
  deterministic rules with sentiment neutral** - LLM outage is not an outage (architecture §5.2).
- Sentiment demonstrably **cannot increase** exposure: a maximally bullish score leaves position size
  bit-identical (§13.4).
- End-to-end: signal -> proposal -> Telegram card showing every risk check -> APPROVE -> paper fill ->
  dashboard and audit trail both updated.

---

### M9 - Adversarial validation + 30-day stability run
**Deliverables:** adversarial battery §18 (shuffled returns, synthetic random walk, parameter
surfaces, regime partitions, Monte-Carlo trade resampling, 2x/5x cost shock, gap injection,
extreme-event replay); LLM adversarial critique for human review; a 30-day continuous PAPER run;
operational runbook (restart, reconcile, kill-switch, incident response); backup **and tested restore**.

**Acceptance:**
- **The strategy fails on shuffled returns and on synthetic random walks.** If it "works" on noise,
  it is fitting noise and is discarded regardless of every earlier gate.
- Survives 2x cost shock; 5x result documented.
- Monte-Carlo drawdown distribution computed; the **95th-percentile** drawdown is reported as the
  planning figure, not the single historical path.
- 30 days continuous PAPER operation: uptime target met, zero unexplained halts, cost reconciliation
  within tolerance.
- Runbook validated by executing each procedure; restore from backup verified.

**Exit:** a complete, evidenced promotion package - **and a Phase 4 decision that is explicitly the
user's, informed by architecture R-14.**

---

## Phase 4 - LIVE (NOT AUTHORISED BY THIS DOCUMENT)

Listed for completeness. Requires a separate decision with full evidence. Minimum conditions:

1. All M0-M9 acceptance criteria met and evidenced in the audit trail.
2. `PHASE` constant deliberately raised in source, via reviewed commit.
3. LIVE API key created **at that moment**: trade scope only, **withdrawal permission disabled**, IP
   allowlisted.
4. Kill switch armed by default; disarmed only by a deliberate audited human action.
5. HITL approval **mandatory** (architecture Q-5 proposal).
6. Capital sized such that a total loss is **immaterial** to the user, with risk per trade and the
   daily halt scaled accordingly.
7. A written, audited human sign-off recorded.
8. A pre-agreed shutdown rule: a stated drawdown at which the system is switched off and re-examined,
   **decided before the first live order, not during the drawdown.**

> **R-14 restated, because it is the most important sentence in this roadmap.** Most retail
> algorithmic crypto systems lose money after fees, funding and slippage. Every gate in this plan is
> designed to make that outcome *visible before capital is committed* rather than after. A plan that
> reaches Phase 4 is not a plan that has proven profitability - only one that has not yet been
> falsified. **The correct and most likely good outcome of this project is a well-engineered system
> that concludes, with evidence, that no tested strategy has a reliable edge.**

---

## Critical path and sequencing constraints

```
P-1..P-4 -> M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8 -> M9 -> [Phase 4 decision]
                    |
                    +-- Q-1 answered here may insert "M1b: secondary backfill source"
```

Hard constraints:
- **M5 (Risk Engine) must complete before M6.** No order-capable code before the risk middleware is
  property-tested. This is the inversion of the usual temptation and it is deliberate.
- **M2 must complete before M4.** The measuring instrument is built and verified before anything is
  measured with it.
- **M2's gate thresholds and trial counter are frozen before M4 begins** - so a strategy cannot be
  helped through by moving a threshold.
- **M6 is DEMO-only.** M7 is PAPER-only. Neither touches LIVE.
- The sealed holdout is read **exactly once**, in M4, after tuning is frozen.

## Milestone risk summary

| M | Risk | Dominant failure mode |
|---|---|---|
| M0 | Low | - |
| M1 | **Medium** | Insufficient history depth (R-9) invalidates the §11.3 protocol |
| M2 | **High (silent)** | An optimistic backtester produces confident garbage downstream |
| M3 | Medium | Look-ahead in an indicator; caught by T-1 tests |
| M4 | **High** | Overfitting; the gates and trial counter are the defence, loosening them is the danger |
| M5 | **High** | A sizing or risk bug converts a bounded loss into an unbounded one |
| M6 | **High** | Duplicate positions / orphaned orders from ambiguous sends (R-6) |
| M7 | Medium | Optimistic fill model; misreading n=50 as proof of edge (R-2) |
| M8 | Medium | Trade authorisation via third-party channel (R-3) |
| M9 | Medium | Strategy that "works" on noise - must fail this to proceed |

**No time estimates are given.** Each milestone exits on evidence, not on a date; dates would create
pressure to pass gates rather than to satisfy them.
