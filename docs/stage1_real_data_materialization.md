# Stage 1 real-data materialization preflight

Status: `STAGE1_REAL_DATA_MATERIALIZATION_STOP`. PR #16 was merged as
`dee6c38b36c8cc3a901aa71bde03d94d7c2b0dfe`; D5 remains `QUALIFIED`
only for individually admitted security-date factor ratios. Seven sampled
`IS_CHANGED=1` effective dates remain fail closed. This branch does not contain
`REAL_RESEARCH_SNAPSHOT_V1`, real HistoricalUniverse rows, real Entry A/B
signals, or a real smoke. It has not run a full Event Study, Exit, or PnL.

## Boundary established from the frozen D2 calendar

The candidate data window is 2024-01-01 through 2026-09-23. The D2 calendar
has 662 exchange sessions. 2024-08-15 has 150 validated prior sessions and is
only a candidate RSI warm-up boundary. With execution at T+1 and H=20 including
D0 plus 19 more sessions, the last possible signal date is **2026-08-26**:
D0 is 2026-08-27 and D19 is 2026-09-23. These are calendar bounds, **not** a
qualified research interval. [The range receipt](../artifacts/stage1_real_data_materialization/qualified_range.json)
is replayed by [the public verifier](../scripts/verify_stage1_real_materialization_stop.py).

## Exact blockers and minimal next actions

| Component | Observed coverage / unknown | Why smoke is blocked | Minimum next action |
| --- | --- | --- | --- |
| D4 | Gate B C2 contract and 13 representative reconstructions only; no complete 2024-01-01–2026-09-23 security-day canonical constraint table or coverage counts. Historical final SSE `cpxx0201/0202` and SZSE `cashauctionparams` reference rows, or an equivalently complete official event/reference reconstruction, are not in the available evidence. | Limit regime, exact upper/lower bound and `trading_allowed` cannot be marked `VALID` across the candidate market by accepting vendor fields or ST labels alone. A vendor ST-label conflict is already observed for 600518.SH on 2024-07-04. | Obtain a licensed/archived effective-dated official daily reference feed, or independently reconstruct each security-day from official rules, reference prices and issuer/exchange events; compare vendor rows, then report VALID/INVALID/MISSING/CONFLICT and all regime totals. [SSE interface](https://www.sse.com.cn/services/tradingtech/development/c/10822594/files/2096257019bf484f9b9935fa73f94721.pdf); [SZSE interface](https://www.szse.cn/marketServices/technicalservice/interface/P020240809676036898865.pdf). |
| D1 | Historical A-share list sampled on only 2024-01-02 (5,096) and 2026-09-22 (5,222); five stock-basic cases and ETF negative control. No 662-session effective identity grid; security-type/lifecycle UNKNOWN and relisting incidence cannot be counted. | HistoricalUniverse and full-prefix RSI cannot establish membership/lifecycle for every candidate security-day. | Batch/cache the 662 daily historical lists, verify type from the historical A-share source plus dated exchange evidence (not ticker prefix), build reversible intervals and reconcile IPO, delist and any relisting. |
| D3 / D6 | Three checked 600519.SH raw bars and seven sampled historical status dates; no bulk OHLCV or suspension join. Missing, invalid, UNKNOWN and CONFLICT totals are unknown. | Indicators, membership, execution and path coverage are incomplete; a missing bar cannot be called suspended. | After authoritative D4 path is secured, capture/hash/resume bounded bars and status in batches; join to D1; fail closed on duplicates, invalid bars, status/bar conflicts and unexplained missing bars. |
| D5 | D5 source contract passes, but no candidate-universe factor/admission grid exists. Seven sampled unresolved revisions include 600519.SH 2025-06-26 and 2026-06-26, 600887.SH 2025-06-06, 001331.SZ 2026-06-26, 688115.SH 2024-10-31, plus two older effective dates. | Per-security-date prefixes after an unresolved action cannot enter PIT indicator prices; the count of admissible prefixes is unknown. | Freeze complete factor/event inputs, reconcile each changed case with final issuer evidence or exclude its affected prefix, and count qualified/blocked prefixes. |
| D8 | Outcome-only adapter has synthetic corporate-action and no-action tests. There is no complete bounded action chronology for candidate H=20 paths. | Real MFE/MAE/RET could cross mechanical ex-price breaks without a verified comparable scale. | Freeze issuer-verified effective-date cash/bonus/rights chronology for all candidate horizons; reconcile high/low/close scale for real cases. Do not credit cash dividend income. |
| D7 / smoke | No jointly qualified, immutable D1–D8 source set; snapshot ID, eligible security-day count and signal count are **unknown**, not zero. | Two runs cannot replay the same real data and a real trace cannot be authenticated. | Only after upstream coverage passes, freeze one new immutable snapshot ID, intersect one contiguous range, materialize eligibility, then run the predeclared 120-session smoke (240 once if a type is absent) twice. |

The [source registry](../artifacts/stage1_real_data_materialization/source_registry.json)
and [quality summary](../artifacts/stage1_real_data_materialization/real_data_quality_summary.json)
leave unmeasured denominators and rates `null`. Starting millions of vendor-only
bar/status downloads while D4 authority is unresolved would not qualify a
snapshot; no bulk download was attempted in this preflight. The local code adds
fail-closed security-day interval, bar, constraint, factor-prefix and trading
state checks; an exclusive-create primitive prevents overwriting a frozen
private blob. These synthetic checks are preparation, not real-source PASS.

Private sampled AmazingData payloads remain under ignored `.local_research_data`.
Only aggregate counts, source hashes, contract identifiers, tests and this
STOP receipt are public. A future full capture must use a **new** snapshot ID;
the D5 sample receipt or this preflight receipt must never be relabeled as the
unified research snapshot.
