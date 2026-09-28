# Stage 1 Observation V2 — trigger-date-only audit

Status: `BLOCKED_TRIGGER_DATE_REGIME_EVIDENCE_INSUFFICIENT`. The user-authorized contract change is frozen; no formal Observation, lifecycle, Entry A/B, or `REAL_STRATEGY_SMOKE_RUN_V1` exists on this branch. No outcome data was accessed.

## Contract change and lineage

The historical [`OBSERVATION_POOL_V1`](../artifacts/stage1_observation_entry/observation_pool_contract.json) and its `LIMIT_UP_COUNT_5_V1` remain untouched. V1 requires every one of the last five observations to have an independently valid symmetric 10% constraint. New [`OBSERVATION_POOL_CONTRACT_V2`](../artifacts/stage1_observation_contract_v2/observation_pool_contract_v2.json) supersedes V1 **only** for target-regime eligibility timing in the current strategy research path: only admission date T must be `QUALIFIED_10PCT`. The five historical observations supply a 4/5 or 5/5 count of *qualified* `CLOSE_LIMIT_UP_10PCT_V3` events; their non-limit days need no separate ST inquiry. An `UNRESOLVED` V3 day is never counted as a hit, though four other `TRUE` days can still satisfy 4/5. RSI remains the frozen `RSI14_PROJECT_V1`, strictly above 70 and formally ready at T.

[`EPISODE_START_ELIGIBILITY_V1`](../artifacts/stage1_observation_contract_v2/episode_start_eligibility_v1.json) states that a qualified trigger establishes episode eligibility. Subsequent sessions do not repeat ST/10% qualification, while explicit lifecycle invalidation, suspension handling, seven-session expiry, quote/PIT validity, and frozen Entry A/B predicates remain in force. The old V1 runtime is a legacy strict path, **not** the V2 runtime. Its `process_eod` hard-checks daily 10% regime and its Entry evaluator expects daily `UniverseRecord.eligible` plus `IS_CLOSE_LIMIT_UP_V1`; those calls cannot be silently reused as V2 behavior. A separate V2 lifecycle/Entry adapter is required after the evidence gate clears; this branch does not claim it has run.

## Recomputed real-data audit

The controlling source is immutable `REAL_RESEARCH_SNAPSHOT_V1`, manifest SHA256 `a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`, plus the V3 provisional candidate parquet frozen in PR #26. The [new trigger audit](../scripts/audit_observation_contract_v2_triggers.py) rebuilds all five-day V3 event screens from raw bars, status, factor, master, and prior valid close. It **does not** promote the old all-five-day targeted qualification CSV. It invokes regime qualification only for each unique trigger T, never for historical lookback days, and does not import or read D8.

| Audit measure | Result |
|---|---:|
| V3 provisional candidates / unique trigger security-dates | 5,540 / 5,540 |
| Old all-five-day dependency security-dates | 13,958 |
| New trigger-only regime dependencies | 5,540 |
| Dependency reduction | 60.3095% |
| Lookback-day regime qualification calls | 0 |
| V3 path self-qualified T | 0 |
| Direct-rule qualified T | 0 |
| Targeted official-status qualified T | 0 |
| Unresolved T regime | 5,540 |
| Definitively below four possible V3 hits | 2,295 |
| Remaining candidates blocked by T evidence | 3,245 |

The [trigger inventory](../artifacts/stage1_observation_contract_v2/observation_v2_trigger_regime_dependency_inventory.csv) identifies 3,179 T dates with a *counterfactual* ordinary 10%-price path above the old 5% ceiling, 2,084 primarily requiring targeted historical risk-state/exception evidence, 256 post-rule dates still requiring no-limit exception clearance, and 21 corporate-action special-reference dates. These four evidence-need categories partition the 5,540 triggers; they do **not** establish qualification. The present official effective-day inventory overlaps **none** of the triggers. Actual V3 status at T is `FALSE` on 2,182 dates and `UNRESOLVED` on 3,358; no actual `TRUE` is licensed by independent exception-clearance evidence. In particular, 3,179 potential price paths are diagnostic only: calling V3 with `special_exception="CLEARED"` merely for recall cannot create a formal event.

The V3 600518.SH / 2024-07-04 regression and 13-case Gate B reconciliation remain in the prior frozen branch and pass their tests. V3's ordinary/special reference logic is unmodified. The new V2 audit has no official-case intersection that would clear no-limit exceptions. Rulebook recognition that normal and risk-warning main-board stocks share 10% after 2026-07-06 does not itself clear a no-limit special day, so the 256 post-rule cases remain unresolved.

The 4,825 `READY_PROVISIONAL` RSI values are **not** formally replayed full-prefix `RSI14_PROJECT_V1` values; 715 others have explicitly unverified warm-up. Neither group is upgraded to formal RSI readiness. This is an additional dependency that would need to be resolved if trigger evidence becomes available.

The [qualification receipt](../artifacts/stage1_observation_contract_v2/trigger_qualification_receipt.json) includes source hashes, grain checks, reason counts and output hash. The [blocked case audit](../artifacts/stage1_observation_contract_v2/blocked_case_audit.json) gives deterministic representative T-only records; it is not a successful formal-event case audit and contains no post-trigger prices.

## Stop boundary

The material gap is trigger-date evidence, not the retired five-day ST requirement. `CLOSE_LIMIT_UP_10PCT_V3` requires independent no-limit exception clearance before a mechanical hit can become `TRUE`; trigger-only qualification likewise needs a dated source for the regime or a genuinely cleared 10% price path. The available six official cases do not overlap the 5,540 trigger dates. Therefore the stage stops at `BLOCKED_TRIGGER_DATE_REGIME_EVIDENCE_INSUFFICIENT`. Formal Observation V2, lifecycle, Entry A/B, prefix/future-mutation tests on real formal events, deterministic complete smoke, and a smoke run manifest are **not run**, not zero-valued strategy results. No Entry Event Study, forward returns, D8 outcome, PnL or edge assessment was performed.
