# M5 design: Risk Engine + Portfolio Manager + Kill Switch (PLAN, for Chief Advisor review)

Status: **PLAN. Nothing is built until the advisor rules.** Scope: roadmap M5, architecture §13, §14 and §19.3. Pure, offline, no network, no credentials. Phase stays 2; no LIVE path is touched.

## 0. Constraints carried in

- **`src/okxq/backtest/` is not changed.** The M4 frozen research sizing (`ResearchSizing`, e32046ed) stays exactly as it is (closing audit). The risk engine is a separate package and is **not** wired into research runs in M5 (§9 Q4).
- **No milestone skipping.** M6 stays blocked: no strategy passed (Phase-2 exit not met).
- The existing contracts (`Signal`, `RiskCheckResult`, `TradeProposal`, `RegimeLabel`, `SentimentScore`, `Position`) are used as they are. No contract change is planned; any change needs its own reviewed commit.

## 1. Package layout: `src/okxq/risk/`

| module | role | purity |
|---|---|---|
| `policy.py` | `RiskPolicy`: every threshold, frozen and pinned by SHA-256 (the `FrozenGates` pattern). Other values refuse to construct | pure |
| `inputs.py` | `PortfolioSnapshot` and `MarketFacts`: frozen, Decimal-only input records for one decision cycle | pure |
| `sizing.py` | §13.2 fixed-fractional sizing and every post-sizing validation | pure |
| `checks.py` | RC-01..RC-12 plus the §13.3 extras, one pure function each | pure |
| `qualitative.py` | §13.4 gating. **Output is only a multiplier in (0, 1] and vetoes** | pure |
| `engine.py` | `evaluate(signal, snapshot, facts, qual) -> TradeProposal`. Runs every check, records all of them, default deny | pure |
| `portfolio.py` | Portfolio Manager state: open risk, cluster exposure, equity curve, high-water mark, UTC-day P&L, consecutive losses. A pure reducer `apply(state, event) -> state`, plus a persistence adapter | reducer pure; adapter does I/O |
| `killswitch.py` | Persisted kill-switch state, filesystem sentinel, fail-safe read, audited engage and disarm | I/O |

**100% branch coverage applies to `policy`, `inputs`, `sizing`, `checks`, `qualitative`, `engine` and the `portfolio` reducer.** `killswitch` and the persistence adapter are tested for their failure modes, also to 100% branch coverage where reachable without fault injection; any line that isn't reachable is listed by name in the design record.

## 2. RiskPolicy (frozen; values per §13.3, plus the ones §13 leaves open)

| field | value | source / why |
|---|---|---|
| max_risk_per_trade | 0.005 | RC-05; equals ResearchSizing |
| max_portfolio_heat | 0.03 | RC-06 |
| cluster_cap | 0.015 | RC-07 |
| max_positions | 5 | RC-08 |
| daily_loss_limit | 0.02 | RC-10. Halt when the loss is **≥** the limit; "< limit" passes |
| max_dd_halt | 0.10 | RC-11. Halt when drawdown from the high-water mark is ≥ 0.10 |
| max_leverage | 3 | RC-12 |
| min_stop_atr | 0.5 | §13.2 k·ATR floor. Open: not set by the architecture |
| min_stop_ticks | 10 | §13.2 m·tick floor. Open |
| liq_buffer_stop_multiple | 1.5 | Liquidation must sit at least 1.5 × stop distance beyond entry, on the stop side. Open |
| margin_headroom | 0.25 | At least 25% of free margin remains after the order. Open |
| staleness_bars | 1 | RC-03: the last closed bar is no older than one timeframe plus a 120 s grace. Open |
| max_entries_per_hour | 3 | §13.3 anti-runaway. Open |
| loss_cooldown | 3 consecutive losses → 24 h with no new entries | §13.3. Open |
| proposal_ttl_s | 60 | `TradeProposal.expires_at`. Open |
| qual_disagree_multiplier | 0.5 | §13.4 |
| sentiment_veto | \|score\| > 0.6 with confidence > 0.7 | §13.4 |
| day_boundary | UTC 00:00 | §13.3 |

"Open" means §13 sets no value. Each is set from first principles in the record, never fitted to anything. Q1 asks the advisor to rule on them.

## 3. Sizing (§13.2), exactly as specified

- `risk_capital = equity_frozen × min(policy.max_risk_per_trade, policy ceiling)`
  - The equity is frozen once per cycle, a conservative mark-to-market at mark prices.
  - Signal has no requested-risk field and `conviction` must not scale size (§13.4), so the strategy request is the ceiling.
- `stop_distance = |entry − stop|`. It must be non-zero and on the correct side for the trade direction.
- `qty = round_DOWN(risk_capital / stop_distance, lot)`
- Then REJECT if any of these holds:
  - qty < min_size (never inflated to reach the minimum);
  - qty > max_size;
  - actual_risk > risk_capital (asserted, and should be unreachable);
  - the stop is under the ATR or tick floors;
  - no leverage in {1, 2, 3} meets margin plus headroom;
  - the liquidation estimate is not beyond the stop by the buffer.
- **Leverage:** the smallest of {1, 2, 3} that fits margin plus headroom. Lower leverage pushes liquidation further away.
- **Isolated-margin liquidation estimate:** long `entry·(1 − 1/lev + mmr)`, short `entry·(1 + 1/lev − mmr)`, using the measured tier-1 mmr (M2 open issue 11: it understates above tier 1, which is stated).
- **Default deny:** every arithmetic step runs on validated Decimals. Any `None`, `NaN`, infinity, ≤ 0 price or exception REJECTs with the reason recorded.

## 4. Checks: all evaluated, never short-circuited (§13.1)

`engine.evaluate` iterates a **module-level tuple `BATTERY`** of check functions. Each takes `(signal, sizing, snapshot, facts, policy)` and returns `RiskCheckResult(check_id, passed, observed, limit, detail)`. An exception inside a check becomes a **failed** result (`detail="exception: …"`), never a pass. The verdict is APPROVED only if every result passed and the sizing is valid. A rejected proposal carries qty 0.

| ID | Check | Observed / limit recorded |
|---|---|---|
| RC-01 | Kill switch disengaged (snapshot carries the switch state, read fail-safe by the caller) | state |
| RC-02 | `signal.env == snapshot.env == facts.env`, and PHASE permits env | — |
| RC-03 | Market-data freshness for the symbol | bar age / budget |
| RC-04 | Symbol tradable: venue state is live, not in maintenance, not flagged for delisting | — |
| RC-05 | Per-trade risk | actual_risk_pct / 0.005 |
| RC-06 | Open risk plus this trade, over equity | heat / 0.03 |
| RC-07 | Cluster open risk plus this trade, over equity | cluster heat / 0.015 |
| RC-08 | Open positions + 1 | count / 5 |
| RC-09 | One position per symbol, no opposing position, no duplicate signal for the symbol in this bar | — |
| RC-10 | UTC-day loss (realised + unrealised) against equity at 00:00 UTC | loss / 0.02 → **halt flag** |
| RC-11 | Drawdown from the high-water mark | dd / 0.10 → **halt flag** |
| RC-12 | Leverage, notional, margin headroom, min/max/step, liquidation buffer | per sub-check |
| RC-13 | Consecutive-loss cooldown (§13.3 extra) | losses / 3 |
| RC-14 | Entries in the trailing hour (§13.3 extra) | count / 3 |
| RC-15 | Venue API error-rate breaker. M5 input only: an unknown value means REJECT | flag |
| RC-16 | Qualitative vetoes (§13.4): sentiment veto, and CRISIS blocks new entries | — |

The engine is pure, so RC-10 and RC-11 don't engage the kill switch themselves. They return a `halt` signal on the proposal (in `detail`), and the **caller** must engage the switch. A test asserts that the reference caller (`risk.cycle.decide`) engages it.

## 5. Clusters (RC-07): frozen rule from the R-8 result

R-8 E4 found that crypto co-moves more under stress: in the worst month, the full-history set's mean pairwise rho rose from 0.63 to 0.75. The frozen rule therefore puts **every crypto perpetual in one cluster `CRYPTO`**. XAU is `METAL`, CL is `ENERGY`, and MU and SNDK are `EQUITY`. Membership comes from a committed asset-class map (`docs/risk_clusters.json`), not from rolling correlation, so it cannot drift calm-period-low the way rolling rho does (R-8 report). Consequence: at most three full-size crypto positions. 1.5% / 0.5% = 3, before RC-06's 3% cap.

## 6. Qualitative gating (§13.4): monotone by construction

`qualitative.gate(label, sentiment) -> (multiplier ∈ {1, 0.5}, veto_long, veto_short, block_entries)`:
- `RULE_LLM_DISAGREE` gives ×0.5;
- the sentiment thresholds veto one side;
- `CRISIS` blocks new entries;
- missing sentiment is neutral.

**Property test:** for any qualitative input, the approved qty ≤ the baseline qty, and no REJECT → APPROVE flip. **R-4 guard (AST):** `sizing.py`, `checks.py` and every future execution module may not import or reference `SentimentScore`, `RegimeLabel`, `conviction`, `score`, `confidence` or `determined_by`. Only `qualitative.py` may, and only `engine.py` imports `qualitative`, applying the multiplier to `risk_capital` **only as `min(base, base × m)`**.

## 7. Portfolio Manager (§14), M5 scope

- **Built now:**
  - The pure reducer over events (`Opened`, `Filled`, `MarkUpdate`, `Closed`, `FundingAccrued`, `DayRollover`), producing open risk, cluster exposure, gross and net exposure, equity curve, high-water mark, day-open equity at UTC 00:00, realised P&L today, consecutive losses and entries in the last hour.
  - That state feeds `PortfolioSnapshot`.
  - Persistence: SQLite, one transaction per event, mirrored to the audit chain, with replay of the event log reproducing state bit-for-bit.
- **Deferred to M6/M7, needing an execution path:** scale-outs, ATR trailing stops, breakeven moves, time exits, and venue reconciliation (§15.4). They manage orders, and M5 has no order path. Q3 asks the advisor to confirm.

## 8. Kill switch (§19.3)

- **State:** `state/<env>/killswitch.json`, written atomically (tmp + fsync + rename). The **sentinel** is `state/<env>/KILL`; its mere existence engages the switch, so it works with the process wedged and is checked on every `engaged()` call.
- **`engaged()` is True if** the sentinel exists, OR the state says engaged, OR the state file is missing and the profile is armed by default (LIVE), OR the state is unreadable or malformed (**fail-safe**).
- **`engage(trigger, snapshot)`** persists the switch and appends the trigger and snapshot to the audit chain. It is idempotent.
- **`disarm(operator, typed_confirmation, reason)`** is manual only. The confirmation must equal the exact phrase `DISARM <env>`. Disarming is audited. **No code path disarms automatically**: an AST guard forbids calls to `disarm` outside the CLI module.
- **Actions in M5:** block entries (RC-01). The order cancel and flatten actions need execution; they are **interfaces only** (`KillActions` protocol) until M6.
- **Tests:**
  - each trigger engages;
  - engagement survives a new process (subprocess test);
  - an unreadable or garbage state file reads as engaged;
  - a sentinel overrides a disengaged state;
  - disarm with a wrong phrase is refused;
  - LIVE with no state file reads as engaged.

## 9. Acceptance mapping and enforcement

- **100% branch coverage:** a CI step, `pytest --cov=okxq.risk --cov-branch --cov-fail-under=100` over the pure modules. A **guard forbids `pragma: no cover` and `# type: ignore` in `okxq.risk`**. `pyproject` excludes `pragma: no cover` lines from coverage, so the pragma would otherwise be a way to fake 100%.
- **Hypothesis properties, over wide random Decimal inputs including None, NaN, 0, negatives and extremes:**
  - actual risk ≤ budget;
  - qty is never rounded up (qty·stop_distance ≤ risk_capital, and qty + lot would exceed it, or qty hit a cap);
  - qty < min gives REJECT;
  - a malformed input gives REJECT;
  - a wrong-side stop gives REJECT;
  - an approved trade always has its liquidation beyond the stop by the buffer;
  - qualitative input is monotone.
- **Fixtures:** for every RC, one passing and one failing fixture, with exact observed and limit values asserted. **RC-10 and RC-11 are tested at exactly the threshold, at threshold − ε and at threshold + ε, and across the UTC midnight boundary** (23:59:59.999 against 00:00:00.000).
- **No-bypass test:**
  - `evaluate`'s signature has no flags;
  - `RiskPolicy` is pinned and refuses other values;
  - every proposal records exactly `{RC-01..RC-16}`;
  - an AST guard finds no `os.environ` or `getenv` in `okxq.risk`, and no conditional around a `BATTERY` entry;
  - `BATTERY` is a tuple, not mutable, and a test monkeypatching it is not part of the API.
- **R-4 guard:** see §6.
- **Isolation guard:** `okxq.risk` imports no `ccxt`, `requests`, `socket`, `okxq.data` or `okxq.backtest.holdout`.

## 10. Questions for the advisor

1. The "Open" policy values in §2: accept them, change them, or require each to come from a measurement? For example, `min_stop_atr` from the M4 candidates' stop distribution. That would be fitting to strategy behaviour, so I'd rather set them from first principles.
2. **RC-16 and CRISIS:** §13.4 blocks entries on `regime == CRISIS`, but M3 made every regime label descriptive (it failed out-of-sample). Blocking on CRISIS only ever reduces risk, so a false positive costs opportunity, not capital. Implement as specified, with the validation status recorded? Or hold it out until the regime revision is spent?
3. Is the Portfolio Manager scope split (§7) acceptable?
4. M5 is not wired into research. A later research cycle that runs under the risk engine (heat, cluster cap, max positions) would be a sizing-model change: a new pinned config and new trials. Confirm that it is out of M5.
5. Is anything that §13, §14, §19.3 or the M5 acceptance require missing?
