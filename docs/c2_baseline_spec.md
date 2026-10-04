# Track 1 lower-bound feasibility baseline: specification (pinned before any run)

CYCLE2_DESIGN §9.3, as ruled by the advisor. This is **signal-free**: random entries, nothing selected, **not trials**. The script calls the engine directly, never the walk-forward protocol, and asserts that the trial log is unchanged. Its one use is the stop rule below. It can DISCARD a candidate; it can never qualify one.

## Data
- Research door only: 1h bars and research funding for the 8 members of `docs/universe_m4.json`.
- Trading span: 2020-07-01 00:00 UTC up to the holdout start.
- Each series is loaded from its first bar, so the risk gate's 257-bar ATR buffer exists early. An instrument joins once it has the bars: before that, SZ-2 refuses its entries and the refusal is counted.
- Regime labels: `okxq.analysis.regime.classify` on research 1d bars. A label is usable from its day's close.

## The two brackets (one per candidate), shared rules from CYCLE2_DESIGN §9.1
- **Blocks.** 00–08, 08–16 and 16–24 UTC. For a decision at T with UTC hour h, the block offset is k = h mod 8.
- **Eligible entry offsets.** `session_orb`: k ∈ {2, 3, 4, 5} (after a 2-bar opening range). `impulse_continuation`: k ∈ {1, 2, 3, 4, 5}.
- **Entry draw.** The draw comes from `sha256(f"{seed}|{inst}|{block_start_ms}")`. It decides whether the block trades (probability π), which eligible k, and which side (½ long, ½ short).
  - π = 0.5 for `session_orb`. Its rationale expects about half of blocks to break out.
  - π = 0.12 for `impulse_continuation`. Its rationale range is about 0.08–0.16 per block; 0.12 is the midpoint.
- **Bracket.** Stop 1.0 × ATR; target `target_atr` × ATR as a resting maker order. ATR is the mean of the last 24 true ranges. Both `target_atr` ∈ {1.5, 2.0} are run.
- **Exits.**
  - The forced exit is decided at k = 7 and fills at that bar's open, before the settlement.
  - Otherwise the exit is decided after 6 bars held.
  - Stops and targets are resolved intrabar by the engine.
- **Risk gate.** The full D3 gate (`okxq.backtest.riskgate`), entry order by the hash shuffle.

## Cost configurations
- **LB (lower bound; the decision runs on this).**
  - Measured fees: maker 0.02%, taker 0.05%.
  - Slippage: slip-v2 impact (Y = 1, σ over 24 bars), the frozen stop overshoot k = 0.2, and **no** Abdi-Ranaldo spread (`spread_lookback` = 0, so the spread term is the 1-tick floor).
  - Participation cap 0.1; initial equity 100,000.
- **SV2 (reported only).** Measured fees with the current research slip-v2, including the Abdi-Ranaldo spread.

## Runs
- **Decision runs.** LB, gate mode OBSERVE_HALTS, seeds 0–19 (20), for each of the 4 brackets (2 candidates × 2 targets).
- **Reported alongside:**
  - LB under ENFORCE, seeds 0–4: blocking rates when the halts latch;
  - SV2 under OBSERVE_HALTS, seeds 0–4.

## Measured per run
- **R per closed trade.** net P&L ÷ (max quantity × the stop distance at the decision). The required edge is −mean(R).
- **Exit mix:** stop, target, time or forced exit, other.
- **Same-bar ambiguity share.** For each stop exit, whether the exit bar's range also traded through the target (`high > target` for a long, `low < target` for a short: the engine's strict trade-through rule).
- **Gate:** entries evaluated and approved; refusals by reason; halts by trigger; RC-16 no-label refusals by year.
- **Engine refusals by reason**, and total funding (it should be 0; any nonzero amount is reported, not hidden).

**Summary.** Per bracket and configuration: the mean over seeds of each seed's mean R, and its standard error (std over seeds ÷ √n).

## Stop rule (LB, OBSERVE_HALTS; advisor cutoff)
- A bracket **fails** if its required edge, −mean R, is **> 0.15 R**.
- A candidate is **DISCARDED with no trials** only if **both** of its brackets fail. Its pinned grid contains both targets, so one feasible bracket keeps the candidate.
- The baseline is a **lower bound** on the edge a real signal must supply. Real entries follow high-range bars and cluster across instruments, so the true requirement is higher.

## Known deviations of the research gate from live (recorded in `okxq/backtest/riskgate.py`)
- **The temporary latch slides.** It ends at the first 00:00 UTC at least 24 h after the *last* event at which the halt condition held. Live engages once and waits for a human disarm. Harsher than live, and accepted.
- **No entry-price tolerance re-check** at the fill. It is immaterial on continuous 1h bars.
- **The day boundary** shifts by at most one bar.
- Any engine `insufficient_margin` refusal that follows a gate approval is reported. It would mean the two margin views have drifted apart.

## Report-only diagnostics
- **5m path check** (BTC, ETH and SOL only; research door). For each ambiguous stop exit, read that hour's 5m bars and record whether the target or the stop was touched first. This is the share of ambiguous trades the engine's stop-first rule may have mis-scored. Never used to change the engine.
- **RC-13 blocking rate:** refusals that include RC-13 ÷ entries evaluated, with the halt counts.

## Outputs
- `docs/c2_runs/baseline_<bracket>_<config>_<mode>.json` per bracket and configuration, and `docs/c2_runs/baseline_summary.txt`.
- A `c2_baseline` record on the PAPER audit chain with this file's hash. The ledger snapshot is refreshed and committed.
