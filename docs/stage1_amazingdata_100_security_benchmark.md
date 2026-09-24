# STAGE1_AMAZINGDATA_100_SECURITY_THROUGHPUT_BENCHMARK

Status: **COMPLETED** — 100 securities, serial baseline measured. One D5 method attempt failed late in a long-lived session and succeeded after resume with a fresh session; both attempts remain in the figures.

## Executive summary

AmazingData 1.1.6 was queried for 100 fixed, board-stratified Shanghai/Shenzhen A shares over **2024-01-01–2026-09-23**. The serial baseline used **3,493.72 seconds (58.23 minutes)** of active wall-clock. D5 `get_backward_factor` alone used **3,343.36 seconds (95.7%)**. Extrapolating the observed total to the latest 5,222-name historical SH/SZ universe gives **50.68 hours**, or **60.81 hours** with an explicitly assumed 20% buffer: `VERY_SLOW`. The next step is D5 acquisition optimization, not direct full-market serial materialization. This is a throughput benchmark, not an exchange-provenance qualification, historical PIT requalification, or strategy backtest.

## Population and repeatable sample

The 2026-09-23 `BaseData.get_hist_code_list(security_type="EXTRA_STOCK_A")` response was filtered to the **5,222** distinct `.SH`/`.SZ` A-share IDs; Beijing IDs are outside this benchmark. The fixed sample uses seed **20260924**, sorted IDs, and 25 slots each for SSE Main, SZSE Main, STAR, and ChiNext. Known ST/suspension, factor, long-history, and recent-IPO cases are included inside—not in addition to—those quotas. D1 `InfoData.get_stock_basic` supplied actual listing dates; **12** sampled names listed during 2024–2026. The exact 100 IDs and reasons are frozen in [sample_universe.csv](../artifacts/stage1_amazingdata_100_benchmark/sample_universe.csv). Re-runs reuse this CSV rather than resampling.

This is a **stratified throughput sample**, not a market-cap- or board-proportionate random sample. The 5,222 denominator is the latest-day historical SH/SZ universe, not the union of every security that traded at any time during 2024–2026. Full-window materialization may need additional delisted names; its ETA must be refreshed with that final count.

## Measurement method

- Phase A: 5-security smoke; Phase B: 20 securities cumulative; Phase C: all 100 cumulative. Completed security-endpoint pairs were skipped on resume only if the ignored cached gzip CSV still matched its checkpoint SHA-256.
- The manifest is board-ordered, so the first 5/20 pilot names came from SSE Main rather than a cross-board random pilot. Pilot latency is therefore diagnostic only; the completed 100-security baseline covers all four boards. The optional 20-security factor batch retest uses five securities per board and compares against those same baseline names.
- One historical-universe lookup establishes the denominator. D1 stock basic uses three measured batches of 5, 15, and 80. D3 `MarketData.query_kline`, D4/D6 `InfoData.get_history_stock_status`, and D5 `BaseData.get_backward_factor` use serial per-security SDK calls. D4/D6 and D5 use `is_local=False`; SDK session reuse matches the existing adapter path.
- Endpoint seconds are `time.perf_counter()` around SDK method attempts, including retry delays if any. Total benchmark wall-clock sums active process segments, including login, universe selection, validation, and cache writes, but excludes downtime between the smoke and resumed run. The first baseline did not add artificial concurrency. A later optional factor batch retest is reported separately.
- Offline tests ran on the same host during part of the serial baseline. They made no additional SDK requests, but CPU/disk contention may have affected measured latency.
- Request counts are **SDK method attempts**, not internal network calls or retries inside the SDK (which it does not expose). Failed attempts remain counted if a later retry succeeds. D5 has no date arguments, so its SDK call may retrieve full history; D5 row counts here are the locally retained candidate-window, post-listing observations, not total transport rows.
- Raw vendor responses, SDK caches, and the checkpoint remain only under ignored `.local_research_data/stage1_amazingdata_benchmark/`. Committed files contain the fixed sample, aggregate metrics, schema, cache hashes, and receipts—not vendor raw dumps or credentials.

## Endpoint performance — serial baseline

| Endpoint | Call mode | Securities | SDK attempts | Retained rows | SDK seconds | Seconds/security | Rows/second | Failed attempts | Retries |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| D1 stock basic | batch 5 / 15 / 80 | 100 | 3 | 100 | 2.54 | 0.0254 | 39.31 | 0 | 0 |
| D3 daily bars | per security | 100 | 100 | 62,126 | 35.45 | 0.3545 | 1,752.26 | 0 | 0 |
| D4/D6 historical status | per security | 100 | 100 | 62,239 | 74.57 | 0.7457 | 834.61 | 0 | 0 |
| D5 backward factor | per security | 100 | 101 | 62,239 | 3,343.36 | 33.4336 | 18.62 | 1 | 1 |

The separate historical-universe setup request took **1.72 seconds**. The **100-security total active wall-clock** was **3,493.72 seconds**, versus **3,455.93 seconds** summed over core endpoint calls; the **37.79-second** difference is login, setup other than the SDK method, validation, and cache persistence. There were **305 SDK method attempts** including the universe lookup: 304 successes, 1 failure, and 1 retry. The phase B pilot's first 5 D5 calls averaged **23.02 s/security** and the next 15 averaged **26.38 s/security**. Across successive 20-name blocks, D5 averaged **25.54, 26.12, 24.18, 27.44, and 63.89 seconds/security**. The last block includes the 387.55-second failed attempt for 301669.SZ. Even after subtracting only that failed attempt, the full-market linear ETA would still be **45.06 hours**, far above the eight-hour `VERY_SLOW` reference. Board order and elapsed time are confounded; latency variation alone does not establish rate limiting.

The observed schemas are recorded in [benchmark_receipt.json](../artifacts/stage1_amazingdata_100_benchmark/benchmark_receipt.json). D3 included `code`, `date`, OHLC, `volume`, and `amount`. D4/D6 included `PRECLOSE`, `HIGH_LIMITED`, `LOW_LIMITED`, both limit-rate fields, `IS_ST_SEC`, `IS_SUSP_SEC`, `IS_WD_SEC`, and `IS_XR_SEC`; no synthetic replacement fields were created. D1 included identity, listing/delisting date, and listing plate.

## Lightweight quality and failure accounting

The 100 D1 identities and listing dates were present, with **0** duplicate IDs, **0** null IDs, and **0** board-code versus `LISTPLATE_NAME` mismatches. D3 had **0** duplicate security-dates, null keys, obvious OHLC violations, or negative volume/amount rows. D4/D6 had **16** rows across 16 securities with null `MARKET_CODE`, `TRADE_DATE`, `HIGH_LIMITED`, and `LOW_LIMITED`; the raw rows remain unchanged in the private cache and are not valid keyed observations. `IS_ST_SEC` and `IS_SUSP_SEC` had no nulls. D3 had **113** date gaps against keyed status observations, all matched by `IS_SUSP_SEC=1`; unexplained gaps were **0**. The sample includes **688** rows flagged ST and **113** flagged suspended. D5 had **0** missing, duplicate-date, or non-positive retained factors and **0** date gaps versus keyed status observations. Repeated cumulative factor **values** on different dates are not duplicates. A known no-limit IPO sentinel is not automatically invalid. These checks measure gross data fitness for this benchmark, not historical correctness of every status label or PIT corporate-action semantics.

The sole failed SDK attempt was a `TypeError` in the SDK calendar path while requesting 301669.SZ D5. A fresh session returned its factor successfully; the **387.55-second** failed attempt remains in D5 wall-clock, attempts, failure rate, and full-market extrapolation. The cause was not established as quota throttling. Timeouts and explicit rate-limit errors were **0**.

[endpoint_metrics.csv](../artifacts/stage1_amazingdata_100_benchmark/endpoint_metrics.csv) gives all endpoint numerators and denominators. [request_failures.csv](../artifacts/stage1_amazingdata_100_benchmark/request_failures.csv) has one row per failed attempt and a header even if no failures occur. The [summary](../artifacts/stage1_amazingdata_100_benchmark/benchmark_summary.json) includes the one universe setup call in total SDK attempts; it keeps setup rows separate from the 100-security core endpoint row total.

## Full-market ETA and engineering decision

The principal linear ETA is **observed active total seconds / 100 × 5,222**. A separate 20% engineering buffer is a scenario, not a measured delay. The [ETA artifact](../artifacts/stage1_amazingdata_100_benchmark/full_market_eta.json) also expands D3, D4/D6, and D5 separately using their measured seconds/security. D1 is different: its endpoint ETA uses `ceil(5,222 / 80) × observed 80-security batch seconds`, not a per-security slope. Endpoint sum and total-wall-clock extrapolation are both shown because setup/serialization overhead is not part of endpoint SDK timings.

The observed-total linear ETA is **182,442.09 seconds (50.68 hours)**; 20%-buffered ETA is **218,930.51 seconds (60.81 hours)**. Endpoint estimates are D1 **36.84 seconds** using 66 batches of 80, D3 **1,851.45 seconds (0.51 hours)**, D4/D6 **3,894.18 seconds (1.08 hours)**, and D5 **174,590.19 seconds (48.50 hours)**. Their sum, **180,372.66 seconds**, differs from the observed-total extrapolation because method-only timings omit fixed work. The `VERY_SLOW` band is an engineering decision aid, not a research-data gate.

The separate [optimized 20-security D5 retest](../artifacts/stage1_amazingdata_100_benchmark/optimized_factor_20.json) used five fixed names per board and one SDK-supported batch call: **43.00 seconds**, 13,240 post-listing factor observations, and **0** errors. The same 20 names took **512.31 seconds** in the serial baseline, a **11.92×** observed speedup. Mechanically extending that one-batch latency to 5,222 names would require 262 batches and about **3.13 hours for D5 alone**, but this is *only a scenario*: sustained multi-batch speed, quotas, session lifetime, and memory behavior have not been measured. The serial 100-security baseline and its ETA are not replaced by this retest. Implement checkpointed D5 batching and a bounded multi-batch soak before full-market materialization; D3 and D4/D6 do not warrant speculative optimization first.

## Boundaries, reproducibility, and delivery

D8 is **DEFERRED**. Future Stage 1 action data paths already exposed by the SDK include `get_adj_factor`, `get_dividend`, and `get_right_issue`; this benchmark does not materialize them. No full-market download, research snapshot, event study, exit, PnL, or edge analysis was run.

The scripts are [serial benchmark](../scripts/benchmark_amazingdata_100.py) and [optional factor batch comparison](../scripts/benchmark_amazingdata_factor_batch20.py). Their offline selection/metric/checkpoint helpers are in [amazingdata_benchmark.py](../research/amazingdata_benchmark.py). The work is based directly on PR #17's AmazingData integration commit `05069c2`; it does not depend on the D4 authority PR #18/#19 branches. The three protected Excel files were not opened, staged, or modified by this task. Starting and ending SHA-256 values matched:

| Protected workbook | SHA-256 before = after |
| --- | --- |
| `inputs/three_board_daily_input.xlsx` | `38dabdc991d26d4d1b5873bfdcbfb5dd29db4671fa1760c20d932d3d17d9b9fe` |
| `inputs/three_board_daily_input_2026_backtest.xlsx` | `282c4abdbb5901f5e3ee33512e7256887c1e92ae129662f631397fbd6943cf22` |
| `手工统计.xlsx` | `2fdf23576a7d220e994cf74143236a0b3db34e9e495f769927d0a65c994f8277` |

Final tests: selection/metrics/checkpoint/quality, existing AmazingData adapter, and Stage 1 focused suites **122 passed, 0 failed, 0 skipped**; the full repository suite **468 passed, 0 failed, 0 skipped**. The known Stage 0 baseline file's CRLF checkout representation was temporarily normalized for the full suite and restored byte-for-byte afterward.
