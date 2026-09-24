# Stage 1 local market store and initial sync

## Contract

AmazingData is the acquisition source. Historical research reads the ignored `data/market_store/` Parquet files through `research.market_store.LocalMarketStore`; it never needs an AmazingData session. The store is mutable and is **not** the frozen `REAL_RESEARCH_SNAPSHOT_V1`. Store schema version is `1`. D8 and all strategy studies remain separate work.

The initial window is 2024-01-01 through 2026-09-23, inclusive. The universe is the vendor's 5,222 SH/SZ A-share IDs as of the end date; rows before each security's listing date are excluded. SH and SZ session lists in the previously captured AmazingData calendar are equal over this window. The canonical security-day key is `(security_id, trade_date)`, and each monthly file is ordered by `(trade_date, security_id)`.

## Measured initial sync

The completed store contains 5,222 securities and 662 trading sessions (2024-01-02 through 2026-09-23). Independent DuckDB readback verified every manifest row count and found zero null or duplicate canonical security-day keys.

| Dataset | Rows | Securities | Sessions | Parquet bytes |
| --- | ---: | ---: | ---: | ---: |
| security_master | 5,222 | 5,222 | — | 53,383 |
| trading_calendar | 662 | — | 662 | 1,690 |
| daily_bars | 3,375,103 | 5,222 | 662 | 66,581,369 |
| daily_status | 3,379,281 | 5,222 | 662 | 24,964,912 |
| adjustment_factor | 3,380,290 | 5,222 | 662 | 6,679,795 |

The canonical Parquet files total 98,281,149 bytes; the store including metadata is 98,286,072 bytes. The raw D4/D6 response contained 1,009 rows with both `MARKET_CODE` and `TRADE_DATE` absent; some still carried rate/status values. They cannot safely be assigned to a security-day, so they remain in ignored staging and are excluded from canonical status. No value was fabricated. Canonical status has zero missing key status fields; bars have zero invalid OHLC or negative volume/amount rows; factors have zero missing/non-positive rows. The count difference between bars, status, and factors is retained as reported source coverage, not filled by synthetic observations.

The checkpointed active acquisition segments sum to 8,367.874 seconds (2.3244 hours): D1 39.167, D3 3,674.187, D4/D6 2,812.841, and D5 1,841.679 seconds. This measured lower bound is 15.36% above the prior 2.015-hour estimate and below its 2.419-hour buffer. True active time may exceed the buffer: the D4/D6 and D5 processes each stopped during a request, and their final in-flight seconds were not checkpointed. Engineering pauses and Parquet materialization are excluded from the acquisition comparison. The D1 bounded interruption, D4/D6 batch transition, and D5 recovery all resumed from completed cache records; the latter skipped 41 completed batches.

## Files and boundaries

`data/market_store/` contains `security_master/part.parquet`, `trading_calendar/part.parquet`, monthly `daily_bars/year=YYYY/month=MM/part.parquet`, `daily_status/...`, `adjustment_factor/...`, and `metadata/manifest.json`. Both this directory and `.local_research_data/` are ignored by Git. Full raw vendor responses, SDK caches, and checkpoint records remain under `.local_research_data/market_store/`. They are not research inputs. The DuckDB connection queries Parquet directly; there is no second permanent DuckDB table or service.

Schema version 1 columns: `security_master(security_id, exchange, board, listing_date, delisting_date, security_name)`; `trading_calendar(trade_date, is_trading_day)`; `daily_bars(security_id, trade_date, open, high, low, close, volume, amount)`; `daily_status(security_id, trade_date, preclose, high_limited, low_limited, price_high_lmt_rate, price_low_lmt_rate, is_st_sec, is_susp_sec, is_wd_sec, is_xr_sec)`; `adjustment_factor(security_id, trade_date, factor)`. The factor remains the previously qualified D5 backward-factor series, not a newly defined adjustment convention.

`scripts/sync_market_store.py` acquires one endpoint phase at a time into raw cache. Each record tracks endpoint, batch/security IDs, window, status, attempts, timestamps, rows and SHA-256. A completed record with a matching cache hash is skipped on restart; failed or interrupted records are retried. D3 uses one security per request and D5 uses the qualified batch size 100. D4/D6 began one-security-at-a-time, then a bounded 5- and 20-security probe verified exact cached-value parity and a material speed gain. `scripts/sync_market_status_batch.py` resumes the remaining D4/D6 IDs in batches of 20, retaining per-security records and checkpointing each complete batch. Requests have finite retries and SDK session recovery. The `--stop-after` argument intentionally stops after a bounded number of new records for the D1 resume test.

`scripts/finalize_market_store.py` requires all four endpoint phases complete, checks keys and required fields, normalizes data, and writes Parquet through a temporary file, readback validation and atomic file replace. Small exact duplicates collapse; conflicting duplicate keys are excluded rather than choosing an arbitrary winner. Invalid raw rows remain in ignored staging for inspection. Systemic anomalies fail the build. The manifest is written last. `scripts/verify_market_store.py` independently reopens every canonical dataset and recomputes counts and key uniqueness. A failed API request never writes a canonical file. A failed partition write leaves its previous file intact. Monthly partitions, rather than one file per security, keep cross-sectional research scans practical.

## Cache reuse and a corrected D1 caveat

The 100-security D3/D4 benchmark and 500-security D5 sustained batch caches are reused only after verifying benchmark dates, identities, schema and file hashes. Their exact contribution and avoided calls are in `artifacts/stage1_local_market_store/cache_reuse_summary.json`.

Measured reuse was 62,126 D3 rows (100 calls avoided), 62,239 D4/D6 rows (100 calls avoided), and 318,150 D5 rows (five batch calls avoided): 442,515 rows and 205 production remote calls avoided across 500 distinct securities. Five separate D4/D6 batch-validation probe calls were made and are not netted into that avoided-call count.

During this run, the earlier D1 CSV was initially judged to have corrupt Chinese names because the terminal rendered them as replacement glyphs. Inspecting Unicode code points later showed the stored text was valid. The D1 remote sync had already completed. Consequently, D1 old-cache rows were **not** reused and no D1 calls were avoided; this is a measured inefficiency, not a data-quality defect. Subsequent rebuilds can reuse the current complete D1 staging without another remote fetch.

## Incremental updater

`scripts/update_market_store.py --end YYYY-MM-DD` is a scheduler-ready command (exit 0 on success/no-op, nonzero on failure). It compares each dataset's actual stored dates with the exchange calendar, including interior gaps; D3 and D4 request only missing sessions. `--refresh-start`, `--refresh-dataset`, and `--security` explicitly permit a bounded dataset/date/security repair. D1 is upserted when work is needed, preserving old delisted IDs. Incoming rows are staged and validated before month-level upserts. A second identical run returns before creating an SDK session or making a remote request.

D5 is not silently treated as an immutable absolute series. The SDK 1.1.6 `get_backward_factor` method has no date-range argument. The normal daily updater therefore does **not** download the entire factor history for all 5,222 names. An explicit/periodic `--refresh-factors --factor-trailing-days 20` call obtains the vendor response in batches of 100, checks the historical old/new ratio at each security's earliest stored anchor and across the trailing overlap, rescales common renormalization to the existing anchor, and rewrites only affected monthly Parquet partitions. A non-common ratio change is recorded as an issue rather than silently overwriting history. This bounds local refresh, **not** the vendor's factor payload; it is an SDK capability limitation to revisit when a date-bounded endpoint becomes available. Until factor refresh is invoked, the factor dataset may lag bars/status, and its own latest date in the manifest is authoritative.

The updater has no system Task Scheduler job in this stage. The controlled incremental and second-run receipts are in `artifacts/stage1_local_market_store/incremental_update_receipt.json`.

## Offline research access

```python
from pathlib import Path
from research.market_store import LocalMarketStore

store = LocalMarketStore(Path("data/market_store"))
bars = store.load_daily_bars(["000001.SZ"], "2024-01-01", "2026-09-23")
status = store.load_daily_status(["000001.SZ"], "2024-01-01", "2026-09-23")
factors = store.load_adjustment_factor(["000001.SZ"], "2024-01-01", "2026-09-23")
store.close()
```

The loader test deliberately makes the AmazingData import unavailable. This verifies the research-side network boundary, but the mutable store itself does not supply a PIT-frozen lifecycle/availability contract. The next stage must produce a frozen research snapshot before real strategy smoke and Event Study.

## Reproduction and evidence

Use a Python 3.13 environment with the installed AmazingData SDK plus `pip install -e ".[market-store,amazingdata]"` (or equivalent local dependencies). Do not put credentials in this repository. The legacy provider loads them through the existing local environment.

```text
py -3.13 -B scripts/sync_market_store.py --phase D1_basic
py -3.13 -B scripts/sync_market_store.py --phase D3_bars
py -3.13 -B scripts/sync_market_status_batch.py
py -3.13 -B scripts/sync_market_store.py --phase D5_factor
py -3.13 -B scripts/finalize_market_store.py
py -3.13 -B scripts/verify_market_store.py
py -3.13 -B scripts/update_market_store.py --end YYYY-MM-DD
```

The eight small committed receipts under `artifacts/stage1_local_market_store/` contain actual row counts, date coverage, issue counts, phase timing, ETA comparison, cache reuse, resume, incremental behavior and manifest summary. Full data and checkpoint paths remain ignored. The estimate of ~2.015 hours (+20% buffer ~2.419 hours) is compared with the measured sum of active endpoint segments; engineering pauses and Parquet materialization are reported separately, not quietly included in the acquisition ETA.

Not completed here: D8 corporate action/outcome price path, `REAL_RESEARCH_SNAPSHOT_V1`, real strategy smoke, Entry Event Study, Exit/PnL, or any edge assessment.
