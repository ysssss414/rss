# STAGE1_AMAZINGDATA_ACQUISITION_OPTIMIZATION

Status: **COMPLETED**. Recommended D5 batch size: **100 securities**. Next action: `STAGE1_AMAZINGDATA_FULL_MARKET_MATERIALIZATION`.

## Decision

AmazingData 1.1.6 returned the same D5 factor keys and values through serial and batched paths. The fastest tested 100-security mode was one batch of 100: **30.78 seconds** versus **3,343.36 seconds** for the same 100 names in the prior serial baseline (**108.61×**, including that baseline's recovered 387.55-second failure). A new, cleaner same-20 control took **526.84 seconds** serially versus **33.07 seconds** for those exact 20 names in the first batch-20 request (**15.93×**). These are different comparisons and should not be conflated.

The chosen size then completed **500 securities in five consecutive remote batches and 141.05 active seconds**, or **0.2821 seconds/security**, with **318,150 retained factor rows** and no failed attempts, retries, timeouts, or explicit rate-limit errors. The first 100 took 30.12 seconds; the last 100 took 23.49 seconds. Measured throughput improved by **28.22%** from first to last, so observed throughput degradation was **−28.22%**. Five batches are useful sustained evidence, not a guarantee of 53-batch full-market session behavior.

At the prior 5,222-name SH/SZ HistoricalUniverse size, this 500-name measured D5 rate implies **0.409 hours (24.55 minutes)** for D5, **0.491 hours** with an assumed 20% buffer. Combining it with the prior D1/D3/D4-D6 endpoint models yields **2.015 hours** for core Stage 1 acquisition, **2.419 hours** with the same modeled buffer. These are estimates, not a full-market execution.

## Comparable timing

All current runs use 2024-01-01 through 2026-09-23, the [fixed 100-name sample](../artifacts/stage1_amazingdata_100_benchmark/sample_universe.csv), `is_local=False`, a separate SDK cache directory, and a fresh SDK process/session per mode. The historical serial 100 run is from the preceding benchmark. Its one failed attempt remains in its wall-clock. The strict 100-name speedups below use that same-name historical serial reference; the current serial 20 is a separate contemporary control. See [full comparison CSV](../artifacts/stage1_amazingdata_acquisition_optimization/batch_size_comparison.csv).

| Mode | Securities | Batch size | Requests | D5 active wall-clock | Seconds/security | Factor rows | Same-100 speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Historical serial | 100 | 1 | 101 | 3,343.36 s | 33.4336 | 62,239 | 1.00× |
| New serial control | 20 | 1 | 20 | 526.84 s | 26.3419 | 12,847 | Not same 100 |
| Batch 20 | 100 | 20 | 5 | 131.89 s | 1.3189 | 62,239 | 25.35× |
| Batch 50 | 100 | 50 | 2 | 49.26 s | 0.4926 | 62,239 | 67.87× |
| Batch 100 | 100 | 100 | 1 | 30.78 s | 0.3078 | 62,239 | 108.61× |
| Sustained batch 100 | 500 | 100 | 5 | 141.05 s | 0.2821 | 318,150 | Different sample |

The new serial control's first 20 names and the first batch-20 request have exactly the same 12,847 retained rows. The latter's 33.07 seconds gives the contemporary **15.93×** same-name speedup. The three 100-name batch results are also directly comparable to each other; batch 100 was **4.29×** faster than batch 20. Larger batch sizes were not explored because 100 already reduced D5 to less than an hour at the measured 500-name rate. All tested sizes were supported. Batch 100 had zero errors and saves one checkpoint per 100 names; its meaningful speed advantage over batch 50 outweighs the coarser resume granularity here.

`BENCHMARK_TIMER_SCOPE`: `time.perf_counter()` encloses SDK method request/response and parsing, factor normalization, compressed per-batch cache persistence, and checkpoint writes. The reported active wall-clock sums process segments and excludes login, universe/basic-metadata setup, manual downtime between runs, test execution, and Git work. SDK-internal HTTP calls and SDK-internal caching are not exposed; `is_local=False` and distinct per-mode local directories explicitly select the SDK remote-fetch path. No parallel SDK requests were made. The benchmark mode does not count a checkpoint cache hit as a remote call.

## Sustained behavior and lightweight quality

The [500-name sample](../artifacts/stage1_amazingdata_acquisition_optimization/sample_universe_500.csv) starts with the original 100 unchanged, then deterministically adds four 100-name blocks from the same 2026-09-23 SH/SZ HistoricalUniverse using seed 20260924. Each block has 25 names per board; the 500-name final composition is 125 each of SSE Main, SZSE Main, STAR and ChiNext. D1 supplied listing dates for the 400 additions outside the D5 timer. Factors were retained only for the common candidate window and on/after each listing date. The SDK method itself does not accept a date range and may transport older history.

| Names | Wall-clock | Seconds/security | Retained rows | Retries | Errors | End-of-batch RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1–100 | 30.12 s | 0.3012 | 62,239 | 0 | 0 | 930.14 MB |
| 101–200 | 27.91 s | 0.2791 | 63,723 | 0 | 0 | 947.34 MB |
| 201–300 | 30.79 s | 0.3079 | 64,067 | 0 | 0 | 945.74 MB |
| 301–400 | 28.72 s | 0.2872 | 64,892 | 0 | 0 | 952.50 MB |
| 401–500 | 23.49 s | 0.2349 | 63,229 | 0 | 0 | 968.04 MB |

The [segment CSV](../artifacts/stage1_amazingdata_acquisition_optimization/sustained_500_metrics.csv) and [per-batch latency CSV](../artifacts/stage1_amazingdata_acquisition_optimization/batch_latency.csv) retain unrounded values and timestamps. Across just five sustained batches, median latency was **28.72 s**, P75 **30.12 s**, P90 **30.52 s**, P95 **30.66 s**, and max **30.79 s**; the upper quantiles are descriptive, not reliable tail-risk estimates. RSS increased **37.90 MB** from the first to last batch (930.14 to 968.04 MB). This does not establish a memory leak, but full-market execution should watch memory and use the checkpoint to restart the process if necessary.

For the 500-name D5 output, duplicate security-date keys, missing factors, and non-positive factors were all **0**. All five batches completed; request attempts **5**, failed attempts **0**, retries **0**, timeouts **0**, rate-limit classifications **0**, and session restarts **0**. This is a lightweight acquisition-quality check, not a redo of factor PIT qualification.

## Equivalence, retry and real resume

The [equivalence receipt](../artifacts/stage1_amazingdata_acquisition_optimization/data_equivalence_summary.json) compares `(security_id, trade_date, factor)` independent of row order. Fresh serial 20 versus batch 20 matched **12,847/12,847** keys; historical serial 100 versus each of batch 20/50/100 matched **62,239/62,239** keys. Batch 20 versus the other batch sizes also matched. Missing, extra, and value-mismatched rows were **0** in every comparison. Numeric factor values were compared exactly after round-trip CSV parsing; no tolerance was needed. The sustained first 200 and separately resumed 200 also matched **125,962/125,962** keys and values.

The [real resume receipt](../artifacts/stage1_amazingdata_acquisition_optimization/resume_test_receipt.json) uses two batch-100 groups covering the fixed extended sample's first 200 names. The first process wrote batch 1 and intentionally stopped. A second process loaded the SHA-256-checked checkpoint, skipped that completed batch, and made exactly one remote request for batch 2. Final coverage was **200 securities**, with **0** missing securities, **0** duplicate output rows, and **0** duplicate remote acquisitions of the completed batch. Each successful batch is stored immediately in ignored local gzip CSV (`security_id`, `trade_date`, `factor`) with a checkpoint hash; later materialization can consume it without repeating an API call.

The retry policy allows at most three retries after the initial request, with bounded backoff. A TypeError in the SDK request path triggers one fresh session before a retry; transient connection/timeout errors may also reinitialize a session. A synthetic injected-error test exercised this recovery and checkpoint skip path. No optimized live batch encountered an error, so live session-recovery effectiveness remains unobserved. No concurrency was added.

## Full-market acquisition estimate

The [ETA JSON](../artifacts/stage1_amazingdata_acquisition_optimization/full_market_eta.json) applies `141.047126 seconds / 500 × 5,222` to D5 and retains the prior endpoint-specific estimates for D1, D3, and D4/D6. The 20% buffer is an assumption, not a measured delay.

| Component | Estimated 5,222-name time |
| --- | ---: |
| D1 basic, 80-name batches | 36.84 s |
| D3 bars | 1,851.45 s (0.514 h) |
| D4/D6 status | 3,894.18 s (1.082 h) |
| Optimized D5 factor | 1,473.10 s (0.409 h) |
| Core Stage 1 acquisition total | **7,255.57 s (2.015 h)** |
| Total with assumed 20% buffer | **8,706.68 s (2.419 h)** |

The prior serial D5 endpoint ETA was **48.497 h**; its total-wall Stage 1 extrapolation was **50.68 h** under a different model including all observed setup/persistence overhead. Do not treat the old total-wall and new endpoint-sum estimates as an exact like-for-like speedup. The new total omits some D1/D3/D4/D6 local overhead that those SDK-only endpoint timings did not capture, while new D5 timing includes its cache/checkpoint work. The 5,222 denominator is the latest-day historical SH/SZ universe, not the union of all delisted names during the window. Full-market actual time, SDK quotas, 53-batch session behavior, and SDK-internal cache/network details remain unmeasured.

## Reproducibility and delivery

The reusable runner is [optimize_amazingdata_d5.py](../scripts/optimize_amazingdata_d5.py), with [batch helpers](../research/amazingdata_batch.py) and [offline summary](../scripts/summarize_amazingdata_d5.py). Its `materialize` mode accepts a future CSV manifest with unique `security_id` and `listing_date` fields, runs the same batch/checkpoint path, and can resume from verified batches; the 5,222-name mode was **not run** in this task. The existing 500-name gzip output is also retained for a later materializer to ingest without another API request; the generic `materialize` mode does not automatically import that benchmark cache. [Benchmark plan](../artifacts/stage1_amazingdata_acquisition_optimization/benchmark_plan.json) specifies samples, timer scope, retry policy, and comparison basis. [Optimization receipt](../artifacts/stage1_amazingdata_acquisition_optimization/optimization_receipt.json) holds mode metrics, latency distribution, quality, memory, and per-batch cache hashes. Raw vendor data, credentials, SDK caches, and checkpoints stay under ignored `.local_research_data/` and are not committed. The new branch is based directly on PR #20, itself based on PR #17; it does not depend on the D4 authority PRs.

The three protected Excel files were not staged or changed by this task. Starting and ending SHA-256 values matched:

| Workbook | SHA-256 before = after |
| --- | --- |
| `inputs/three_board_daily_input.xlsx` | `38dabdc991d26d4d1b5873bfdcbfb5dd29db4671fa1760c20d932d3d17d9b9fe` |
| `inputs/three_board_daily_input_2026_backtest.xlsx` | `282c4abdbb5901f5e3ee33512e7256887c1e92ae129662f631397fbd6943cf22` |
| `手工统计.xlsx` | `2fdf23576a7d220e994cf74143236a0b3db34e9e495f769927d0a65c994f8277` |

Focused tests: **92 passed, 0 failed, 0 skipped**. Full repository: **473 passed, 0 failed, 0 skipped**. The known Stage 0 baseline file's CRLF checkout representation was temporarily normalized for the full suite and restored byte-for-byte afterward.
