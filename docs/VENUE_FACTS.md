# OKX Venue Facts - Measured, Not Assumed

Measured: **2026-10-02** · ccxt **4.5.85** · Python 3.12.10 · Windows 11
Method: public REST endpoints only, no credentials. Raw evidence in `docs/q1_raw.json`,
`docs/q1_funding_raw.json`, `docs/q1_proxy_raw.json`; probe scripts in `scripts/recon_*.py`.

> **Every number in this document was measured on the date above.** Nothing here is recalled from
> documentation or assumed from another venue. Venue behaviour changes - **re-measure before relying
> on any of this in a later milestone**, and update the date.

---

## 1. Q-1 RESOLVED - OHLCV history depth: **sufficient**

Method: binary search for the earliest timestamp returning data. `probe(t)` is monotonic in `t`
(no data below the boundary, data above it), so the search is valid at ~19 requests per series.

| Symbol | listTime | 1m | 5m | 1h | 1d |
|---|---|---|---|---|---|
| BTC/USDT:USDT | 2019-11-12 | **6.80y** (2019-12-16) | **6.80y** (2019-12-15) | **6.83y** (2019-12-03) | **7.57y** (2019-03-07) |
| ETH/USDT:USDT | 2019-11-12 | 6.77y (2019-12-24) | 6.78y (2019-12-23) | 6.81y (2019-12-12) | 7.57y (2019-03-07) |
| SOL/USDT:USDT | 2021-01-22 | 5.69y (2021-01-22) | 5.70y (2021-01-21) | 5.73y (2021-01-09) | 6.51y (2020-03-29) |

**Verdict: PASS.** ~6.8 years on 1m/5m/1h for the majors, against the §11.3 walk-forward requirement
of >= 3 years spanning a full bull and bear phase. The 2021 bull, the 2022 bear and the 2020 crash
are all inside the sample. **Risk R-9 is closed favourably. No secondary OHLCV source is needed.**

> **Correction to the raw output.** `q1_raw.json` labels the 1m/5m/1h boundaries
> `limited_by=RETENTION`. That label is an artefact of the probe's 2-day threshold and is **wrong**.
> A rolling retention window would sit at a fixed distance from *now*; these boundaries sit at a
> fixed *calendar date* (~Dec 2019), roughly a month after listing. The correct reading is simply
> **"data begins ~Dec 2019"**. 1d data predates the swap listing because OKX backfills the daily
> series from the underlying index.

### Throughput

| Timeframe | Measured | Full backfill, one symbol |
|---|---|---|
| 1h | ~864 bars/s | ~1 min |
| 1m | ~877 bars/s | **~68 min** |

Single-request latency: ~300 ms median (5 samples: 315, 301, 305, 572, 304 ms).

**1m is deliberately not backfilled at M1** - ~68 min per symbol, no acceptance criterion requires
it, and no strategy yet demands it. Revisit when one does.

---

## 2. Q-1 PARTIAL - Funding-rate history: **~3 months only**

| Symbol | Oldest retained | Depth | Rows | Interval |
|---|---|---|---|---|
| BTC/USDT:USDT | 2026-06-29 08:00 | **0.26y** | 286 | 8h |
| ETH/USDT:USDT | 2026-06-29 08:00 | **0.26y** | 286 | 8h |
| SOL/USDT:USDT | 2026-06-29 08:00 | **0.26y** | 286 | 8h |

The boundary is **identical across all three symbols** and sits ~95 days from the measurement date,
while the instruments listed in 2019 and 2021. That is a **venue retention limit**, not a listing
effect. It matches OKX's documented "up to three months". CCXT exposes exactly one funding-history
endpoint for OKX (`public/funding-rate-history`), so there is no deeper route through CCXT.

**This collides with M2.** The backtester accrues funding at every real funding timestamp
(architecture §11.2) across a multi-year walk-forward, and only ~3 months of realised funding exists.

### Ruling (Chief Advisor, 2026-10-02) - four parts

1. **Archive realised funding from now on.** The window rolls, so every week of delay permanently
   loses a week of ground truth. Implemented in M1 (`run_funding`, full retained depth for the whole
   universe) and **must run on a schedule** from here on.
2. **Reconstruct pre-2026-06 funding from the mark/index premium.** Both series carry **6.76y**
   (§3 below), so this is feasible. It is a **model, not the realised rate**, and is gated: over the
   ~3-month overlap where realised funding exists, the reconstruction must reproduce realised
   *cumulative* funding drag within a stated tolerance (provisional: 20% relative error on cumulative
   drag plus sign agreement on the large majority of intervals; finalise from the measured error
   distribution, and escalate again if it cannot be met). **Do not assert OKX's funding formula from
   memory when building this** - the empirical check against realised data is the gate either way.
3. **G-9 backstops the model error.** The 2x cost-stress gate applies to modelled funding too.
4. **A third-party archive supersedes the model where it covers.** See §6.

**No M1b milestone and no roadmap restructure. The §11.3 walk-forward remains executable.**

---

## 3. Mark and index candles - the reconstruction inputs

| Series | Earliest (1h) | Depth |
|---|---|---|
| last | 2019-12-12 | 6.81y |
| **mark** | 2019-12-29 | **6.76y** |
| **index** | 2019-12-29 | **6.76y** |

Both are deep enough to model funding across the whole OHLCV sample.

---

## 4. API mechanics - traps found the hard way

Each of these cost real debugging time at M1. They are recorded so they are paid for once.

| # | Fact | Consequence |
|---|---|---|
| **V-1** | Candle endpoints honour **max 300 rows**. Requesting 500 or 1000 silently returns 300. | Never trust a requested limit; page on what came back. |
| **V-2** | Candle rows are returned **newest-first (descending)**. | Must be reversed; the store and backtester are chronological. |
| **V-3** | `after` pages **backward** (rows older than the given ts); `before` pages **forward**. The names read backwards from intuition. | Backward walks use `after`. |
| **V-4** | **CCXT's `fetch_funding_rate_history` maps only `since` -> `before` and silently ignores `until`.** | Passing `until` paginates *nowhere* - the venue returns the same recent window every call. This produced a false "33 days of funding history" reading during recon until caught. Backward walks must pass a raw `after` cursor. |
| **V-5** | CCXT `fetch_ohlcv` auto-switches to `HistoryCandles` when `since < now - 1439*duration`, and brackets with `before = since-1`, `after = since + duration*limit`. | A `since` older than the instrument's history returns **empty**, not "oldest available" - so earliest-data must be *searched* for, not read. |
| **V-6** | **`fetch_tickers()` returns SPOT only** on OKX; swap symbols are absent entirely. | Needs `params={"type": "swap"}`. Without it, universe selection silently yields **zero** instruments - no error. |
| **V-7** | **CCXT reports `quoteVolume = None` for OKX swaps, and its `baseVolume` is OKX's `vol24h`, which counts CONTRACTS, not base units.** For BTC-USDT-SWAP one contract is 0.01 BTC, so reading `baseVolume` as base currency **overstates size by 100x**. | Turnover must be computed: `volCcy24h * last`. Verified: `vol24h * contractSize == volCcy24h`. |
| **V-8** | **Index candles are published per instrument FAMILY** (`BTC-USDT`), not per instrument (`BTC-USDT-SWAP`). | Passing the instId returns OKX error **51001** "Instrument ID ... doesn't exist". Index data is fetched by family but stored under `inst_id` so it still joins. |
| **V-9** | The `confirm` field is `"1"` for a closed bar, `"0"` for the in-progress bar. Last-price rows have 9 fields, **mark/index rows have 6 and carry no volume at all** - their `row[5]` is `confirm`. | Reading `row[5]` as volume for mark/index would silently store 0/1 as turnover. |
| **V-10** | Raw REST responses carry **all numeric fields as strings**; CCXT's unified `fetch_ohlcv` parses them to `float`. | Ingesting via the implicit endpoints preserves exactness, honouring rule D-1 end to end. |
| **V-11** | Volume fields: `vol24h` = contracts, `volCcy24h` = base currency, and for candles index 5/6/7 = contracts/base/quote. | All three are stored so nothing downstream has to guess. |

### Non-venue trap worth recording

| # | Fact | Consequence |
|---|---|---|
| **V-12** | Python's **default decimal context is `prec=28`**. | Quantising to scale 18 fails with `InvalidOperation` for any value above 10 integer digits - e.g. an ordinary **$91bn daily quote volume**. It rejected every 1d bar for BTC and ETH until `to_decimal` was given a local context at `prec=38`. |

---

## 5. Clock skew - **a hard pre-M6 blocker**

Measured three times, latency-compensated, with round-trip times of 269-356 ms:

```
skew ~= +199049 ms    (rtt 269 ms)
skew ~= +199092 ms    (rtt 356 ms)
skew ~= +199051 ms    (rtt 295 ms)
```

**The host clock is ~199 seconds (3 min 19 s) ahead of OKX's server.** Consistent across samples and
far beyond any latency artefact, so it is real.

**Harmless for M1**, whose requests are entirely unsigned. **Not harmless for M6**: OKX rejects
*signed* requests whose timestamp drifts beyond its tolerance, which is far below 199 s.

**Pre-M6 prerequisite P-10, two parts:**

1. **Resync the host clock** - `w32tm /resync` (elevated), and confirm the Windows Time service is
   set to start automatically. *This is a change to the user's machine and is their call, not
   something to do unasked.*
2. **A boot-time skew check in the adapter** (§16) that refuses authenticated operations when skew
   exceeds the venue's tolerance. **Measure that exact tolerance at M6** - it is not asserted here.

---

## 6. Third-party funding archives - exist, all commercial

Searched at M1 per the advisor's ruling (part 4). Realised funding beyond OKX's ~3-month window is
available from data vendors, none free:

| Provider | Claimed OKX coverage |
|---|---|
| Tardis.dev | tick history from 2018+, S3/NDJSON/CSV |
| CoinAPI | flat files via S3, ~3-4 years |
| Amberdata | OKX from 2019-07-11; swaps from 2019-07-12 |
| CryptoHFTData | funding, mark and index as Parquet/Zstd |

**Not verified by us** - these are vendor claims from a web search, not measurements. One blog result
also claimed a 400-record endpoint "≈1 year"; that is arithmetically wrong (400 x 8h = 133 days) and
contradicts our own measurement, so it was discarded. **Our 95-day figure is primary evidence and
takes precedence.**

**Decision for the user, not yet taken:** pay a vendor for realised funding over the full span, or
proceed with the §2 reconstruction plus its validation gate. The reconstruction path is implemented
and costs nothing; the vendor path is strictly better data for the span it covers.

---

## 7. Universe as measured

485 active USDT-margined perpetual swaps (of 4,479 total markets). Top turnover at measurement time:

| Symbol | 24h turnover (computed per V-7) |
|---|---|
| BTC/USDT:USDT | ~$9.10bn |
| ETH/USDT:USDT | ~$7.74bn |
| ZEC/USDT:USDT | ~$1.40bn |

---

## 8. Q-3 RESOLVED - TA-Lib works

`ta-lib==0.8.1` installed **from a prebuilt Windows wheel**; no C toolchain, no Visual Studio build
tools, no fallback needed. 201 functions available.

Verified by computation, not by import alone:

* `SMA(5)` on the ramp 1..10 returns 4 NaNs then exactly 3.0 and 8.0 - matches hand calculation.
* `RSI(14)` over a 500-step random walk stays within [0, 100] (observed 23.01-76.04).
* `ATR(14)` produces a positive finite value from high/low/close.

**Verdict: TA-Lib is the primary indicator library. `pandas-ta` is not required.** Declared in
`pyproject.toml` and pinned in `uv.lock`, so `uv sync --locked` reproduces it in CI - it is **not**
an ad-hoc local install.

Per architecture **T-2**, any indicator we implement ourselves is still validated against TA-Lib as
the reference at M3, and every indicator must pass the **T-1** no-look-ahead property test.
