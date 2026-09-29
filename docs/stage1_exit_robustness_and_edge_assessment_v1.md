# Stage 1 exit robustness and edge assessment

## Boundary and split

`EXIT_ROBUSTNESS_AND_EDGE_ASSESSMENT_RUN_V1` inherits the **unchanged**
505 Formal Entries and all eight exits of [`EXIT_AND_PNL_RUN_V1`](../artifacts/stage1_exit_and_pnl_v1/run_manifest.json).
It does not regenerate Observation, Entry, RSI, V3, or exit parameters. The
[split contract](../artifacts/stage1_exit_robustness_v1/edge_assessment_split_v1.json)
was written before the present subgroup/feature-outcome analysis: 2024–2025
development has 389 Entries (174 normal, 215 board); 2026 through September
23 has 116 (68 normal, 48 board). Entry A/B counts are 328/61 and 98/18.
**All 2024–2026 outcomes had already been inspected in prior stages.** The
2026 partition is a descriptive temporal contrast, not untouched
out-of-sample validation. A distinct future holdout would be required for
new strategy rules or a positive edge claim.

Normal-close T-day fill remains a same-close **research counterfactual**, not
verified historical execution. Board PnL remains conditional-on-fill only;
the two layers never form a combined portfolio return. Returns are
event-weighted research *price* returns, not account total returns. The
cost contract is an assumed commission/tax schedule on normalized notional.

## Normal-close robustness

All eight registered exits have negative net mean in **each** calendar year;
year and split rows, N, gross/net percentiles, positive fraction, reason mix,
holding days, tail shares and skewness are in the
[normal summary](../artifacts/stage1_exit_robustness_v1/normal_close_robustness_summary.csv)
and [temporal summary](../artifacts/stage1_exit_robustness_v1/temporal_robustness_summary.csv).
The table shows net mean percent, without selecting a favorable year:

| Exit | 2024 N / mean | 2025 N / mean | 2026 N / mean | All N / mean |
|---|---:|---:|---:|---:|
| TIME_H1 | 63 / -2.02 | 111 / -1.78 | 68 / -1.65 | 242 / -1.81 |
| TIME_H3 | 63 / -3.25 | 111 / -3.16 | 66 / -3.11 | 240 / -3.17 |
| TIME_H5 | 63 / -5.35 | 111 / -4.25 | 66 / -3.78 | 240 / -4.41 |
| TP5_H5 | 63 / -2.78 | 111 / -2.97 | 67 / -4.84 | 241 / -3.44 |
| TP10_H5 | 63 / -3.60 | 111 / -2.46 | 67 / -5.15 | 241 / -3.51 |
| TP5_SL5_H5 | 63 / -2.64 | 111 / -2.43 | 68 / -2.30 | 242 / -2.45 |
| TP10_SL10_H5 | 63 / -3.00 | 111 / -1.34 | 68 / -3.21 | 242 / -2.29 |
| TP10_SL5_H5 | 63 / -2.96 | 111 / -1.81 | 68 / -1.80 | 242 / -2.11 |

For TP5/H5, the all-sample net median is **+4.89%**, but the net mean is
**-3.44%**. Of 241 closed events, 99 lose money, 76 lose at least 10%, and
30 lose at least 20%. The latter 30 contribute 47.4% of the magnitude of
all negative returns, an arithmetic decomposition, **not** an identifiable
pre-entry subset. TP5/H5 net p10 is -21.69% and p90 +4.98%; skewness is
-0.85. Thus its negative mean is not "most events slightly losing": a
majority are positive while large failures dominate the average. By
contrast, TIME_H1 has 40.5% positive net events and a -2.71% median, so
its negative result is broader. The [tail anatomy](../artifacts/stage1_exit_robustness_v1/left_tail_anatomy.json)
reports the same decomposition for all eight exits.

Entry A/B, 4/5 versus 5/5, first versus subsequent Entry, single versus
repeated security and V3 versus operational provenance are all reported for
every exit. For normal TP5/H5, A (201/202 closed) has -3.80% mean and B
(40/40 closed) -1.63%; this is descriptive only. The 5/5 normal group has **2** events and
operational-status provenance **6**; neither supports rule selection.
Repeated Entry is preserved at event grain; 242 normal events represent
224 securities. The 32 subsequent normal events are not silently merged
with first events or treated as IID account trades.

## Pre-entry left-tail diagnostics

An [independent feature builder](../scripts/build_pre_entry_features_v1.py)
generates private `ENTRY_PRE_SIGNAL_FEATURES_V1` from frozen Entry,
Observation, D3 bars and calendar. A runtime as-of guard rejects H1+ bar
lookups. The builder never loads D8 *economic rows*, Event Study outcomes or
trade PnL; opening the immutable snapshot does hash all constituents,
including D8, for integrity only. The 505 feature keys join 1:1 with outcomes
**after** the feature Parquet has been materialized. Its
[contract](../artifacts/stage1_exit_robustness_v1/pre_entry_feature_contract_v1.json)
pre-registers nine economically motivated features, fixed bins and two
cross-tabs; [inventory](../artifacts/stage1_exit_robustness_v1/pre_entry_feature_inventory.csv)
shows six features with 505/505 coverage and three five-day features with
498/505. Missing windows are not imputed. MA10/MA20, exact V3 hit sequence,
turnover and market/theme context were deferred for lack of a qualified
frozen input or source timing contract.

H5 <=-10%, H5 <=-20%, failure to take TP5 before H5, and TP5-then-deep-
reversal are **outcome-only labels**, never inputs to the feature builder.
The [diagnostic table](../artifacts/stage1_exit_robustness_v1/left_tail_diagnostics.csv)
reports N, H1/H3/H5 outcomes, TP5 net PnL and label incidence for every
registered bin in all/development/2026. Some cells look suggestive (for
example high versus low raw five-day gain), but low-gain bins are sparse,
and none meets the frozen direction, minimum-N and temporal consistency
gate. The [shortlist](../artifacts/stage1_exit_robustness_v1/left_tail_hypothesis_candidates.json)
is therefore **empty**: `NO_STABLE_PRE_ENTRY_DIAGNOSTIC` among the nine
tested features, not proof that no useful feature exists. This is
post-outcome exploration with nine univariate tests and two cross-tabs;
there is no p-value hunt or same-sample filter approval. Raw lookback
returns may be distorted across corporate actions because signal-side D8
data is intentionally forbidden.

## Cost, same-bar and clustered uncertainty

[Cost sensitivity](../artifacts/stage1_exit_robustness_v1/cost_sensitivity.csv)
reports baseline, zero-cost diagnostic, and twice research cost for all
eight exits in both separate layers. The normal-close gross/zero-cost mean
is still negative in every exit (TP5/H5 -3.33% versus baseline net -3.44%),
so assumed fees are not the primary source of this negative baseline.

[`STOP_FIRST`](../artifacts/stage1_exit_and_pnl_v1/exit_contract_registry_v1.json)
remains primary. [TARGET_FIRST sensitivity](../artifacts/stage1_exit_robustness_v1/same_bar_ambiguity_sensitivity.csv)
is an optimistic same-bar upper bound, not an executable variant. For normal
TP5/SL5, 23 dual-hit events shift mean net from -2.45% to -1.49% under that
upper bound; it remains negative. For board conditional TP5/SL5, 40 cases
shift -0.11% to +1.42%, demonstrating why optimistic intraday ordering
cannot be substituted for the conservative baseline.

[Security-cluster bootstrap](../artifacts/stage1_exit_robustness_v1/edge_cluster_bootstrap.json)
resamples unique securities with all their repeated events, 2,000 fixed-seed
draws per layer/exit. It reports within-sample percentile 90%/95% intervals,
not a holdout test. Normal TP5/H5 mean -3.44% has a 95% cluster interval
[-4.93%, -2.14%]; all eight normal intervals lie below zero. Board
conditional TP5/H5 mean +0.58% has [-0.74%, +1.88%], crossing zero.
Bootstrap uncertainty does not repair same-close fill, board fillability,
multiple testing, or prior outcome inspection.

## Board data qualification

The [fillability audit](../artifacts/stage1_exit_robustness_v1/board_fillability_audit.json)
finds only daily bars/status in the frozen snapshot, with no timestamped
intraday, order-book, seal or queue table. The repository's qualified
AmazingData bridge calls daily `query_kline`; a prior Gate C audit
documented possible minute/snapshot API surfaces but did not qualify
historical coverage or queue evidence. All 263 board Entry days show
lower-price daily prints before a close at the D4 upper bound; the
timestamps of those prints relative to the closing signal are unknown.
No frozen board day is a one-price upper-bound day under this vendor-D4
comparison. D4's bound is a diagnostic, not independently certified
exchange reference history. Under [SSE](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
and [SZSE](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf)
price/time-priority matching, OHLC lacks order timing and queue position;
it cannot establish the user's fill. Result:
`BOARD_FILLABILITY_DATA_INSUFFICIENT`, 0 unconditional executable board
trades and no invented fill probability.

The [board conditional robustness table](../artifacts/stage1_exit_robustness_v1/board_conditional_robustness_summary.csv)
reports all eight exits and the same descriptive subgroup cuts. Conditional
TP5/H5 net means are +0.16%, +0.77%, +0.93% in 2024, 2025, 2026,
respectively; that is an exploratory conditional candidate only, with a
95% cluster interval crossing zero and no verified T-day board fill.

## Conclusion and audit trail

The [conclusion taxonomy](../artifacts/stage1_exit_robustness_v1/edge_assessment_conclusions.json)
is: normal close `EDGE_NOT_SUPPORTED` under current Entry, eight simple
exits and same-close research fill; board
`CONDITIONAL_EDGE_CANDIDATE` **and** `FILLABILITY_UNQUALIFIED`; left-tail
`NO_STABLE_PRE_ENTRY_DIAGNOSTIC` within the pre-registered nine features.
None is a permanent proof of strategy impossibility or a live-trading
decision. The recommended next *question* is
`STAGE1_STRATEGY_REASSESSMENT`, not an automatically started stage.

The [quality receipt](../artifacts/stage1_exit_robustness_v1/edge_assessment_quality_receipt.json)
checks 505 feature/event joins, 4,040 unique Entry×Exit keys, frozen split
counts, all exits, diagnostic-bin coverage, population separation and
reconciliation to parent gross/net means. The [real case audit](../artifacts/stage1_exit_robustness_v1/edge_case_audit.json)
shows Entry-time features beside later outcomes for normal failures and
rebounds, and strong/weak board conditional paths. No one-price board case
is fabricated when absent. The [run manifest](../artifacts/stage1_exit_robustness_v1/run_manifest.json)
and [reproducibility receipt](../artifacts/stage1_exit_robustness_v1/edge_assessment_reproducibility.json)
pin input, contract, code, runtime and output hashes. Private feature and
trade Parquet remain gitignored; only aggregate and bounded derived audit
evidence enter the PR.
