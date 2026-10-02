# okx-quant

A quantitative multi-agent trading system for OKX, built under a strict phased protocol.

> ## ZERO-LIVE-CAPITAL
>
> **This system cannot trade real capital.** The LIVE environment is locked by four
> independent layers (`SYSTEM_ARCHITECTURE.md` §6.4). The strongest is not a flag: **no
> trade-permitted LIVE API credential is ever provisioned**, so no configuration error, code
> defect or LLM output can reach a live order endpoint.
>
> Current phase: **2** (Foundation & Research). LIVE unlocks at phase **4**, and only after
> every criterion in architecture §11.5 plus written human sign-off.

## Status

| | |
|---|---|
| Phase | 2 of 4 - Foundation & Research |
| Milestone | **M1 complete** (data pipeline, venue reconnaissance). M0 complete. |
| Next | M2 - Event-driven backtester + §11.3 acceptance gates |
| Environments buildable | DEMO (M6), PAPER (M7) |
| Environments locked | **LIVE** |

**Measured venue facts live in `docs/VENUE_FACTS.md`** - history depth, API traps, clock skew. Read it
before writing anything that talks to OKX; every number there was measured, not assumed.

## Documents

- **`SYSTEM_ARCHITECTURE.md`** - full design: 20 subsystems, 9 ruled conflicts, data
  contracts, risk formulas, risk register, Chief Advisor verdict (§24)
- **`DEVELOPMENT_ROADMAP.md`** - milestones M0-M9 with acceptance criteria and prerequisites

Read the architecture document's §2 (rulings) and §22 (risk register) before changing
anything. In particular, **R-14**: most retail algorithmic crypto systems lose money after
fees, funding and slippage. Every gate in this project exists to make that visible *before*
capital is committed.

## Setup

Requires Python 3.11+ (3.12 installed and used here) and [uv](https://docs.astral.sh/uv/).

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
```

## Verify

```powershell
.\scripts\verify.ps1     # lint, format, mypy --strict, guard tests, full suite + coverage
```

Or individually:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -m guard -v   # safety guards only
.venv\Scripts\python.exe -m pytest
```

## Run

The environment is a **required** argument. There is deliberately no default.

```powershell
.venv\Scripts\python.exe -m okxq.run --env PAPER   # boots, exit 0
.venv\Scripts\python.exe -m okxq.run --env DEMO    # boots, exit 0
.venv\Scripts\python.exe -m okxq.run --env LIVE    # refuses, exit 2
```

M0 boot validates the environment, creates its isolated directory tree, verifies the audit
chain and appends a `boot` record. **There is no trading loop yet** - the master agent
arrives with M5/M6, and nothing order-capable is built until the Risk Engine is complete and
property-tested (roadmap sequencing constraint).

## Data pipeline (M1)

Public endpoints only - no credential is read, constructed or transmitted.

```powershell
# Backfill: 20 liquid USDT perps, 1d + 1h over 7 years, mark/index for 5, funding for all
.venv\Scripts\python.exe -m okxq.data.cli backfill --env PAPER --symbols 20 --years 7 --timeframes 1d 1h

# Coverage and gap report
.venv\Scripts\python.exe -m okxq.data.cli report --env PAPER
```

Backfill is **idempotent and resumable**: completed calendar-month partitions are skipped, and a
resumed run consults the manifest so it fetches only what is missing rather than re-walking years of
pages. The in-progress month is always rewritten, since it will keep gaining bars.

Storage, per environment:

```
data/<env>/parquet/ohlcv/inst_id=.../timeframe=.../price_type=.../year=.../month=.../part.parquet
data/<env>/parquet/funding/inst_id=.../...
data/<env>/analytics.db          DuckDB views over the Parquet (views, not copies)
state/<env>/data_manifest.db     SQLite coverage manifest: partitions, gaps, rejections, runs
```

Prices are stored as `DECIMAL(38,18)` sourced from the venue's raw **string** fields, so exactness
survives ingestion and DuckDB arithmetic (rule D-1). CCXT's unified `fetch_ohlcv` returns floats,
which is why the implicit endpoints are used instead.

Validation **quarantines, never repairs**: no forward-filling, no interpolation, no clipping. Gaps
are recorded in the manifest, because a missing bar is information - a venue outage or a halt - and
the backtester needs to know which.

## Layout

```
src/okxq/
  phase.py          LIVE lock Layer 2 - the phase constant
  contracts.py      §4 data contracts; D-1 (no float money), D-2 (env tagging)
  errors.py         typed error taxonomy; SafetyError never retried
  env/profiles.py   DEMO/PAPER/LIVE profiles; per-env physical isolation
  audit/chain.py    §20.2 append-only hash-chained audit trail
  obs/              structured JSON logging + secret redaction
  data/             §12 pipeline: okx_public, validate, store, manifest, backfill, cli
  run.py            CLI entry point; --env required
tests/
  guards/           ZERO-LIVE-CAPITAL guards - a failure here blocks all progress
  unit/             contracts, audit chain, validation, store durability, venue traps
scripts/recon_*.py  dated evidence for the Q-1/Q-3 findings in docs/VENUE_FACTS.md
```

## Pre-commit

A secret scan guards against committing a credential - installed **before** any API key exists.

```powershell
.venv\Scripts\python.exe -m pre_commit install
.venv\Scripts\python.exe -m pre_commit run --all-files
```

## The three environments

They answer three different questions and are **not** one flag behind a boolean
(architecture ruling X-3):

| | DEMO | PAPER | LIVE |
|---|---|---|---|
| Question | Is the adapter correct? | Does it behave as backtested, at real latency? | Does it make money? |
| Order endpoint | OKX simulated venue | **none - never called** | real venue |
| Fills from | OKX's simulator | our own fill engine | real matching engine |
| Capital at risk | none | none | real |
| Status | buildable at M6 | buildable at M7 | **LOCKED** |

**Neither DEMO nor PAPER is evidence of profitability.** DEMO fills come from a simulator
whose liquidity does not reflect the real book; PAPER fills come from our own model.

## Hard invariant: LLM confinement

LLMs are confined to qualitative research, regime classification, sentiment scoring and
reporting. **All trade calculation, sizing, risk validation and execution logic is
deterministic code.** This is structural, not a policy: LLM-facing agents are constructed
with read-only tools only, so `place_order`, `size_position` and `arm_kill_switch` are not in
any LLM tool registry - prompt injection has nothing to call. Qualitative inputs are
*monotonically risk-reducing*; none can increase position size or loosen a stop.

## Secrets

Credentials come from environment variables (`OKXQ_DEMO_API_KEY`, `OKXQ_DEMO_API_SECRET`,
`OKXQ_DEMO_API_PASSPHRASE`, etc.) and are **never** committed, never logged, never placed in
an LLM context. `OKXQ_LIVE_*` must not exist - a guard test asserts their absence.
