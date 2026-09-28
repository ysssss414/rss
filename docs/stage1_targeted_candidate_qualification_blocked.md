# Stage 1 provisional candidates and targeted-regime qualification

Result: `BLOCKED_CANONICAL_LIMIT_UP_UNQUALIFIED`. This is not a strategy smoke
run. No provisional candidate became a formal Observation or Entry.

The new active offline path starts at immutable `REAL_RESEARCH_SNAPSHOT_V1`,
uses the qualified minimal pre-window IPO calendar, and scans only SSE/SZSE
main-board observations. A raw-close move of at least 5% against either
supplier preclose or previous raw close, or a match to supplier upper price,
marks a **LIMIT_UP_LIKE** discovery hit. The deliberately low threshold admits
old 5% risk-warning events as false positives. It is neither a final limit-up
threshold nor a valid price-limit classification. A 4/5 window with provisional
RSI above 70 is retained; old listings without the required 120-observation
truncated warm-up are retained as `UNVERIFIED_WARMUP` rather than excluded.
Neither vendor flag nor vendor upper price is canonical authority.
The union recalls every V1 row whose raw close matches its vendor upper
price, but it cannot prove a mathematical superset of unknown official
limit-up rows when both vendor reference and upper price are wrong. This
remaining recall risk must be tested against the acquired official daily
reference subset before formal use.

The [candidate receipt](../artifacts/stage1_targeted_regime/provisional_candidate_inventory.json)
reports 6,388 provisional rows on 1,391 securities (4/5: 4,649; 5/5: 1,739),
2024-01-08 through 2026-09-23. Its 15,489 unique five-window dependency
security-days are 0.8143% of the 1,902,063 old-rule main-board security-days
considered by the former global route. This 99.19% search-space reduction is
useful, but not a coverage or qualification claim. The
[dependency inventory](../artifacts/stage1_targeted_regime/targeted_regime_dependency_inventory.csv)
records shared dates once and links every affected candidate.

The [targeted triage](../artifacts/stage1_targeted_regime/targeted_qualification_receipt.json)
keeps all 15,489 dependency days unresolved: 14,858 pre-2026-07-06 dates
lack independent historical risk state and exception exclusion; 631 later
dates still lack independent exclusion of first-day no-limit or other special
exceptions. The six preserved official issuer/exchange case documents do not
intersect these actual dependency dates. Three bounded code/date searches
yielded no pre-trigger document admitted into qualification. All 6,388
candidates therefore fail closed; none were rejected or promoted.

Raw close above a 5% move relative to supplier `PRECLOSE` is not yet the
qualified `PRICE_PATH_EXCLUDES_5PCT_REGIME` method. The exchange rules calculate
limits from an applicable reference, which can change on ex-right/ex-dividend
days; IPO, relisting and the first delisting-arrangement day can also have no
daily limit. The [SZSE 2026 rule](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf)
states both the no-limit exceptions (3.3.15) and ex-right reference treatment
(4.4.1–4.4.3). V1's vendor `PRECLOSE` and `HIGH_LIMITED` are not independently
verified official daily references. In this dependency set, 11,129 raw moves
exceed the broad 5% floor, but **zero** are promoted through price-path inference.

Even an independently qualified 10% regime would not prove the frozen
`IS_CLOSE_LIMIT_UP_V1` predicate: it needs a validated raw absolute upper
limit and tick-aware equality for each of five days. Existing
[Gate B daily contract](../artifacts/stage1_gate_b_retry3/daily_constraint_contract.json)
has 13 representative case checks, not a historical daily official upper-price
archive. Hence no strict `LIMIT_UP_COUNT_5_V1`, Observation state machine,
Lifecycle, Entry A/B, or smoke manifest has been materialized. Empty formal
event files would falsely suggest a tested zero-signal result and are not written.

The [cleanup receipt](../artifacts/stage1_targeted_regime/stage1_strategy_path_cleanup_receipt.json)
shows that the blocked full-market branches and receipts remain reachable,
while neither the old crawler nor global DailyTradingConstraint/V2 is invoked
by the new route. The next bounded evidence task is to obtain official final
pre-open daily reference/upper-limit rows, or independently reconstruct them
from official event/reference/tick facts, **only for dependency dates**. Then
qualify any remaining risk-state/exception dates, rerun the frozen strict
predicate, and materialize formal events only if all gates pass.
