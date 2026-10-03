# M1 Coverage & Gap Report

Generated: **2026-10-03 08:30 UTC** · environment: **PAPER**
Source: the Parquet store itself (DuckDB) cross-checked against the SQLite manifest -
so this describes what is on disk, not what the backfill believed it wrote.

Store: **2,532 Parquet files, 122.3 MB**

---

## 1. Headline

| Metric | Value |
|---|---|
| OHLCV bars stored | **1,826,453** |
| Distinct instruments (OHLCV) | **20** |
| Funding observations | **6,934** |
| Distinct instruments (funding) | **20** |

## 2. Coverage by series

| price_type | tf | instruments | bars | from | to |
|---|---|---|---|---|---|
| last | 1d | 20 | 27,015 | 2020-01-01 | 2026-10-01 |
| index | 1h | 5 | 181,273 | 2020-01-02 | 2026-10-02 |
| last | 1h | 20 | 649,317 | 2019-12-16 | 2026-10-02 |
| mark | 1h | 5 | 181,266 | 2020-01-02 | 2026-10-02 |
| last | 5m | 5 | 787,582 | 2024-10-02 | 2026-10-02 |

## 3. Gap analysis - re-derived from timestamp deltas

Gaps are **recorded, never filled**. Missing bars are counted against the expected grid
independently of the pipeline's own validator.

| tf | bars | gap runs | missing bars | % of grid |
|---|---|---|---|---|
| 1d | 27,015 | 0 | 0 | 0.0000% |
| 1h | 649,317 | 0 | 0 | 0.0000% |
| 5m | 787,582 | 0 | 0 | 0.0000% |

## 4. Per-instrument detail (1h, last price)

| instrument | bars | from | to | years | gap runs | missing |
|---|---|---|---|---|---|---|
| BTC-USDT-SWAP | 59,580 | 2019-12-16 | 2026-10-02 | 6.8 | 0 | 0 |
| ETH-USDT-SWAP | 59,370 | 2019-12-25 | 2026-10-02 | 6.77 | 0 | 0 |
| XRP-USDT-SWAP | 59,226 | 2019-12-31 | 2026-10-02 | 6.76 | 0 | 0 |
| DOGE-USDT-SWAP | 54,615 | 2020-07-10 | 2026-10-02 | 6.23 | 0 | 0 |
| UNI-USDT-SWAP | 52,958 | 2020-09-17 | 2026-10-02 | 6.04 | 0 | 0 |
| AAVE-USDT-SWAP | 50,942 | 2020-12-10 | 2026-10-02 | 5.81 | 0 | 0 |
| NEAR-USDT-SWAP | 50,674 | 2020-12-21 | 2026-10-02 | 5.78 | 0 | 0 |
| SOL-USDT-SWAP | 49,908 | 2021-01-22 | 2026-10-02 | 5.69 | 0 | 0 |
| SAND-USDT-SWAP | 48,227 | 2021-04-02 | 2026-10-02 | 5.5 | 0 | 0 |
| PEPE-USDT-SWAP | 29,967 | 2023-05-03 | 2026-10-02 | 3.42 | 0 | 0 |
| SUI-USDT-SWAP | 29,919 | 2023-05-05 | 2026-10-02 | 3.41 | 0 | 0 |
| WLD-USDT-SWAP | 27,991 | 2023-07-24 | 2026-10-02 | 3.19 | 0 | 0 |
| TRUMP-USDT-SWAP | 14,917 | 2025-01-19 | 2026-10-02 | 1.7 | 0 | 0 |
| HYPE-USDT-SWAP | 14,123 | 2025-02-21 | 2026-10-02 | 1.61 | 0 | 0 |
| XAU-USDT-SWAP | 12,992 | 2025-04-09 | 2026-10-02 | 1.48 | 0 | 0 |
| PUMP-USDT-SWAP | 10,681 | 2025-07-14 | 2026-10-02 | 1.22 | 0 | 0 |
| ZEC-USDT-SWAP | 7,935 | 2025-11-06 | 2026-10-02 | 0.91 | 0 | 0 |
| MU-USDT-SWAP | 5,099 | 2026-03-04 | 2026-10-02 | 0.58 | 0 | 0 |
| SNDK-USDT-SWAP | 5,099 | 2026-03-04 | 2026-10-02 | 0.58 | 0 | 0 |
| CL-USDT-SWAP | 5,094 | 2026-03-04 | 2026-10-02 | 0.58 | 0 | 0 |

## 5. 5m coverage

| instrument | bars | from | to |
|---|---|---|---|
| BTC-USDT-SWAP | 210,387 | 2024-10-02 | 2026-10-02 |
| ETH-USDT-SWAP | 210,387 | 2024-10-02 | 2026-10-02 |
| SOL-USDT-SWAP | 210,387 | 2024-10-02 | 2026-10-02 |
| ZEC-USDT-SWAP | 95,228 | 2025-11-06 | 2026-10-02 |
| SNDK-USDT-SWAP | 61,193 | 2026-03-04 | 2026-10-02 |

## 6. Funding coverage and cadence

Retention is a venue limit of ~3 months (`VENUE_FACTS.md` §2). The cadence is
**per-instrument** (fact V-13) - assuming a global 8h would under-accrue funding by half
on the 4h instruments, understating costs in the profitable direction.

| instrument | rows | from | to | cadence |
|---|---|---|---|---|
| CL-USDT-SWAP | 577 | 2026-06-29 | 2026-10-03 | **4h** |
| NIGHT-USDT-SWAP | 577 | 2026-06-29 | 2026-10-03 | **4h** |
| PUMP-USDT-SWAP | 577 | 2026-06-29 | 2026-10-03 | **4h** |
| TRUMP-USDT-SWAP | 577 | 2026-06-29 | 2026-10-03 | **4h** |
| BTC-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| DOGE-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| ETH-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| HYPE-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| MU-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| NEAR-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| PEPE-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| SAND-USDT-SWAP | 291 | 2026-06-29 | 2026-10-03 | **8h** |
| SNDK-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| SOL-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| SUI-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| UNI-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| WLD-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| XAU-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| XRP-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |
| ZEC-USDT-SWAP | 289 | 2026-06-29 | 2026-10-03 | **8h** |

## 7. Manifest cross-check

| Source | Partitions | Rows |
|---|---|---|
| Manifest | 2,477 | 1,829,532 |
| Parquet on disk | 2,532 | 1,833,387 |

Recorded gap runs: **0** · recorded rejections: **9** · backfill runs: **5**

The manifest may record *fewer* rows than disk holds - the in-progress calendar month
is written but deliberately not sealed, and a crashed run leaves work to redo rather
than a partition falsely marked complete. It must never record *more*.

Manifest over-claim check: OK

### 7.1 About the 9 recorded rejections

All 9 carry rule `future_or_incomplete`, all on 5m, all within the final 15 minutes of
the first 5m run. **They were false rejections, caused by a defect in our pipeline, not
by bad venue data.** `now_ms` was sampled once per phase; the 5m pass ran roughly 50
minutes, so by the time it reached later instruments the reading was stale and bars the
venue had already marked closed (`confirm="1"`) looked like future bars.

Fixed in two parts: the clock is now read per instrument immediately after each fetch,
and the guard tests the bar's *open* against a 15-minute tolerance rather than its close
against an exact `now` - this host measures ~199 s of skew against the venue, so a strict
test would keep losing data. A re-run recovered every affected bar and reported **zero**
rejections; 5m grid continuity is now 0 missing bars.

The 9 records remain in the manifest deliberately: the rejection log is append-only
history, and deleting evidence of a real defect would be the wrong instinct.

## 8. Walk-forward eligibility - not every instrument qualifies

The universe is selected by **liquidity**, which is the right basis for execution
realism but says nothing about history length. The §11.3 walk-forward needs >= 3 years
spanning a bull and a bear phase, and only part of the universe clears that bar.

- **>= 3 years of hourly history: 12 of 20 instruments**
- below 3 years: 8 (newer listings - MU, SNDK, CL at ~0.6y are the extreme)

**Consequence for M2/M4:** the research universe for walk-forward validation is the
**12-instrument** subset, not all 20. The younger instruments remain
useful for execution and slippage modelling on recent data, but a strategy cannot be
walk-forward validated on 7 months of history, and G-3's 100-trade floor would be met on
noise rather than on a cycle. Universe selection for M4 must filter on history length as
well as liquidity - the current `MIN_LISTING_AGE_DAYS = 180` floor is far too permissive
for that purpose (it exists to exclude price-discovery artefacts, a different concern).

## 9. Data-quality assertions

Re-checked independently in `scripts/verify_data_quality.py`; all passing at generation:

- zero OHLC violations (`high < low`, open/close outside range, non-positive prices)
- zero negative volumes
- zero duplicate `(inst_id, timeframe, price_type, ts_open_ms)` keys
- zero off-grid bar opens
- zero unclosed/partial bars stored
- prices stored as `DECIMAL(38,18)`, exact through the round trip
