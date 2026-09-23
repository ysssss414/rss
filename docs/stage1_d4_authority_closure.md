# Stage 1 D4 authority closure — STOP

## A. Scope

This qualification concerns only the daily **price-limit regime** for one A-share security before the opening auction. It does not determine reference prices, absolute limit prices, whether a stock traded or was suspended (D6), security-day universe completeness (D1/D3), factor PIT (D5), chronology (D8), or any strategy outcome. The Gate B `DailyTradingConstraint` contract remains unchanged. A `VALID` D4 regime is only one input to that larger contract.

## B. Baseline

The work starts from `05069c2cbd198abcb951a16ed3bb794ffabc145b` on the still-open PR #17 branch, stacked as `codex/rss-stage1-d4-authority-closure`. `main` was `dee6c38b36c8cc3a901aa71bde03d94d7c2b0dfe` at branch creation. Gate B C2 and its 13 representative cases established the calculation contract, not a market-wide state feed. The real-data materialization gate remains STOP. The candidate window 2024-01-01 to 2026-09-23 and candidate research start 2024-08-15 are **not qualified**. `qualified_research_start`, `qualified_signal_end`, and `qualified_session_count` stay null in the pre-existing range artifact.

## C. Official authority sources

The [authority manifest](../artifacts/stage1_d4_authority_closure/authority_manifest.json) records URL, exchange, publication/effective dates, local frozen bytes and SHA-256, clause or page, coverage, and replay admission. The frozen SSE 2023/2026 rule DOCX files and 2026 SZSE rule PDF are primary rule evidence. In particular, [SZSE 2026 rule §3.3.13/§3.3.15](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf) gives 10% main-board, 20% ChiNext and the specified no-limit windows; its [revision explanation](https://docs.static.szse.cn/www/lawrules/rule/allrules/bussiness/W020260424690713346617.pdf) explicitly states the main-board risk-warning change from 5% to 10%. The [official SSE 2026 notice](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml) and [official SZSE 2026 notice](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html) put the new rules into effect on **2026-07-06**.

The former [SZSE 2023 official notice](https://www.szse.cn/lawrules/rule/repeal/rules/t20230217_598773.html) remains an official source, but its linked original PDF returned 404 to direct retrieval in this environment. Search caches are not frozen primary evidence. The 2023 SZSE source therefore has `admitted_for_replay=false`; its rule rows are represented for test and acquisition planning, not for production qualification. Issuer/exchange announcements for 600187, 600518, 603194, 001391, 688511 and 000838 are frozen and hash checked where available. An announcement proves only the stated event and coverage; it is not a complete all-security state register.

The SSE `cpxx0201/cpxx0202` and SZSE `cashauctionparams` specifications describe exchange pre-open fields and updates, but the required historical **rows** were not obtained. An interface specification alone cannot certify what was sent for a security-day. No AmazingData regime/status/limit field is an authority source.

## D. Rule matrix

The [frozen matrix](../artifacts/stage1_d4_authority_closure/rule_matrix.json) uses non-overlapping, inclusive effective dates and a clause for each represented category. Its executable categories are deliberately narrower than all possible exchange rules:

| Period | Main board normal | Main board risk warning | STAR / ChiNext | First five IPO sessions |
| --- | --- | --- | --- | --- |
| 2023-04-10–2026-07-05 | 10% | 5% | 20% | no daily limit where explicitly supported |
| From 2026-07-06 | 10% | 10% | 20% | no daily limit where explicitly supported |

The table states rule authority only. For 2023 SZSE, the original PDF still must be reacquired before the row can qualify a real date. The 2026 SZSE rule also identifies relisting first day and delisting-arrangement first day as no-limit; its later delisting days use board rates. STAR 2026 delisting first-day no-limit is represented. Other SSE relisting/delisting and earlier STAR special windows are **not** silently inferred from neighboring rows. Missing rule categories return `MISSING_RULE_AUTHORITY`. Exchange-recognized “other circumstances”, temporary adjustments, and security-specific exceptions remain unresolved until an authoritative, complete notice/daily-row path is acquired.

A security code suffix or prefix is a plausibility check, not proof of board or A-share class. A stock-name change is not itself a regime event; risk-warning **effective status** is. Ex-right/ex-dividend changes the reference price, not the regime. Suspension is D6. `NO_DAILY_LIMIT` does not imply unconstrained order-price mechanics.

## E. Security-day state model

The [fact importer schema and bounded records](../artifacts/stage1_d4_authority_closure/bounded_sample_facts.json) use `security_id`, `field`, `value`, `source_id`, `known_at`, `effective_at`, `first_applicable_trading_date`, and `covered_until`. Required fields are official exchange, board, A-share class, listing kind (IPO versus relisting), listing session, and special status. Only an IPO has a first-five-session IPO window; a relisting first day is a separate rule. Risk-warning status is additionally required when a normal post-IPO rate must be selected. IPO first-five, relisting first-day and delisting first-day rule paths do not need risk-warning status to determine no-limit, but still require their other facts. An explicit `NONE` for special status must have coverage; absence is not `NONE`. Longer event persistence is not assumed without a complete official history.

## F. Resolver

`research/d4_authority.py` takes frozen rules and official state facts; it returns `limit_regime`, `qualification_state`, structured `reason_codes`, source trace, and rule effective interval. It does not read bars, actual returns, vendor limit fields, stock names, or wall-clock time. It does not calculate reference/upper/lower prices or change Gate B logic. `VALID` is emitted only when all required applicable facts and exactly one admitted rule match; unsupported types are `INVALID`, absent evidence is `MISSING`, and competing current facts/rules are `CONFLICT`.

## G. PIT semantics

The resolver's as-of instant is **09:15 Asia/Shanghai** on the trading date. Both disclosure `known_at` and state `effective_at` must precede it, and the query must lie within `first_applicable_trading_date..covered_until`. Conservative end-of-publication-day timestamps are used when a frozen source has a date but no precise release time; thus it cannot backfill that day's open. Future ST introductions/removals, listing records and rule changes cannot rewrite earlier results. The announcement date, decision date and first applicable trading date are separate fields.

## H. Bounded real sample

The [replayed sample](../artifacts/stage1_d4_authority_closure/bounded_sample_summary.json) contains the original 13 Gate B dates plus three official-event boundary probes: STAR risk-warning removal (688511, 2026-04-21) and SZSE main-board risk-warning introduction/rule boundary (000838, 2026-04-27 and 2026-07-06). It covers 9 securities and 16 security-days across SSE/SZSE, main/STAR/ChiNext, risk-warning, IPO, suspension, ex-dividend and the 2026 rule boundary. This is **not** the suggested 20–50-security breadth or a representative market sample.

Result: `VALID=1`, `MISSING=15`, `INVALID=0`, `CONFLICT=0`; among VALID, `NO_DAILY_LIMIT=1` and 5/10/20% each 0. The single valid day is SSE main-board IPO 603194 on 2024-12-24, supported by frozen [listing PDF](../artifacts/stage1_gate_b_retry3/603194_listing.pdf) and frozen SSE 2023 rule. The other source-bound cases retain an exact reason and authority trace; 001391 first day, for example, is `MISSING_RULE_AUTHORITY` because the SZSE 2023 original rule has not been frozen. 600187/600518/688511/000838 event notices prove specific transitions but not full identity, listing-session, negative special-status and persistence coverage. The original Gate B 13 price-limit calculation checks are not revoked; they answer a narrower question.

## I. Missing/conflict semantics

`UNKNOWN` is never coerced to 10%. Missing required state, a source outside the admitted frozen registry, an expired coverage interval, or a missing rule yields `MISSING`. Incompatible latest official facts or overlapping rule claims yield `CONFLICT`. Explicitly unsupported A-share scope yields `INVALID`. A later official update never resolves a historical conflict by using hindsight. Reason codes make all exclusions countable. Suspended/no-bar days are not used to infer D4 status.

## J. Tests

`tests/test_stage1_d4_authority.py` covers ordinary main board, STAR, ChiNext, risk warning and 2026 switch, IPO first five/day six, relisting/delisting synthetic rule paths, missing/conflicting facts and rules, future-state/rule PIT invariance, post-open disclosure, unknown sources, and D6/vendor independence. Synthetic tests prove algorithm branches, **not** real-source qualification. `scripts/validate_stage1_d4_authority.py` verifies every admitted frozen source hash, source references, rule-period non-overlap, exact deterministic real-sample replay, and STOP receipt counts. Final run: D4 targeted **29 passed, 0 failed, 0 skipped**; selected Stage 1 suite **171 passed, 0 failed, 0 skipped**; full repository suite **492 passed, 0 failed, 0 skipped**; manifest/replay validator **1 passed, 0 failed**. For the full suite, the pre-existing CRLF checkout of `baselines/stage0/pipeline_reference.json` was temporarily normalized to LF and then byte-for-byte restored; its post-run SHA-256 is `c56f2129ab620f4943e66c300895c81620c313902d4e58bece3ebbfaecfdfeae`.

## K. Protected files

`inputs/three_board_daily_input.xlsx`, `inputs/three_board_daily_input_2026_backtest.xlsx`, and `手工统计.xlsx` are protected. Their original modified/untracked states are not staged or changed. Before/after SHA-256 values match: respectively `38dabdc991d26d4d1b5873bfdcbfb5dd29db4671fa1760c20d932d3d17d9b9fe`, `282c4abdbb5901f5e3ee33512e7256887c1e92ae129662f631397fbd6943cf22`, and `2fdf23576a7d220e994cf74143236a0b3db34e9e495f769927d0a65c994f8277`.

## L. Gate result

`STAGE1_D4_AUTHORITY_CLOSURE_STOP`. D4 remains blocked. The deterministic partial model is reviewable, but neither an all-market official state path nor all historical exchange daily-control rows nor all exception classes are qualified. A single bounded VALID IPO date is not a candidate-window pass. [Qualification receipt](../artifacts/stage1_d4_authority_closure/qualification_receipt.json) preserves null research boundaries.

## M. Remaining blockers

See receipt `D4-A1` through `D4-A5`: SSE historical final pre-open cpxx rows; SZSE historical cashauctionparams rows; complete PIT security master/event/status with provable negative coverage; original SZSE 2023 rule PDF; and complete temporary-exception catalogue. The first three likely require exchange or licensed-participant historical access. Manually collected issuer notices can validate selected dates but cannot prove all-market absence of intervening changes. The smallest next acquisition is a dated official archive sample at the 2026-07-03/07-06 risk-warning boundary plus a complete timestamped status slice; then rerun this bounded gate before widening.

## N. Next action

Acquire/verify D4-A1–A5, extend the real sample to each unresolved board and lifecycle exception, and rerun deterministic replay. Only after D4 and the independent other gates pass should the next stage be `D1/D3/D6 bulk materialization + D5 prefix qualification + D8 chronology qualification`. No full materialization, real smoke, Event Study, Exit/PnL, or edge assessment was performed here.

## O. Git delivery

This is a stacked D4 branch based on open PR #17, intended as an independent Draft PR with that branch as base. No PR is merged and no protected workbook is staged. Commit/PR identifiers and final test/hash counts are reported separately after verification.
