# Stage 1 V3 tick-space close-limit event and targeted smoke audit

Status: `BLOCKED_TARGETED_REGIME_EVIDENCE_INSUFFICIENT`. This branch is stacked on the frozen V2 failure branch. V1 absolute-price and V2 9.91%–10.09% contracts are unchanged; V2 remains failed for strategy qualification. V3 is a separate, scoped calculation contract, not an active formal strategy runtime. No formal Observation, lifecycle, Entry, outcome, or smoke manifest was materialized.

## Rule, reference, and qualification boundary

The [V3 qualification receipt](../artifacts/stage1_close_limit_up_v3/contract_qualification.json) marks the scoped mechanical contract `QUALIFIED_FOR_STRATEGY_RESEARCH`, conditional on independent day-level evidence; it explicitly leaves the active formal runtime blocked.

`PRICE_LIMIT_TICK_ROUNDING_V1` uses exact positive integer cents for an applicable 0.01 CNY main-board A-share tick. Its 10% upper is `max(floor((previous_close_cents * 11000 + 5000) / 10000), previous_close_cents + 1)`. The midpoint is rounded upward, not by binary floating-point or Python `round`. The separate [rounding contract](../artifacts/stage1_close_limit_up_v3/rounding_contract.json) records the frozen official sources, applicable scope, and tick exception. Existing Gate B `rate_limits` and tests independently use `Decimal(ROUND_HALF_UP)`.

The [reference-day contract](../artifacts/stage1_close_limit_up_v3/reference_day_contract.json) permits previous *valid raw trading close* only on a screened ordinary reference-price day. D5 factor changes, vendor ex-right/ex-dividend and delisting screens, IPO phase, and missing inputs route to special/unresolved. There is no qualified canonical exchange ex-reference price in Snapshot V1, so special reference days fail closed. The `ORDINARY` label is a screen, **not** independent official proof of no-limit exception clearance. An exact `close == rounded_upper == high` remains `UNRESOLVED` until that clearance is independently dated. Changed price ticks are also unresolved. D5 PIT-adjusted prices are not exchange references. D8 is `OUTCOME_ONLY_NEVER_SIGNAL` and appears only in a post-hoc cross-check; it never changes event, candidate, regime, or entry decisions.

Official rules: [SSE 2023 trading rules](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml), [SSE 2026 trading rules](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml), and [SZSE 2026 trading rules](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf). The cited clauses state ordinary RMB A-share 0.01 CNY ticks, daily limit formulas, half-up tick rounding and minimum one-tick separation, while allowing the exchanges to specify a different tick. The exact rule copies and hashes are frozen in the contract.

### Low-price validation

Theoretical and rounded prices are CNY. Return and deviation are percentage points; each row is an exact-cent calculation, not market performance.

| Previous | Theoretical 10% | Rounded upper | Implied return | Deviation from 10% |
|---:|---:|---:|---:|---:|
| 0.50 | 0.550 | 0.55 | 10.00000% | 0.00000 |
| 0.51 | 0.561 | 0.56 | 9.80392% | -0.19608 |
| 0.99 | 1.089 | 1.09 | 10.10101% | +0.10101 |
| 1.00 | 1.100 | 1.10 | 10.00000% | 0.00000 |
| 1.01 | 1.111 | 1.11 | 9.90099% | -0.09901 |
| 1.50 | 1.650 | 1.65 | 10.00000% | 0.00000 |
| 1.93 | 2.123 | 2.12 | 9.84456% | -0.15544 |
| 2.00 | 2.200 | 2.20 | 10.00000% | 0.00000 |
| 3.33 | 3.663 | 3.66 | 9.90991% | -0.09009 |
| 5.00 | 5.500 | 5.50 | 10.00000% | 0.00000 |
| 10.00 | 11.000 | 11.00 | 10.00000% | 0.00000 |
| 20.00 | 22.000 | 22.00 | 10.00000% | 0.00000 |
| 50.00 | 55.000 | 55.00 | 10.00000% | 0.00000 |
| 100.00 | 110.000 | 110.00 | 10.00000% | 0.00000 |

The frozen 600518.SH / 2024-07-04 official case has previous raw close 1.93, raw close/high 2.12, and an issuer-announced effective return to the 10% regime. V3 computes 2.12 and returns `TRUE`; V2 returns `FALSE`. The [13-case Gate B reconciliation](../artifacts/stage1_close_limit_up_v3/gate_b_reconciliation.json) has no ordinary-day conflicts. Its suspended, off-board, IPO, and ex-dividend cases remain excluded or unresolved as applicable; they were not made artificially true. The announcement is [issuer evidence](https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF).

## Frozen Snapshot V1 audit

Snapshot V1 manifest SHA256: `a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`. The broad V3 detector is `raw return >= 5% OR special-reference review`. Every positive-cent 10% rounded upper has at least a 5% implied move under the current tick contract; an exhaustive test covers 1–100,000 cents, and the 2,083,520 tradable main-board Snapshot V1 dates have **zero** counterfactual ordinary V3 hits missed by the broad detector. This is a discovery recall check, not a formal event qualification. Exact candidates: 5,540 from 1,377 securities, covering 13,958 distinct dependency security-dates; 4,167 broad 4/5 and 1,373 broad 5/5. Another 715 provisional windows have unverified RSI warm-up and are retained only for review.

Among all tradable main-board dates, the counterfactual classifier finds 2,071,721 screened ordinary; 8,194 corporate-action special; 417 IPO no-limit; 76 other special; and 3,112 unresolved. Its counterfactual `TRUE` count is 39,546, but these are **not** qualified real events because the run deliberately assumed exception clearance only to test detector recall.

The [targeted dependency receipt](../artifacts/stage1_close_limit_up_v3/targeted_qualification_receipt.json) classifies 13,958 dependency dates: 13,871 screened ordinary, 77 corporate-action special (0.55%), 2 other special, and 8 unresolved. Ordinary dates split into 7,817 mechanical V3 hits pending exception clearance and 6,054 non-limit dates. All 13,958 regime decisions remain `UNRESOLVED`; none of the existing six dated official cases intersects this set. Of the mechanical hits, 7,418 old-rule paths have a *counterfactual* rounded 10% upper strictly above the rounded 5% upper. This only establishes potential 5% exclusion after no-limit and special-reference exceptions are independently cleared. The stricter separation check is necessary at ultra-low prices where rounded 5% and 10% ceilings can coincide (e.g. 0.10 CNY both round to 0.11).

Dependency-level candidate triage yields 2,295 rejected by the strict 4/5 mechanical possibility test; 2,792 unresolved for regime/exception evidence; and 453 further unresolved for RSI warm-up. The counterfactual strict V3 count is 4/5 on 2,238 provisional windows and 5/5 on 962; neither is a formal Observation. The special-reference footprint is small enough for local fail-closed review, not a market-wide reference-price reconstruction. The D8 post-hoc check intersects 72 canonical and 5 excluded event records with the dependency set, all on already screened special dates; **zero** screened ordinary dependency dates overlap D8. D8 was not used to choose candidates or signals.

The frozen [`LIMIT_UP_COUNT_5_V1`](stage1_strategy_primitives.md) requires all five observation dates to have qualified symmetric 10% constraints, including the one non-limit date in a 4/5 admission. The [Observation/Entry contract](stage1_observation_entry_contract.md) also requires a validated 10% regime on each Entry evaluation date. Price-path evidence alone cannot qualify those non-limit dates; the existing official inventory has no matching dated cases, and vendor ST/status labels are not independently admitted. The blocker is therefore `BLOCKED_TARGETED_REGIME_EVIDENCE_INSUFFICIENT`. Formal Observation, Entry A/B, lifecycle, and `REAL_STRATEGY_SMOKE_RUN_V1` remain **not run**, not zero-valued strategy results. No forward outcome or performance data was evaluated.
