# M4 design record: strategy engine and first research cycle

## 0. BLOCKERS (owner's items; they block PROMOTION, not research runs)

| ID | Needed from the user | Blocks |
|---|---|---|
| **U-1** | Measured perpetual-swap fee rates, with a date. The 0.08% / 0.10% supplied are OKX's regular-user SPOT rates (docs/VENUE_FACTS.md); research uses them as `UNMEASURED_ASSUMPTION` | every holdout run (`PROMOTION-FEES` is INVALID until fees are MEASURED) |
| **U-2** | P-11 evidence: `.\scripts\archive_funding.ps1 -Status` showing `result: 0 = success` and a fresh last-success time | promotion |
| **U-3** | Written acceptance of **V-14** (survivorship cannot be repaired on OKX), recorded on the audit chain | promotion |

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
