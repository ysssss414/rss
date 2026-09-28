# Stage 1 operational trigger qualification and real strategy smoke

The active research path is `REAL_RESEARCH_SNAPSHOT_V1 → CLOSE_LIMIT_UP_10PCT_V3`
`→ OBSERVATION_POOL_CONTRACT_V2 → OPERATIONAL_TRIGGER_QUALIFICATION_V1`
`→ seven-session Observation lifecycle → Entry A/B`. The order of the two
middle checks is implementation-independent: both are applied at trigger T.
The old V1/V2 contracts and official-only audit path remain historical; the
active runner does not invoke the 13,958-day regime dependency audit, V2
fixed-percentage limit predicate, current-state status lookup, or D8.

## Evidence change

This is an explicit research-standard downgrade, not an authority upgrade for
the vendor. The [frozen contract](../artifacts/stage1_operational_trigger_qualification_v1/contract.json)
has four states. Dated issuer/exchange evidence or an effective rule takes
precedence over the exact tick-space V3 path; V3 takes precedence over the
frozen T-day `daily_status.is_st_sec`; missing/contradictory inputs go to
`REVIEW_REQUIRED`. The vendor row is an **operational screen**, never a
canonical historical ST register. A non-V3 vendor ST row is conservatively
rejected, accepting possible false negatives from stale ST flags. A vendor
false-normal could create false positives; the bounded official cases found
no such candidate conflict, but they cannot establish full historical
absence. Special reference/IPO/unknown cases are excluded for review.

The unchanged V3 engine receives `special_exception="CLEARED"` only in this
explicitly operational application after its ordinary-reference and
known-special screens. This is **not** a claim that each day received
independent official exception clearance. The method and evidence level remain
in every formal Observation. On 600518.SH, the [issuer's July 3, 2024 notice](https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF)
sets July 4 withdrawal and a 10% limit. Snapshot V1 still has
`is_st_sec=true` that day, while V3 is TRUE; the dated official/V3 evidence
overrides that stale vendor flag. The [SSE 2026 rule](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
and [SZSE 2026 rule](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html)
take effect July 6; a historical vendor 5% field does not veto the qualified
rule/price path.

## RSI and episode boundaries

`RSI14_PROJECT_V1` remains first-valid-delta, Decimal, threshold strictly
above 70. A listing whose full listing-to-T prefix is in V1 uses it. Older
listings use the frozen `RSI_WARMUP_POLICY_V1` truncated suffix: minimum 120
valid observations, target 150, and a **noncanonical** replay mode retained in
provenance. The 715 not-ready candidates are counted separately from the 21
trigger-regime review cases. A truncated READY value is suitable only for this
research smoke, not a reconstructed full historical RSI truth.

Trigger qualification is only at T. No lookback day receives a separate ST
qualification; no post-admission day is rechecked for ST. The seven-session
episode still handles suspension without entry evaluation, invalid quote,
known delisting, expiry, overlap suppression, signal-pending, and later
nonoverlapping re-entry. Entry A and B use their frozen predicates. Entry V3
only classifies an intent; no fill, position, or `TRADE_LIFECYCLE_V1` position
transition is inferred. The latter contract is retained but cannot be
materialized without a separate qualified execution result.

## Frozen run and boundaries

The frozen run contains 5,540 provisional T candidates. Operational
qualification yields 3,337 V3-price-path, 2,071 vendor/official-normal,
111 vendor-ST rejected, and 21 review-required cases; 715 have RSI not ready.
After strict V3/RSI checks, 2,749 remain, one of which is rejected by the
trigger screen. Overlap suppression yields 993 formal admissions across 752
securities (972 four-of-five and 21 five-of-five), from 2024-07-03 through
2026-09-23. There are 505 Entry signals: 426 A and 79 B; 263 board-intent
and 242 normal-close-intent. The review set covers 21 securities (1 high
priority), all currently requiring special-reference/company-action review.
No known official false-normal contradiction intersects the candidate set;
that is a bounded check, not a universal vendor accuracy claim.

The [run manifest](../artifacts/stage1_real_strategy_smoke_v1/run_manifest.json)
links hashes for qualification, review, formal Observation, episode lifecycle,
Entry, case audit, source contracts and code. The runner performs two complete
calculations, two as-of-prefix checks, and one mutation of a future T-day
vendor status; earlier formal artifacts must remain identical. The signal
source whitelist is the five V1 market datasets only. No D8 or future outcome
field is read. The [case audit](../artifacts/stage1_real_strategy_smoke_v1/case_audit.json)
contains only evidence through the relevant trigger or Entry day.

This run is a **signal smoke**, not an event study. It makes no claim about
fills, returns, win rate, PnL, MFE/MAE, exit rules, or strategy edge.
Before a later Entry Event Study, use only formal events and retain evidence
method and noncanonical RSI replay mode for stratification/sensitivity checks.
