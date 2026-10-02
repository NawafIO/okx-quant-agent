# OKX Quantitative Multi-Agent Trading System - Architecture Design Document

Date: 2026-10-02
Phase: **1 of 4 - INSPECT / ANALYZE / DESIGN. Zero live trading. Zero code written.**
Status: **Chief Advisor verdict issued 2026-10-02: RECOMMEND TO APPLY**, conditional on prerequisites
P-1..P-9 and audit findings F-1/F-2 (both applied). See §24. **Awaiting user authorisation -
no implementation is authorized by this document.**
Companion: `DEVELOPMENT_ROADMAP.md` (milestones, acceptance criteria, prerequisites)
Author role: Lead Quantitative Systems Architect

> **Live trading is architecturally impossible in Phase 1, not merely disabled.** See §6.4 for the
> four-layer lock. The outermost layer is that no trade-permitted API credential is ever provisioned,
> so no configuration error, no code defect and no LLM output can reach a live order endpoint.

---

## 1. Workspace inspection - findings

Inspected `C:\Users\nawaf\projects` (non-git shared root) to depth 3, plus a full-tree content search
for `ccxt|okx|binance|backtest|trading|ohlcv|duckdb|freqtrade` (case-insensitive).

| # | Finding | Detail |
|---|---|---|
| **W-1** | **No existing trading code. Greenfield.** | Content search returned zero matches across the entire root. Nothing to refactor, nothing to reuse functionally. |
| **W-2** | Three unrelated projects occupy the root | `Athar` (Windows activity tracker: C#/.NET Framework 4.8 + PowerShell 5.1 + JS report UI, git repo, actively developed); `Berd-Arabic` (shipped binaries, ~330 MB); `goose-artifacts` (loose scripts, installers, an `Athar-main` duplicate). |
| **W-3** | **Python is absent.** | `python.exe` on PATH is the Microsoft Store *stub alias* only - invoking it prints the install advertisement. `pip` MISSING, `py` launcher MISSING, `uv` MISSING. `HKLM\SOFTWARE\Python\PythonCore` and `HKCU\...\PythonCore` both empty. `winget list --name Python` -> no installed package. **There is no Python runtime on this machine.** |
| **W-4** | Toolchain actually present | `winget` (so W-3 is a one-command fix), `git` 2.x (`C:\Program Files\Git`), `node` v24.11.0 + `npm` (vendored under the Berd app directory, not a system install), Windows PowerShell **5.1** (no `pwsh`). **No Docker.** |
| **W-5** | No reusable component, but a reusable *convention* | Athar's `docs/` establishes this workspace's engineering register: numbered findings, explicitly **ruled** conflicts, status/sign-off headers, constraints documents treated as authoritative over design documents. This document adopts that register deliberately. |
| **W-6** | Windows-only, single-host, no container runtime | Drives the deployment model in §7: a Windows service/scheduled-task process set, not a Compose stack. No orchestrator is available and none should be assumed. |
| **W-7** | Encoding hazard observed | `Athar/docs/ARCHITECTURE-V2.md` renders Arabic as mojibake when read back (UTF-8 bytes interpreted as ANSI). All documents and data files in this project are **ASCII-only or explicitly UTF-8 with declared encoding**; `Set-Content`/`Out-File` calls must pass `-Encoding utf8`. |

### 1.1 Consequence of W-3 for this design

The requested stack (CCXT, DuckDB, Parquet, Streamlit, multi-year pandas research) is Python-centric.
Python being absent **blocks nothing in Phase 1**, which produces documents only. It becomes a hard
prerequisite at M0 of the roadmap. It is recorded as ruling **X-1** below rather than being treated as
a reason to redesign the stack.

---

## 2. Ruled design conflicts

Each item below is a point where two constraints, or a constraint and the inspected reality, could not
both be satisfied literally. All are ruled here. Rulings are authoritative over any contrary text
elsewhere in this document.

| ID | Conflict | **Ruling** |
|---|---|---|
| **X-1** | Mandated stack is Python-centric; no Python runtime exists (W-3). TypeScript/Node is installed and CCXT has a first-class JS implementation. | **PYTHON. Install it; do not redesign.** Rationale: the quantitative ecosystem is first-class only in Python (pandas/numpy/scipy vectorised research, `duckdb`, `pyarrow`, `vectorbt`/custom event loop, `python-telegram-bot`, Streamlit). Node loses Streamlit outright and materially thins backtesting, statistics and data-frame tooling; a TS port would trade a one-command install for a permanent ecosystem tax. **Node is retained for nothing in this system.** Python 3.12+ via `winget install Python.Python.3.12` is prerequisite **P-1** (roadmap M0). |
| **X-2** | "LIVE environment must exist as a decoupled target" vs. "LIVE strictly locked in Phase 1". | **BOTH, via capability absence rather than a flag.** The LIVE adapter profile is fully specified and type-checked, but unreachable: the credential set for LIVE is never created (§6.4 Layer 1). A locked environment is a *missing capability*, not a `False` in a config file. |
| **X-3** | DEMO and PAPER both described as "simulated" - risk of collapsing them into one mode behind a boolean. | **THREE DISTINCT MECHANISMS, NOT ONE FLAG.** DEMO exercises OKX's own simulated-trading venue over the real API (validates the adapter, auth, symbol metadata, order lifecycle, error taxonomy). PAPER never contacts any order endpoint - it is our own fill engine consuming the live public feed (validates strategy, slippage model, latency budget). They answer different questions and must both pass. See §6. |
| **X-4** | "Automated discard of models with MaxDD > 15% or PF < 1.5" - as literally specified this is an overfitting engine. | **GATES ADOPTED, BUT INSUFFICIENT ALONE - AUGMENTED.** The two thresholds are necessary conditions, not sufficient ones. They are enforced only *in addition to* a minimum trade count, walk-forward out-of-sample evaluation, a never-touched holdout, and a multiple-testing correction. See §11.3 and finding **R-1**. Repeated automated search against a single in-sample window is prohibited. |
| **X-5** | "50 paper trades validate performance before real consideration." | **ADOPTED AS AN INFRASTRUCTURE GATE, EXPLICITLY NOT AN EDGE GATE.** 50 trades cannot distinguish a profitable strategy from a losing one at any useful confidence (§11.4). The 50-trade run is the acceptance test for *latency, slippage realism, order-lifecycle correctness and operational stability*. Edge evidence comes from the backtest + walk-forward, never from n=50. See finding **R-2**. |
| **X-6** | LLM agents are central to the "multi-agent" framing, yet must not touch trade calculation. | **HARD INVARIANT ENFORCED STRUCTURALLY.** LLM output is confined to a closed enum or a bounded score and may only ever enter deterministic code as an *input to a gate*, never as a number that propagates into size, price, stop or quantity. No LLM has credentials, and no LLM has a code path to the Execution Engine. See §5 and §5.2. |
| **X-7** | Interactive approval via Telegram = a trade-authorisation channel on third-party infrastructure. | **PERMITTED WITH FIVE MANDATORY CONTROLS** (allowlisted chat ID, signed callback payloads, proposal TTL, server-side single-use state, re-validation at execution). An unsigned Telegram callback button must never be a sufficient condition to place an order. See §19.2 and finding **R-3**. |
| **X-8** | DuckDB *and* SQLite *and* Parquet all named for storage. | **ALL THREE, PARTITIONED BY ROLE, NOT OVERLAPPING.** Parquet = immutable historical market data (columnar, partitioned). DuckDB = the analytical query engine over that Parquet, plus research result tables. SQLite = live operational transactional state (open orders, positions, proposal state machine) where ACID single-writer semantics matter. Audit log is append-only files, hash-chained (§20). |
| **X-9** | No Docker (W-6) vs. a 20-subsystem service architecture. | **SINGLE-HOST PROCESS SET, modular monolith.** One Python package, a small number of long-running processes (collector, trading core, dashboard, bot) supervised by Windows Task Scheduler / NSSM. Module boundaries are enforced in code by the data contracts in §4, not by network boundaries. This is a deliberate down-scoping of operational complexity to match the host. |

---

## 3. Traceability - mandated scope to design section

All 20 subsystems, both operational workflows, and the hard invariant are mapped. This table is the
coverage checklist for the Chief Advisor audit.

| # | Mandated subsystem | Section | Deterministic? | LLM involvement |
|---|---|---|---|---|
| 1 | Master Trading Agent | §8.1 | Yes - state machine | None |
| 2 | Market Scanner | §8.2 | Yes | None |
| 3 | Market Regime Detection | §8.3 | Hybrid - deterministic primary, LLM advisory | **Permitted (enum only)** |
| 4 | Quant Analysis Engine | §9.1 | Yes | None |
| 5 | Technical Analysis Engine | §9.2 | Yes | None |
| 6 | News & Sentiment Analysis | §9.3 | No - LLM native | **Permitted (bounded score)** |
| 7 | Strategy Engine | §10 | Yes - pure functions | None |
| 8 | Historical Data Pipeline | §12 | Yes | None |
| 9 | Event-Driven Backtesting Engine | §11 | Yes | None |
| 10 | Risk Engine (pre-trade middleware) | §13 | **Yes - absolutely** | **Forbidden** |
| 11 | Portfolio Manager | §14 | Yes | None |
| 12 | Execution Engine | §15 | **Yes - absolutely** | **Forbidden** |
| 13 | OKX Exchange Adapter (CCXT) | §16 | Yes | None |
| 14 | Performance & Metrics Engine | §17 | Yes | Reporting prose only |
| 15 | Adversarial / Validation Agent | §18 | Hybrid | **Permitted (critique text)** |
| 16 | Monitoring Dashboard | §19.1 | Yes | Narration only |
| 17 | Alert & Notification (Telegram) | §19.2 | Yes - transport | Conversational layer only |
| 18 | Emergency Stop / Kill Switch | §19.3 | **Yes - absolutely** | **Forbidden** |
| 19 | Database & Storage Architecture | §20.1 | Yes | None |
| 20 | Logging & Immutable Audit Trail | §20.2 | Yes | None |
| W-A | Workflow: Self-Testing & Validation Protocol | §11.3, §11.4 | Yes | None |
| W-B | Workflow: Interactive HITL Interface + Trade Gate | §19.2 | Gate is deterministic | Conversation only |
| INV | Hard Invariant: LLM confinement | §5 | - | - |
| ENV | Decoupled DEMO / PAPER / LIVE | §6 | Yes | None |

---

## 4. Data contracts

Every inter-module boundary is a frozen, validated, typed record. Implementation: `pydantic v2`
models (runtime validation at every boundary) with `Decimal` for all money and quantity fields.

> **D-1 - No `float` for money, price, quantity or risk.** Python `float` is IEEE-754 binary and
> cannot represent `0.1` exactly. All prices, quantities, notionals and equity values are
> `decimal.Decimal` constructed **from strings**, never from floats. `float` is permitted only inside
> indicator mathematics and statistics, which never produce an order field directly.

> **D-2 - Every record carries `env: Literal["DEMO","PAPER","LIVE"]`.** It is a required field, not a
> default. Any module receiving a record whose `env` differs from its own bound environment raises
> `EnvironmentMismatchError` and halts - it does not coerce, log-and-continue, or fall back. This makes
> cross-environment contamination a loud crash instead of a silent live order.

```python
# contracts.py  (illustrative - normative field sets, Phase 2 implements)

Env = Literal["DEMO", "PAPER", "LIVE"]


class Candle(BaseModel):  # immutable market fact
    env: Env
    symbol: str  # CCXT unified, e.g. "BTC/USDT:USDT"
    timeframe: str  # "1m" | "5m" | "1h" | "4h" | "1d"
    ts_open: datetime  # UTC, tz-aware, left edge of bar
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool  # partial bars NEVER feed strategies
    source: str  # "okx.rest.history" | "okx.ws" | "backfill:<name>"
    ingested_at: datetime


class RegimeLabel(BaseModel):
    env: Env
    symbol: str
    ts: datetime
    regime: Literal["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "CRISIS"]
    confidence: Decimal  # [0,1]
    determined_by: Literal["RULE", "LLM_CONFIRMED", "RULE_LLM_DISAGREE"]
    rule_inputs: dict  # ADX, realised vol, Hurst - auditable


class SentimentScore(BaseModel):
    env: Env
    symbol: str
    ts: datetime
    score: Decimal  # [-1,+1] BOUNDED AND CLAMPED
    confidence: Decimal  # [0,1]
    model_id: str  # exact LLM model id, for audit
    source_urls: list[str]
    # NOTE: consumed ONLY as a veto/scale gate in §13.4. Never multiplies position size.


class Signal(BaseModel):  # emitted by Strategy Engine, pure function output
    env: Env
    strategy_id: str
    strategy_version: str  # git sha of strategy module
    symbol: str
    ts: datetime
    side: Literal["LONG", "SHORT"]
    entry_ref: Decimal  # reference price at signal time
    stop_loss: Decimal  # MANDATORY - no signal without a stop
    take_profit: list[Decimal]  # >=1 target
    conviction: Decimal  # [0,1] - deterministic, from strategy math only
    features: dict  # every indicator value used, for audit/replay


class TradeProposal(BaseModel):  # Risk Engine output - the only thing executable
    proposal_id: UUID
    env: Env
    signal: Signal
    qty_base: Decimal  # post-rounding, exchange-legal
    notional_quote: Decimal
    risk_amount: Decimal  # actual currency at risk after rounding
    risk_pct_of_equity: Decimal  # actual, recomputed - not the requested target
    leverage: Decimal
    liquidation_estimate: Decimal | None
    risk_checks: list[RiskCheckResult]  # ALL checks, each with pass/fail + values
    verdict: Literal["APPROVED", "REJECTED"]
    expires_at: datetime  # TTL - stale proposals are unexecutable (§19.2)
    hmac: str  # signature over canonical payload (§19.2)


class RiskCheckResult(BaseModel):
    check_id: str  # "RC-01".."RC-12" per §13.3
    passed: bool
    observed: Decimal | None
    limit: Decimal | None
    detail: str


class Order(BaseModel):
    client_order_id: str  # idempotency key - OUR id, deterministic, replay-safe
    proposal_id: UUID
    env: Env
    symbol: str
    side: str
    type: Literal["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
    qty_base: Decimal
    price: Decimal | None
    reduce_only: bool
    time_in_force: Literal["GTC", "IOC", "FOK", "POST_ONLY"]
    state: Literal["NEW", "SENT", "ACK", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"]
    exchange_order_id: str | None


class Fill(BaseModel):
    env: Env
    client_order_id: str
    exchange_trade_id: str
    ts: datetime
    price: Decimal
    qty_base: Decimal
    fee: Decimal
    fee_currency: str
    slippage_bps: Decimal  # vs. proposal entry_ref - feeds §17 and the §11.4 gate


class Position(BaseModel):
    env: Env
    symbol: str
    side: str
    qty_base: Decimal
    avg_entry: Decimal
    stop_loss: Decimal
    take_profit: list[Decimal]
    unrealised_pnl: Decimal
    realised_pnl: Decimal
    opened_at: datetime
    strategy_id: str
    protective_orders_confirmed: bool  # see §15.3 - a position without a live stop is an incident
```

**Contract flow (the only legal path to an order):**

```
Candle/Tick -> Scanner -> [Regime, TA, Quant] -> Strategy Engine -> Signal
  -> Risk Engine (deterministic; ALL of RC-01..RC-12) -> TradeProposal{APPROVED}
    -> [HITL approval gate, if enabled]  -> Execution Engine -> Order -> Adapter -> Fill
                                                                              -> Portfolio -> Metrics
```

No arrow may be short-circuited. There is no code path from Signal to Order that bypasses the Risk
Engine; the Execution Engine's only public entry point accepts a `TradeProposal` with
`verdict == "APPROVED"` and a valid, unexpired HMAC, and accepts nothing else.

---

## 5. HARD INVARIANT - LLM confinement

### 5.1 Permission table

| Capability | LLM permitted? | Enforcement |
|---|---|---|
| Qualitative market research, narrative summary | **YES** | Output is text, consumed by humans/reports only |
| Market regime classification | **YES, advisory only** | Must emit one of 5 closed enum values; deterministic rule classifier runs independently and **wins on disagreement** (§8.3) |
| News / sentiment scoring | **YES, bounded** | Output clamped to `[-1,+1]`; schema-validated; consumed only as a veto/gate (§13.4) |
| Interactive reporting, answering user queries | **YES** | Read-only access to metrics store; cannot invoke execution tools |
| Adversarial critique of a strategy design | **YES** | Produces text findings for human review; cannot block or pass a gate automatically |
| **Position sizing / quantity** | **NO** | §13.2 formula, deterministic code only |
| **Stop-loss or take-profit price** | **NO** | Strategy math + §13 validation only |
| **Any risk check or threshold evaluation** | **NO** | §13.3 RC-01..RC-12, pure functions |
| **Order parameters, order placement, cancellation** | **NO** | LLM has no tool binding to the Execution Engine |
| **Kill-switch arm/disarm** | **NO** | §19.3 - human or deterministic trigger only |
| **Holding or reading API credentials** | **NO** | Credentials are never in an LLM context window |

### 5.2 Why this is structural and not a policy

1. **No tool binding.** The LLM-facing agents are constructed with a tool list that contains read-only
   query functions only. `place_order`, `cancel_order`, `size_position`, `set_stop`, `arm_kill_switch`
   are not in any LLM tool registry. The invariant cannot be violated by a prompt, because the
   capability is not exposed - prompt injection in a news article has nothing to call.
2. **Type narrowing at the boundary.** Every LLM response is parsed into `RegimeLabel` or
   `SentimentScore` - closed enums and clamped decimals. An LLM cannot return "size 3.7 BTC" because
   there is no field of that shape anywhere in its output schema. Parse failure = the input is
   discarded and the deterministic default is used; it is never retried into compliance.
3. **Numeric isolation.** No LLM-derived number is ever a multiplicand in a sizing or price
   expression. `SentimentScore.score` is used exclusively in boolean comparisons
   (`if score < -0.6: veto_longs`). This is asserted by a unit test that greps the sizing and
   execution modules for any import or reference to an LLM-sourced field - a CI guard, finding **R-4**.
4. **Degrade-closed.** If every LLM provider is unavailable, the system continues trading on
   deterministic rules alone, with sentiment treated as neutral and regime from the rule classifier.
   LLM outage is not an outage. Conversely, an LLM can only ever *reduce or block* exposure relative
   to the deterministic baseline - never increase it.

---

## 6. Decoupled execution environments

### 6.1 The three environments answer three different questions

| | **DEMO** | **PAPER** | **LIVE** |
|---|---|---|---|
| Question answered | Is our adapter, auth and order lifecycle correct against the real API? | Does the strategy behave as backtested against live data, at acceptable latency/slippage? | Does it make money? |
| Order endpoint contacted | **Yes** - OKX simulated-trading venue | **No** - never | Yes - real venue |
| Market data | Real OKX public feed | Real OKX public feed | Real OKX public feed |
| Fills produced by | OKX's simulator | **Our own fill engine** (§6.3) | Real matching engine |
| Capital at risk | None | None | **Real** |
| Credentials | Demo key, trade scope | **Read-only key, or none** | **Not provisioned in Phase 1** |
| Phase 1 status | Buildable (M6) | Buildable (M7) | **LOCKED** (§6.4) |
| Validates | API contract, error taxonomy, symbol metadata, idempotency | Strategy, slippage model, latency budget, operational stability | - |

**Neither DEMO nor PAPER is evidence of profitability.** DEMO fills come from a simulator whose
liquidity does not reflect the real book; PAPER fills come from our own model, so PAPER measures our
model's self-consistency, not the market's behaviour. This is stated plainly because conflating
simulator P&L with expected live P&L is the single most common failure mode of systems of this shape.

### 6.2 Decoupling mechanism

Environment is selected **once, at process start, from an explicit CLI argument** - never from a
mutable config file, never from a default, never switchable at runtime.

```
python -m okxq.run --env PAPER      # required; no default value exists
```

- A single frozen `EnvProfile` is constructed at startup and injected everywhere. There is no global
  mutable "current env".
- **Per-environment physical isolation:** separate SQLite database file, separate Parquet root,
  separate audit-log chain, separate log files, separate Telegram chat, separate dashboard port,
  separate credential source. A DEMO process physically cannot write to the LIVE ledger.
- **Env is a required field on every record (D-2)** and mismatches raise and halt.
- The adapter factory is a `match` over the env with **no default branch** - adding a fourth
  environment is a compile-time-visible change, and a malformed env value is an immediate crash.

### 6.3 PAPER fill engine (deterministic, conservative by construction)

PAPER must not be optimistic, or it produces false confidence. Rules:

- Fills are evaluated against the **live order book / trade stream**, never against the candle close.
- A limit order fills only when the opposing side **trades through** the limit price, not merely
  touches it - and fills at most the volume observed at that level.
- A market order fills by **walking the live book** level by level, consuming real depth, and pays
  the resulting volume-weighted price; the whole top-of-book is never assumed available.
- **Latency injection:** a measured round-trip delay (from DEMO's observed latency distribution) is
  applied between decision time and fill evaluation. The price can and must move against us in that
  interval.
- **Fees applied at the real OKX taker rate**, plus funding charges on perpetual positions held
  across a funding timestamp.
- Explicit **adverse selection penalty**: when the book is thin relative to our size, add the full
  modelled impact; no midpoint fills, ever.
- Every PAPER fill records `slippage_bps`, which is compared against the backtester's assumption -
  divergence is itself a gate (§11.4).

### 6.4 LIVE lock - four independent layers

Layer 1 is the only one that matters, because it is the only one that cannot be defeated by a bug in
our own code.

1. **CREDENTIAL ABSENCE (primary).** No OKX API key with trade permission is created, and no
   `LIVE_*` secret exists in any store. The LIVE profile resolves its credentials from a source that
   is empty. Even a total failure of layers 2-4 yields an authentication error, not a trade.
2. **PHASE CONSTANT.** `PHASE: Final[int]` in a dedicated module (`okxq/phase.py`). `EnvProfile`
   construction for `LIVE` raises `LiveTradingLockedError` while `PHASE < LIVE_UNLOCK_PHASE`, where
   `LIVE_UNLOCK_PHASE = 4` - matching the roadmap's phase map, in which **Phase 3 is simulated
   operation (DEMO/PAPER) and Phase 4 is live**. Changing the constant is a reviewable source diff,
   not a config edit. *(Corrected during M0: an earlier draft of this clause said `PHASE < 3`, which
   contradicted the roadmap's own phase map and would have unlocked LIVE during simulated operation.)*
3. **KILL SWITCH DEFAULT-ENGAGED for LIVE.** The LIVE profile boots with the kill switch armed
   (§19.3); disarming requires a deliberate, audited, human action.
4. **CI GUARD.** A test asserts that instantiating the LIVE profile raises, that the raise survives a
   monkeypatched phase constant (proving the check is not dead code), and that no module imports a
   live credential name. The suite fails if LIVE becomes reachable.

Promotion DEMO -> PAPER -> LIVE requires the §11.5 criteria **and** explicit written human sign-off
recorded in the audit trail. There is no automatic promotion path to LIVE under any circumstance.

---

## 7. Deployment topology (per X-9)

```
okx-quant/
  pyproject.toml            # pinned deps, lockfile committed
  src/okxq/
    contracts.py            # §4 - frozen pydantic models
    phase.py                # §6.4 L2 - PHASE constant
    env/profiles.py         # §6.2 - EnvProfile factory, no default branch
    adapters/okx.py         # §16
    data/{collector,backfill,store}.py   # §12
    analysis/{ta,quant,regime,sentiment}.py  # §9, §8.3
    strategy/{base,registry}.py + strategy/impl/*.py  # §10
    backtest/{engine,gates,walkforward}.py   # §11
    risk/{engine,formulas,limits}.py     # §13 - PURE, NO I/O
    portfolio/manager.py    # §14
    execution/{engine,fills_paper,reconcile}.py  # §15, §6.3
    master/agent.py         # §8.1 - state machine
    metrics/engine.py       # §17
    validation/adversarial.py  # §18
    ui/{dashboard_app.py,telegram_bot.py}  # §19
    safety/kill_switch.py   # §19.3
    audit/{log,chain}.py    # §20.2
  tests/{unit,property,integration,guards}/
  data/<env>/parquet/...    # §20.1, per-env root
  state/<env>/okxq.db       # SQLite, per-env
  audit/<env>/*.jsonl       # hash-chained, append-only
```

Four long-running processes, supervised by Windows Task Scheduler (or NSSM), auto-restart on exit:
`collector` (market data), `core` (master agent + strategy + risk + execution), `dashboard`
(Streamlit), `bot` (Telegram). Inter-process coordination through the per-env SQLite database with
WAL mode; **`core` is the single writer** to operational state.

---

## 8. Coordination layer

### 8.1 Master Trading Agent (subsystem 1) - deterministic state machine

Not an LLM. An explicit finite state machine; every transition is logged with its trigger.

```
BOOT -> VALIDATING_ENV -> SYNCING_DATA -> RECONCILING(§15.4) -> IDLE
IDLE --(scan tick)--> SCANNING -> ANALYZING -> SIGNAL_PENDING
SIGNAL_PENDING -> RISK_CHECK -> {REJECTED -> IDLE | AWAITING_APPROVAL | EXECUTING}
AWAITING_APPROVAL --(approve)--> EXECUTING | --(cancel|TTL expiry)--> IDLE
EXECUTING -> MANAGING_POSITION -> IDLE
ANY --(kill switch | daily loss halt | data staleness | reconcile mismatch)--> HALTED
HALTED -> (human reset only)
```

Responsibilities: own the scan schedule; sequence the analysis fan-out; enforce one-signal-per-
symbol-per-bar; delegate to Risk (never override it); drive the approval flow; mediate HALTED. It
**cannot** compute sizes, bypass risk, or place an order itself.

### 8.2 Market Scanner (subsystem 2)

Universe: OKX USDT-margined perpetual swaps, filtered deterministically each cycle -
24h quote volume above floor, spread below ceiling, minimum listing age (avoid new-listing
pathologies), open interest floor, not in a manual denylist. Ranks candidates by a composite of
realised volatility, volume z-score and relative strength. Output: ranked symbol list with the
filter values that produced it, so any inclusion is auditable. Hard cap on universe size to bound
per-cycle compute and API weight.

### 8.3 Market Regime Detection (subsystem 3) - hybrid, deterministic-primary

**Deterministic classifier is authoritative.** Inputs: ADX, EMA slope/structure, realised vol vs. its
own percentile history, ATR percentile, Hurst exponent, and (as a crisis flag) correlation collapse
across the universe. Produces one of `TREND_UP | TREND_DOWN | RANGE | HIGH_VOL | CRISIS`.

The LLM runs **independently and in parallel** on qualitative context (news flow, macro calendar) and
emits the same enum. Resolution:

| Rule says | LLM says | Result |
|---|---|---|
| X | X | `LLM_CONFIRMED` - proceed, normal sizing |
| X | Y (disagree) | **`RULE_LLM_DISAGREE` - rule's label stands, and risk appetite is REDUCED** (§13.4) |
| X | unavailable / unparseable | Rule's label, `determined_by="RULE"`, proceed normally |

Disagreement can only ever shrink exposure. The LLM cannot change the regime label itself.

---

## 9. Analysis engines

### 9.1 Quant Analysis Engine (subsystem 4)

Deterministic statistics on closed bars: returns distribution (mean, vol, skew, kurtosis), realised
and Parkinson volatility, ATR, rolling Sharpe/Sortino, max drawdown, autocorrelation and
variance-ratio (mean-reversion vs. trend evidence), Hurst exponent, cross-asset correlation matrix
and clustering (feeds the correlation limit RC-07), beta to BTC, funding-rate statistics and
basis for perps. All vectorised over DuckDB/Parquet; all outputs reproducible from stored inputs.

### 9.2 Technical Analysis Engine (subsystem 5)

Pure functions, `(DataFrame) -> Series`, no state, no I/O. Trend (EMA/SMA, MACD, ADX, Supertrend,
Donchian), momentum (RSI, Stochastic, CCI, ROC), volatility (ATR, Bollinger, Keltner), volume (OBV,
VWAP, CVD, volume profile), structure (swing highs/lows, S/R, pivots).

> **T-1 - No look-ahead, enforced.** Indicators are computed **only on closed bars**
> (`Candle.is_closed == True`). Every indicator is covered by a property test asserting that
> `f(series[:n])[-1] == f(series)[n-1]` - i.e. the value at bar *n* never changes when future bars
> arrive. An indicator that fails this test cannot be used in a strategy. Shifted-signal convention:
> a signal computed on bar *n* may only be executed at bar *n+1*'s open or later.

> **T-2 - Library policy.** Prefer a maintained library (`pandas-ta` / `ta-lib` if its native build
> succeeds on this host - to be verified at M1, since TA-Lib needs a C toolchain on Windows). Any
> indicator we implement ourselves is validated against a reference implementation to a stated
> tolerance before use.

### 9.3 News & Sentiment Analysis (subsystem 6) - LLM native, tightly bounded

Sources: exchange announcements (listings, delistings, maintenance - these are *operational* risk,
not sentiment), major crypto news feeds, funding/liquidation extremes as a crowd-positioning proxy.
LLM summarises and emits a clamped `SentimentScore`.

> **S-1 - Treat all ingested news as hostile input.** It is untrusted third-party text entering an
> LLM context. Mitigations: the LLM has no tools at all in this path (summarisation only); output is
> schema-validated into a closed shape; content is delimited and the system prompt declares it as
> data, not instructions; a prompt-injection heuristic flags and quarantines suspicious articles. An
> injected instruction has no capability to call, which is the actual defence - the rest is depth.

> **S-2 - Hard operational overrides are deterministic, not sentiment.** A scheduled OKX maintenance
> window or a delisting notice for a held symbol triggers a **deterministic** rule (block new entries
> / flatten), parsed from the structured announcement API - never gated on an LLM's reading of it.

---

## 10. Strategy Engine (subsystem 7)

```python
class Strategy(Protocol):
    id: str
    version: str  # version = git sha, recorded on every Signal
    required_timeframes: list[str]
    required_indicators: list[str]
    allowed_regimes: list[str]  # declarative regime gating

    def generate(self, ctx: MarketContext) -> Signal | None: ...  # PURE
```

- `generate` is a **pure function** of its context: no I/O, no clock access, no randomness (any
  stochastic element takes an injected seed). This is what makes a backtest and live run bit-identical
  on the same inputs, and is asserted by test.
- **Every signal must carry a stop-loss.** A `Signal` without `stop_loss` fails contract validation -
  the system has no concept of an unprotected entry.
- Strategies declare `allowed_regimes`; the Master Agent will not route a signal generated in a
  disallowed regime.
- **Versioned registry.** Strategies are registered with a version; results, proposals and fills are
  all tagged with it. A strategy's recorded performance can never be silently attributed to modified
  code.
- Initial candidate families (hypotheses to be tested and mostly expected to be rejected):
  trend-following breakout with ATR trailing stop; mean-reversion at Bollinger/Keltner extremes
  gated to `RANGE`; funding-rate carry on perps; volatility-breakout on compression. Each is a
  hypothesis with a stated economic rationale, not a parameter set to be optimised until it passes.

---

## 11. Event-Driven Backtesting Engine (subsystem 9) + Self-Testing Protocol (Workflow A)

### 11.1 Engine design

Single-threaded chronological event loop over a merged, timestamp-ordered event queue
(`BarClosed`, `Tick`, `FundingAccrual`, `OrderFilled`, `StopTriggered`, `TimerFired`).

**The engine never sees a future event.** The strategy is handed an immutable, right-truncated view
of history at the current event's timestamp - not the full DataFrame with a cursor. Look-ahead is
prevented by the data structure, not by developer discipline.

### 11.2 Realism requirements (non-negotiable)

| Factor | Treatment |
|---|---|
| Execution timing | Signal on bar *n* close executes at bar *n+1* open at the earliest (T-1) |
| Fees | Real OKX maker/taker rates, charged per fill in the correct currency |
| Funding | Perpetual funding accrued at every real funding timestamp held |
| Slippage | Volatility- and size-scaled model, floor of 1 tick; **assumption recorded and later reconciled against PAPER (§11.4)** |
| Stops | Intrabar triggering using high/low; a stop and a target hit in the same bar resolves **pessimistically** (stop assumed first) unless finer-grained data proves otherwise |
| Gaps | Stop fills at the gapped price, not the stop price - gap risk is real |
| Liquidation | Modelled explicitly for leveraged perps |
| Partial fills | Modelled for size relative to observed volume |
| Survivorship | Universe includes **delisted** symbols for their live period |

### 11.3 Model acceptance gates (per ruling X-4)

A strategy is **discarded automatically** if **any** of these fails:

| Gate | Threshold | Source |
|---|---|---|
| G-1 Max Drawdown | **> 15%** -> DISCARD | Mandated |
| G-2 Profit Factor | **< 1.5** -> DISCARD | Mandated |
| G-3 Trade count | **< 100 closed trades** -> INVALID, not evaluable | **Added (R-1)** |
| G-4 Out-of-sample | PF must hold **>= 1.2** on walk-forward OOS folds | **Added (R-1)** |
| G-5 OOS degradation | OOS PF < **50%** of in-sample PF -> DISCARD (overfit) | **Added (R-1)** |
| G-6 Parameter stability | Performance must not collapse under +/-20% parameter perturbation | **Added (R-1)** |
| G-7 Concentration | Top 5 trades contributing **> 50%** of net profit -> DISCARD | **Added (R-1)** |
| G-8 Multiple testing | Deflated Sharpe Ratio adjusted for the number of configurations tried must stay significant | **Added (R-1)** |
| G-9 Costs | Must remain above gates at **2x** modelled fees and slippage | **Added (R-1)** |

> **Finding R-1 (HIGH) - the mandated gates alone are an overfitting engine.** "Automatically discard
> models with MaxDD > 15% or PF < 1.5" applied to a search over many configurations on one in-sample
> window does not select good strategies - it selects the configurations that fit the noise of that
> window. With enough trials, something always passes. G-3..G-9 exist to convert the mandated
> thresholds from a *selection rule* into *necessary conditions within a statistically honest
> protocol*. **Additionally: every configuration tried is counted and logged** (the denominator for
> G-8), and a final holdout period is sealed - excluded from all research and touched exactly once,
> immediately before any promotion decision. If the holdout fails, the strategy is dead; it is not
> re-tuned on the holdout.

Walk-forward protocol: rolling anchored windows (e.g. 12 months train / 3 months OOS, stepped by 3
months) across multi-year history, spanning at least one full bull and one full bear phase, with the
2022 drawdown and at least one flash-crash event inside the sample.

### 11.4 Paper-trading sandbox gate (per ruling X-5)

Minimum **50 closed paper trades** against the live feed before a strategy is eligible for further
consideration. Acceptance criteria:

| Criterion | Threshold |
|---|---|
| Closed paper trades | **>= 50** (mandated) |
| Decision-to-order latency | p99 within stated budget; **zero** missed bars |
| Realised paper slippage vs. backtest assumption | within **1.5x**; above that the backtest is re-run with corrected costs and must still pass §11.3 |
| Order-lifecycle correctness | 100% of orders reach a terminal state; **zero** orphaned/unknown states |
| Protective-order integrity | **zero** instances of a position existing without a confirmed live stop |
| Reconciliation | **zero** unexplained divergences between internal state and venue state |
| Unhandled exceptions | **zero** |
| Kill-switch drill | Executed at least once, flattening verified |

> **Finding R-2 (HIGH) - 50 trades cannot validate profitability, and must not be read as doing so.**
> At n=50, the confidence interval on profit factor or expectancy is so wide that a genuinely
> unprofitable strategy passes routinely by chance. The 50-trade run is therefore scoped above as an
> **infrastructure and realism acceptance test** - it proves the machine works and that our cost
> assumptions are not fantasy. Evidence of *edge* comes only from §11.3's walk-forward and holdout.
> A system that promotes on 50 profitable paper trades is a system that promotes on luck.

### 11.5 Promotion criteria between environments

| Transition | Requirements |
|---|---|
| Research -> DEMO | §11.3 G-1..G-9 all pass; sealed holdout passes; strategy code frozen at a git sha |
| DEMO -> PAPER | Adapter integration suite green; full order lifecycle exercised incl. rejects, partial fills, cancels; idempotency proven by forced-retry test |
| PAPER -> LIVE | §11.4 all criteria met; >= 30 continuous days of operation; cost reconciliation within tolerance; kill-switch drill passed; **PHASE >= 4**; **explicit written human sign-off in the audit trail**; LIVE credentials provisioned at that moment with minimum necessary scope and withdrawal disabled |

---

## 12. Historical Data Pipeline (subsystem 8)

Stages: **fetch** (CCXT `fetch_ohlcv`, paginated backwards from now, respecting rate limits) ->
**validate** -> **normalise** (UTC tz-aware, `Decimal` via string, canonical symbol) -> **store**
(Parquet, partitioned `symbol/timeframe/year/month`) -> **register** (DuckDB view + a manifest row
recording coverage and gaps).

Validation rules, every one of which quarantines rather than silently repairs:
monotonic non-duplicate timestamps; **gap detection against the expected bar grid** with gaps
recorded explicitly in the manifest, never forward-filled; OHLC sanity (`low <= open,close <= high`,
all positive); non-negative volume; outlier flagging at >N sigma for human review; **partial final
bar always dropped**.

Idempotent and resumable: re-running a backfill for a covered range is a no-op; interruption resumes
from the manifest. Writes are atomic (temp file + rename) so a crash cannot leave a torn Parquet file.

> **Open question Q-1 (resolve empirically at M1, do not assume).** OKX's candle endpoints differ in
> history depth - the recent-candles endpoint and the history-candles endpoint have different limits
> and different retention per timeframe, and CCXT's `fetch_ohlcv` pagination behaviour over the
> history endpoint must be measured, not assumed. **M1 task:** for each required timeframe, determine
> the actual earliest retrievable timestamp per symbol and the achievable throughput under rate
> limits. If depth is insufficient for the multi-year walk-forward in §11.3, a secondary backfill
> source is required and becomes a roadmap addition. **This document deliberately does not state
> OKX's limits as fact.**

---

## 13. Risk Engine (subsystem 10) - deterministic pre-trade middleware

**The most safety-critical module. Pure functions, zero I/O, zero network, zero LLM, 100% branch
coverage required, property-tested.** It receives a `Signal` plus an immutable snapshot of portfolio
state and returns a `TradeProposal` with every check recorded. It is the only module that may compute
a position size.

### 13.1 Design rules

- **Default deny.** Any error, missing input, `None`, `NaN`, or unparseable value in any check
  produces `REJECTED`. There is no "assume fine and continue".
- **No bypass.** No flag, config value, environment variable or admin command skips a risk check.
- Checks are **independent and all evaluated**, so the proposal records the full picture rather than
  short-circuiting at the first failure.
- Rejection is normal and cheap. The engine is not tuned to approve.

### 13.2 Position sizing - fixed fractional

```
  risk_capital   = equity * risk_per_trade_pct          # e.g. 10_000 * 0.005 = 50
  stop_distance  = abs(entry - stop_loss)               # REJECT if 0 or if sign is wrong for side
  qty_raw        = risk_capital / stop_distance
  qty_base       = round_DOWN_to_lot_step(qty_raw)       # always down - never round up into risk
  notional       = qty_base * entry
  actual_risk    = qty_base * stop_distance              # <= risk_capital by construction
  actual_risk_pct= actual_risk / equity                  # RECORDED on the proposal
```

Mandatory post-sizing validations:

- `qty_base < exchange_min_size` -> **REJECT**. The size is never inflated to meet the minimum; that
  would silently exceed the risk budget. This is the single most common sizing bug in retail systems.
- `actual_risk > risk_capital` -> **REJECT** (should be unreachable given round-down; asserted anyway).
- `stop_distance < k * ATR_floor` or `< m * tick_size` -> **REJECT** (a too-tight stop manufactures a
  huge position from a small risk budget - the dangerous direction of the sizing formula).
- Stop must be on the correct side of entry for the side (`LONG: stop < entry`); otherwise **REJECT**.
- Required margin at the venue's rules must be available with headroom; leverage capped per §13.3.
- For leveraged perps: estimated liquidation price must sit **beyond** the stop by a stated buffer, so
  the stop is reached first. If not, **REJECT**.

Equity basis: a conservative mark-to-market of realised equity using mark prices, computed once per
decision cycle and frozen for that cycle - so sizing cannot oscillate within a cycle. Risk percent is
taken from the **lower** of the strategy's requested risk and the portfolio policy ceiling.

### 13.3 Pre-trade check battery

| ID | Check | Default |
|---|---|---|
| RC-01 | Kill switch disengaged | Armed = reject all |
| RC-02 | Environment match (D-2) + PHASE allows this env | Mismatch = halt |
| RC-03 | Market data freshness (last tick/bar within staleness budget) | Stale = reject |
| RC-04 | Symbol tradable (listed, not in maintenance, not flagged for delisting per S-2) | - |
| RC-05 | Per-trade risk <= `max_risk_per_trade_pct` | 0.5% |
| RC-06 | **Portfolio heat**: sum of open risk + this trade <= `max_portfolio_heat_pct` | 3.0% |
| RC-07 | **Correlation cluster exposure** <= cluster cap (crypto is ~1-beta to BTC; 5 "uncorrelated" alt longs is one leveraged BTC long) | 1.5% per cluster |
| RC-08 | Max concurrent positions | 5 |
| RC-09 | One position per symbol; no opposing position; **no duplicate signal per symbol per bar** | - |
| RC-10 | **Daily loss halt**: today's realised + unrealised loss < `daily_loss_limit_pct` | 2.0% -> HALTED |
| RC-11 | **Equity drawdown halt**: peak-to-current drawdown < `max_dd_halt_pct` | 10% -> HALTED |
| RC-12 | Notional, leverage and margin-headroom caps; exchange min/max/step compliance | lev <= 3x |

Plus: consecutive-loss cooldown; a cap on new entries per hour (anti-runaway); a venue API error-rate
circuit breaker; and a **daily-reset boundary defined in a fixed stated timezone (UTC)** so "today" is
never ambiguous.

### 13.4 Where qualitative inputs may touch risk (one direction only)

| Condition | Effect |
|---|---|
| `RegimeLabel.determined_by == "RULE_LLM_DISAGREE"` | Risk appetite multiplier **x0.5** |
| `SentimentScore.score < -0.6` with confidence > 0.7 | **Veto new LONG entries** |
| `SentimentScore.score > +0.6` with confidence > 0.7 | **Veto new SHORT entries**; does **not** enlarge longs |
| `regime == "CRISIS"` | New entries blocked; existing positions managed to exit only |
| Sentiment unavailable | Treated as neutral; deterministic baseline unchanged |

Qualitative inputs are **monotonically risk-reducing**. There is no path by which an LLM output
increases position size, loosens a stop, or raises a limit.

---

## 14. Portfolio Manager (subsystem 11)

Single source of truth for positions, open risk, exposure and equity curve, per environment.
Computes aggregate open risk (feeding RC-06), per-cluster exposure (RC-07), net and gross exposure,
margin utilisation; maintains the equity curve and high-water mark (RC-11); manages position
lifecycle (scale-outs at targets, ATR trailing stops, breakeven moves, time-based exits); accrues
funding on perps; and reconciles against venue state (§15.4). All state transitions are written to
SQLite in a transaction and mirrored to the audit chain.

---

## 15. Execution Engine (subsystem 12)

### 15.1 Entry contract

The **only** public entry point accepts a `TradeProposal` and rejects it unless: `verdict ==
"APPROVED"`; `expires_at` is in the future; HMAC verifies over the canonical payload; `env` matches
the process environment; the `proposal_id` has not been consumed before (single-use); and - if HITL is
enabled - a recorded, matching approval exists. No other way in.

### 15.2 Idempotency and the unknown-state problem

- Every order carries a **deterministic `client_order_id`** derived from
  `(proposal_id, leg, attempt_class)`. A retry after a network timeout re-sends the *same* id, so the
  venue deduplicates instead of us double-filling.
- Retries are permitted **only** on provably non-committal failures (connect timeout, 5xx before
  send-ack). A read timeout *after* sending is **UNKNOWN**: the system must **query venue state by
  `client_order_id` before any further action**, and must never blind-retry. Blind retry on ambiguous
  send is how duplicate positions are created.
- An order that cannot be resolved to a terminal state within a bounded window raises an incident and
  transitions the agent to `HALTED`.

### 15.3 Protective orders

A position without a live protective stop at the venue is an **incident**, not a state. Sequence:
place entry -> on fill confirmation, immediately place `reduce_only` stop (and targets) -> verify
acceptance by reading back the order -> set `protective_orders_confirmed = True`. If the protective
order is rejected or cannot be confirmed, the engine **closes the just-opened position immediately**
and raises an alert. A continuous watchdog re-asserts missing protective orders and alerts on every
occurrence.

### 15.4 Reconciliation

On boot, after any disconnect, and on a fixed interval: fetch venue positions, balances and open
orders; diff against internal state; **any unexplained divergence -> `HALTED` + alert, never
auto-correct silently**. Reconciliation runs before the agent is allowed to leave `BOOT`, so a crash
mid-order cannot result in a second entry on restart.

---

## 16. OKX Exchange Adapter (subsystem 13) - via CCXT

Thin, fully-typed boundary; CCXT types never leak past it (so the venue is replaceable and testable).
Responsibilities: symbol/precision/limits metadata loading (`load_markets`, cached with TTL); REST
with rate-limit governance and exponential backoff with jitter; WebSocket public feeds
(trades/book/candles) and private feeds (orders/positions/balance) with heartbeat monitoring and
automatic resubscribe; clock-skew measurement against server time; normalisation of OKX error codes
into a typed taxonomy (`RateLimited`, `InsufficientMargin`, `InvalidOrder`, `AuthError`,
`VenueMaintenance`, `Unknown`) because retry policy depends entirely on correct classification.

> **Open question Q-2 (verify at M1, do not assume).** DEMO is intended to use OKX's demo-trading
> venue, which in CCXT is reached via sandbox mode (`set_sandbox_mode(True)`), and which OKX
> implements with a simulated-trading request header rather than a wholly separate hostname. **The
> exact mechanism, the correct flag, whether a separate demo API key must be generated in the OKX UI,
> and which symbols exist in demo must all be verified empirically against live CCXT and current OKX
> documentation during M1.** This document does not assert these details as fact; the adapter is
> designed so that the DEMO profile's base-URL/header/credential resolution is a single isolated
> function, making Q-2's answer a one-place change.

> **Finding R-5 (MEDIUM) - CCXT is a single point of failure and a supply-chain dependency.** It is
> the right choice (maintained, unified, broad coverage), but: pin the exact version and commit the
> lockfile; never auto-upgrade; re-run the full DEMO integration suite on any upgrade; and keep the
> adapter thin enough that a direct-API fallback for the order path remains feasible.

---

## 17. Performance & Metrics Engine (subsystem 14)

Returns and risk: total/CAGR, Sharpe, Sortino, Calmar, max and average drawdown, drawdown duration,
volatility, VaR/CVaR, ulcer index. Trade statistics: win rate, profit factor, expectancy, average
win/loss, payoff ratio, consecutive runs, holding time, MAE/MFE. Execution quality: realised slippage
vs. reference, fill rates, latency distributions, fee and funding drag. Attribution by strategy,
symbol, regime, session and side.

**Cost reconciliation is a first-class output:** backtest-assumed vs. PAPER-realised vs. (eventually)
LIVE-realised costs, side by side. A strategy whose live costs exceed its backtest assumption by more
than the stated tolerance is automatically re-evaluated against §11.3 with corrected costs. LLMs may
narrate these numbers; they never compute them.

---

## 18. Adversarial / Validation Agent (subsystem 15)

Automated adversarial battery (deterministic): look-ahead detection (re-run the backtest with future
data deliberately corrupted - results must be unchanged); shuffled-returns and synthetic
random-walk tests (the strategy must **fail** on noise, else it is fitting noise); parameter
sensitivity surfaces; regime-partitioned performance (does it only work in one bull window?);
Monte-Carlo trade-order resampling for drawdown distribution; cost-shock stress (2x, 5x); missing-data
and gap injection; and extreme-event replay (flash crash, funding spike, venue outage).

LLM contribution: adversarial *critique prose* - what assumptions is this strategy making, what market
change would break it, what is the economic story and is it plausible. Findings are written for human
review. **The LLM cannot pass or fail a gate.** Gate outcomes are computed by the deterministic
battery above.

---

## 19. Interface, alerting and safety

### 19.1 Monitoring Dashboard (subsystem 16)

Streamlit, read-only, bound to **localhost**, one port per environment. **The environment is displayed
as a large, colour-coded banner on every page** (DEMO blue / PAPER amber / LIVE red) - operator
confusion about which environment is in front of them is itself a risk control. Views: equity curve
and drawdown, open positions with live risk, recent proposals with every risk-check result, order
blotter, data-pipeline health and gap map, latency/slippage distributions, strategy leaderboard,
kill-switch status and system state.

### 19.2 Alert & Notification System + HITL Trade Gate (subsystem 17, Workflow B)

Telegram bot. Conversational queries (read-only): market analysis, indicator values, regime,
positions, paper balance, performance, data health. These may be LLM-mediated, with the LLM holding
read-only query tools only.

**Trade approval flow:**

```
Signal -> Risk Engine (ALL checks run FIRST) -> TradeProposal{APPROVED}
  -> Telegram message: symbol, side, entry, stop, target(s), qty, notional,
     actual risk % and currency amount, leverage, every risk check with values,
     regime, strategy id+version, and the expiry countdown
  -> inline keyboard: [ APPROVE ] [ CANCEL ]
  -> on APPROVE: re-validate (§19.2 controls) -> Execution Engine
```

> **Finding R-3 (HIGH) - a Telegram button must never be a sufficient condition to trade.** Telegram
> is third-party infrastructure outside our trust boundary, and callback data is attacker-shaped
> input. Five mandatory controls, all required:
>
> 1. **Chat/user allowlist** - a single hard-configured authorised user id; every other sender is
>    ignored and logged as a security event.
> 2. **Signed callback payload** - callback data carries `proposal_id` + HMAC-SHA256 over the
>    canonical proposal using a local secret. An unsigned or mis-signed callback is discarded.
>    Callback data is never trusted to carry prices or quantities - only an opaque id.
> 3. **Server-side single-use state machine** - the proposal exists in SQLite as
>    `PENDING -> APPROVED|CANCELLED|EXPIRED`; the transition is a conditional update, so a replayed or
>    double-tapped callback is a no-op. Idempotent by construction.
> 4. **TTL** - `expires_at` (default 60s). Expired proposals are unexecutable, because an approval
>    arriving after the market has moved is not the trade that was approved.
> 5. **Re-validation at execution** - on approval, the Risk Engine re-runs the **full** battery
>    against *current* prices and state, and current price must be within a stated tolerance of
>    `entry_ref`. If anything fails, the trade is dropped and the user is told why. Approval
>    authorises the *proposal*, never an unconditional order.
>
> Additionally: the bot token is a credential (§20.3) and its compromise must not be sufficient to
> trade - which is exactly what controls 2 and 5 guarantee.

Alert classes: INFO (fills, daily summary), WARN (data staleness, elevated API errors, approaching
limits), CRITICAL (kill switch, daily loss halt, position without stop, reconciliation mismatch,
repeated order failure) - with deduplication and rate limiting so an incident storm cannot drown the
one message that matters.

### 19.3 Emergency Stop / Kill Switch (subsystem 18)

**Triggers.** Manual: Telegram command (allowlisted user, explicit typed confirmation), dashboard
control, and a **filesystem sentinel file** - the last of which works even if the Python process is
wedged, and is checked on every cycle. Automatic: daily loss limit (RC-10), equity drawdown halt
(RC-11), reconciliation mismatch, position detected without a protective stop, API error-rate breach,
market-data staleness beyond budget, unhandled exception in the core loop.

**Actions, in order:** (1) block all new entries immediately; (2) cancel all open non-protective
orders; (3) per configured policy, either hold positions with stops intact or flatten with
`reduce_only` market orders; (4) alert CRITICAL on every channel; (5) write the trigger, the state
snapshot and the action taken to the audit chain.

**Properties:** fails safe (the kill switch engages if its own state cannot be read); **engagement
survives process restart** (persisted, re-read at boot - a crash-restart must not silently resume
trading); **armed by default in LIVE** (§6.4 L3); disarming is manual, human, audited, and never
automatic. Drilled at least once per §11.4.

---

## 20. Storage, audit and secrets

### 20.1 Database & Storage Architecture (subsystem 19) - per ruling X-8

| Store | Role | Why |
|---|---|---|
| **Parquet** (`data/<env>/parquet/symbol=/timeframe=/year=/month=`) | Immutable historical OHLCV, trades, funding | Columnar, compressed, partition-pruned; natural fit for multi-year scans |
| **DuckDB** (`data/<env>/analytics.db`) | Analytical engine over Parquet; research results; backtest runs and metrics | Zero-copy Parquet queries, SQL over columnar, no server |
| **SQLite** (`state/<env>/okxq.db`, WAL) | Live operational state: proposals, orders, fills, positions, kill-switch state, approval state machine | ACID transactions, single-writer discipline, crash-safe - correctness over throughput |
| **Append-only JSONL** (`audit/<env>/`) | Immutable audit trail (§20.2) | Append-only, hash-chained, independently verifiable |

Retention: raw market data indefinite (it is the research asset); operational state 2 years; audit
logs indefinite. Backups: nightly copy of SQLite + audit chain to a second location; Parquet is
reproducible from the venue but backed up to avoid re-fetch cost. **Restore is tested, not assumed.**

### 20.2 Logging & Immutable Audit Trail (subsystem 20)

Two separate streams, not to be confused:

- **Operational logs** - structured JSON, levelled, rotated, correlation-id threaded. Debuggability.
- **Audit trail** - append-only, **hash-chained** (each record stores the SHA-256 of the previous
  record, so any retroactive edit or deletion breaks the chain and is detectable), one chain per
  environment. Recorded: every signal, every risk-check result with its observed values, every
  proposal, every approval or cancellation with who and when, every order and state transition, every
  fill, every kill-switch event, every config change, every promotion sign-off, every LLM call
  (model id, prompt hash, raw response, parsed result) so a qualitative input can always be traced
  back. A chain-verification tool runs on boot and on demand.

**Secrets never enter logs or the audit trail.** A redaction filter is applied at the logging
boundary, and a test asserts that a known secret value cannot appear in any emitted record.

Reconstruction requirement: for any trade, the audit trail alone must answer - what data, what
indicator values, which strategy version, which checks and with what numbers, who approved it, what
was sent, what came back, what it cost.

### 20.3 Secrets handling

Credentials come from environment variables injected by Windows DPAPI-protected user-scoped storage
(or a credential manager) - **never** from a file in the project tree, never committed, never in a
container image, never in an LLM context window. `.gitignore` covers `.env*`, `state/`, `data/`,
`audit/`. Scopes are minimal: DEMO key = trade on demo only; PAPER = read-only or none; LIVE =
not provisioned in Phase 1, and when eventually provisioned, **withdrawal permission disabled and IP
allowlisted**. Rotation procedure documented. A pre-commit secret scan is part of M0.

---

## 21. Testing strategy

| Layer | Scope | Requirement |
|---|---|---|
| Unit | Indicators, risk formulas, contracts, state machines | Risk Engine: **100% branch coverage** |
| Property (Hypothesis) | Risk invariants: *actual risk never exceeds budget*; *size never rounds up*; *no look-ahead* (T-1); *default-deny on malformed input* | Required before any DEMO order is placed |
| Golden/regression | Backtest output fixed for a fixed input set | Any change in results must be deliberate and explained |
| Integration (DEMO only) | Full order lifecycle incl. rejects, partials, cancels, retries, forced-timeout idempotency | Never against LIVE |
| Chaos | Kill network mid-order; kill the process mid-order; stale feed; clock skew; venue 5xx storm; duplicate callback | Must end in a safe, reconcilable state - **never a duplicate position** |
| Guard tests | LIVE unreachable while `PHASE < 3`; no LLM-sourced field referenced in sizing/execution (R-4); no secret in logs | Must fail CI if violated |

---

## 22. Consolidated risk register

| ID | Sev | Risk | Mitigation | Residual |
|---|---|---|---|---|
| **R-1** | HIGH | Mandated gates alone select for overfitting | G-3..G-9, walk-forward, sealed holdout, trial counting, DSR (§11.3) | Non-zero; crypto history is short and regime-poor. Honest expectation: **most candidate strategies will fail, and that is the correct outcome** |
| **R-2** | HIGH | 50 paper trades misread as proof of edge | Re-scoped as an infrastructure gate; edge evidence only from walk-forward + holdout (§11.4) | Low once documented; high if ever forgotten |
| **R-3** | HIGH | Telegram as a trade-authorisation channel | Five controls: allowlist, HMAC, single-use state, TTL, re-validation (§19.2) | Low; token compromise alone cannot trade |
| **R-4** | HIGH | LLM output reaching trade math | No tool binding, closed schemas, numeric isolation, CI guard, degrade-closed (§5) | Low - capability absence, not policy |
| **R-5** | MED | CCXT single point of failure / supply chain | Pinned version, lockfile, thin adapter, DEMO suite on upgrade, direct-API fallback feasible (§16) | Medium - inherent to any dependency |
| **R-6** | HIGH | Duplicate position from ambiguous order send | Deterministic `client_order_id`, no blind retry on UNKNOWN, query-before-act, boot reconciliation (§15) | Low |
| **R-7** | HIGH | Position left without a protective stop | Confirm-or-close sequence, watchdog, kill-switch trigger (§15.3) | Low |
| **R-8** | MED | Hidden correlation - "diversified" alts are one BTC bet | Cluster exposure cap RC-07 driven by measured correlation (§9.1) | Medium; correlations rise in crises, exactly when it hurts |
| **R-9** | MED | Insufficient OKX history depth for multi-year validation | Q-1 measured at M1; secondary backfill source if short | Unknown until measured - **do not assume it is fine** |
| **R-10** | MED | Overnight/weekend gap and liquidation risk on leveraged perps | Leverage cap 3x, liquidation-beyond-stop check, gap-aware backtest fills | Medium - gap risk cannot be eliminated |
| **R-11** | MED | Single-host, no redundancy; this PC is the system | Kill switch survives restart, boot reconciliation, supervised restart, stops live **at the venue** so a dead process still has protection | Medium - accepted for Phase 1-3 scope |
| **R-12** | LOW | Prompt injection via ingested news | No tools in that path, schema clamping, delimiting, quarantine heuristic (S-1) | Low |
| **R-13** | MED | Operator error - acting in the wrong environment | Env banner, per-env everything, required CLI flag, no default, D-2 halts | Low-medium |
| **R-14** | HIGH | **Base-rate risk: most retail algorithmic crypto systems lose money after costs.** Fees, funding and slippage are a large, certain drag against an uncertain edge | Honest cost modelling, 2x cost stress (G-9), cost reconciliation (§17), small risk budget, hard halts | **Material and irreducible. This is the dominant financial risk of the entire project and no amount of engineering removes it.** |

---

## 23. Open questions - Q-4/Q-5/Q-6 RULED, Q-1/Q-2/Q-3 empirical

| ID | Question | Disposition |
|---|---|---|
| **Q-1** | OKX historical depth per timeframe, **OHLCV and funding rates** (§12) | **OPEN - empirical.** Measure at M1 and record in `VENUE_FACTS.md`. If < 3 years on the research timeframe, a secondary source is mandatory before §11.3 is meaningful |
| **Q-2** | Exact CCXT/OKX demo-trading mechanism (§16) | **OPEN - empirical.** Verify at M1/M6 against live CCXT + current docs. Isolated to one function by design |
| **Q-3** | Is TA-Lib buildable on this host, or is `pandas-ta` the only option? (T-2) | **OPEN - empirical.** Attempt at M1; `pandas-ta` is an acceptable fallback |
| **Q-4** | Default risk budget | **RULED 2026-10-02: APPROVED AS PROPOSED.** 0.5% per trade, 3.0% portfolio heat, 1.5% per correlation cluster, 5 concurrent positions, 2.0% daily loss halt, 10% equity drawdown halt, 3x max leverage. Internally consistent: 5 x 0.5% = 2.5% <= 3.0% heat; the cluster cap permits 3 same-cluster positions. Conservative and bounding |
| **Q-5** | HITL always-on, or auto-execute once proven? | **RULED 2026-10-02: HITL MANDATORY THROUGH LIVE LAUNCH.** Auto-execution requires a separate future ruling supported by PAPER evidence. **Not to be designed for now** |
| **Q-6** | Is a 15% MaxDD gate paired with a 10% equity halt (RC-11) self-contradictory? | **RULED 2026-10-02: NOT CONTRADICTORY - CORRECT ORDERING.** The live halt being tighter than the backtest tolerance means live operation stops *before* reaching the worst historically tolerated case |

---

## 24. Chief Advisor verdict

**Date:** 2026-10-02 · **Scope:** this document + `DEVELOPMENT_ROADMAP.md`, Phase 1 design only

### VERDICT: **RECOMMEND TO APPLY** - conditional on prerequisites P-1..P-9 and audit findings F-1, F-2

**Justification.** The traceability table (§3) was checked against the mandate: all 20 subsystems,
both operational workflows, the LLM hard invariant and the three-environment decoupling are each
mapped to a section with a stated *enforcement mechanism*, not merely a description. The design
correctly converts the mandate's two riskiest literal requirements into honest forms - ruling X-4
(the MaxDD/PF gates alone constitute an overfitting engine, hence G-3..G-9) and ruling X-5 (n=50
paper trades re-scoped as an infrastructure gate) - while still enforcing the mandated thresholds.
The LIVE lock is capability-absence rather than a flag, which is the only kind of lock a
configuration defect cannot defeat. Venue-specific facts (Q-1, Q-2) are correctly marked as
to-measure rather than asserted from memory. R-14 is stated as dominant and irreducible, which is the
honest framing.

**Risk assessment.** The risk register (§22) is endorsed as written; **no risk was found understated.**
R-14 (base-rate financial risk) is the dominant *financial* risk and no amount of engineering removes
it. R-1/R-2 are the dominant *methodological* risks and the augmented gate structure is the correct
defence. R-6/R-7 are the dominant *operational* risks and the idempotency and confirm-or-close
designs are adequate.

**Audit findings issued (both applied to the documents 2026-10-02):**

| ID | Finding | Resolution |
|---|---|---|
| **F-1** | **Funding-rate history was missing from M1.** M2's cost model accrues funding at every real funding timestamp (§11.2) and §20.1 stores it, but M1 backfilled OHLCV only - so M2 could not have met its own acceptance criteria | Funding-rate backfill, validation and depth measurement added to M1 deliverables, acceptance criteria and Q-1 |
| **F-2** | **Subsystem 6 (News & Sentiment) had no milestone home.** §3 mapped it to §9.3, but no milestone built it, while M8's acceptance criteria assumed its existence | Assigned explicitly to M8 with deliverables and acceptance criteria; prerequisite **P-9** (LLM credential) added, with the degrade-closed note that its absence never blocks trading |

**Prerequisites before any code executes:** P-1 (install Python 3.12+ - there is no Python runtime on
this host) through P-9, as listed in the roadmap. **P-7 (explicit user authorisation) remains the
final gate - this verdict authorises nothing by itself.**

---

## 25. What Phase 1 explicitly does not do

No code written. No dependency installed. No API key created. No network call to OKX. No order placed
in any environment. No Python runtime installed. No git repository initialised. This document and
`DEVELOPMENT_ROADMAP.md` are the complete Phase 1 deliverable, and implementation begins only after
the Chief Advisor's verdict and the user's authorisation.
