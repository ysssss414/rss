# Stage 1 frozen Entry exit and price-PnL contract

## Scope and truth claims

`EXIT_AND_PNL_RUN_V1` starts from the frozen 505 formal Entries in
`REAL_STRATEGY_SMOKE_RUN_V1`; it never reruns Observation, RSI, Entry, or
trigger qualification. The parent [`ENTRY_EVENT_STUDY_RUN_V1`](../artifacts/stage1_entry_event_study_v1/run_manifest.json)
is **signal outcome**, not a filled-trade backtest. This stage models eight
pre-registered exits, but does not establish historical fills, portfolio
returns, total shareholder returns, or strategy edge.

The [execution contract](../artifacts/stage1_exit_and_pnl_v1/execution_contract_v1.json)
splits 242 normal-close intents from 263 board-close intents. Normal uses a
`SIMULATED_CLOSE_FILL_V1` at T's raw close. Since the signal is finalized at
that same close, this is a counterfactual **research fill assumption**, not an
independently verified executable auction fill. Its summary layer is named
`EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION` only in the sense permitted by
this research contract. Board rows are *only* `CONDITIONAL_ON_FILL_ONLY`: if
filled at T's close, what would the exit path have been? Daily OHLC does not
qualify a historical board fill. `BOARD_FILLABILITY_UNQUALIFIED`; board
unconditional executable count is zero. These layers are never pooled into a
single strategy return. Every frozen Entry is simulated independently, even
for repeated securities; overlapping trades are not a single-account NAV.

## Exit mechanics

The [registry](../artifacts/stage1_exit_and_pnl_v1/exit_contract_registry_v1.json)
contains exactly `TIME_H1`, `TIME_H3`, `TIME_H5`, `TP5_H5`, `TP10_H5`,
`TP5_SL5_H5`, `TP10_SL10_H5`, and `TP10_SL5_H5`. These were chosen after the
Event Study and are **exploratory**, not an independent holdout test. No grid
search or ex-post new threshold is allowed. Profit-protection/trailing is
contract-only and not simulated: same-day trailing high versus stop order is
not identified from OHLC.

T+1 is mandatory: every check starts at the first *exchange* trading session
after signal T, including suspended sessions in the H1/H3/H5 clock. Time
exits use the target session's close. A suspended timeout becomes pending and
exits at the next valid trading open. A one-price limit-down day at the
observed D4 lower bound cannot fill a long sell, so an active stop/timeout
remains pending until the next tradable open. Flat falling OHLC without an
attributable lower bound fails closed. An ordinary one-price limit-up does
not automatically block a sell. Missing status/invalid OHLC and delisting
without a liquidation value remain unresolved/null, never forward-filled.

For TP/SL, thresholds start at the simulated fill and are tick-rounded in
the raw-price scale after dividing by cumulative D8 comparable factor.
An open beyond the stop/target executes at **open**, not a favorable
threshold. If both intraday bounds hit without an opening resolution,
`SAME_BAR_AMBIGUOUS_STOP_FIRST` exits at the stop. This is deliberately
conservative, but daily OHLC still cannot prove intraday liquidity at each
level. The private trace records each session's raw OHLC, factor, tick
thresholds, and decision. The engine stops reading after modeled exit.

D8 actions take effect in the price scale only when their known date strictly
precedes the effective date and the frozen event source hash is valid.
Excluded actions fail closed. Adjusted comparable price is *not* actual
account cash proceeds: cash dividend income, rights subscription economics,
tax on distributions, share quantities, and total shareholder return are
unmodeled. Thus this is `PRICE_RETURN_PNL_V1`, a stylized research return.
It should not be described as realized historical account PnL, especially
for corporate-action windows. In the actual 4,040 modeled rows, 15 rows
from four distinct Entry events intersect a D8 `DIVIDEND` event before exit;
the event label does not separately quantify cash versus stock components.

## Costs and outputs

The [cost contract](../artifacts/stage1_exit_and_pnl_v1/transaction_cost_contract_v1.json)
uses a fixed **research** broker commission of 3 bps on each side, inclusive
of exchange handling and regulatory charges, plus 0.1 bps transfer fee on
each side and 5 bps sell-only stamp duty. The last two correspond to the
[ChinaClear transfer schedule](https://www.chinaclear.cn/zdjs/editor_file/20220701154723234.pdf)
and [SSE's fee summary](https://one.sse.com.cn/onething/gptz/), following the
[Ministry of Finance's 2023 stamp-duty reduction](https://m.mof.gov.cn/czxw/202308/t20230827_3904226.htm).
The commission is **not** the user's account rate; normalized notional 1
cannot model the broker's minimum CNY fee or integer-share lots. For gross
return `g`, costs are `0.00031 + (1+g)*0.00081`, and net is `g - costs`.
The zero-slippage baseline is fixed; optional adverse buy bps/ticks are an
interface, not an optimized sensitivity.

The private gitignored `ENTRY_TRADE_PNL_V1` Parquet has 4,040 rows at one
Entry × one exit grain. The [public summary](../artifacts/stage1_exit_and_pnl_v1/exit_contract_summary.csv),
[population split](../artifacts/stage1_exit_and_pnl_v1/execution_population_summary.csv),
[exit reasons](../artifacts/stage1_exit_and_pnl_v1/exit_reason_summary.csv),
[pre-exit excursions/capture](../artifacts/stage1_exit_and_pnl_v1/exit_efficiency_summary.csv),
and [Entry/trigger subgroups](../artifacts/stage1_exit_and_pnl_v1/exit_pnl_subgroup_summary.csv)
report every registered exit in each layer. Returns are event-weighted and
conditioned on `CLOSED` rows; unresolved and right-censored counts are shown,
not silently treated as zero. MFE/MAE end at the actual modeled exit:
prior complete sessions plus only the exit print for gap/intraday exits;
the full exit day is included only for a close exit. Capture equals gross
return divided by pre-exit MFE only when MFE > 0. For fixed TP exits a
capture near 1 can be partly mechanical, not evidence of a good strategy.

## Baseline readout (descriptive only)

Means and medians below are percent **per closed trade**, rounded to two
decimals, not annualized. `N` is closed/total. The complete precision and
percentiles are in the CSV.

| Exit | Normal N | Normal gross mean | Normal net mean | Normal net median | Board conditional N | Board gross mean | Board net mean | Board net median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TIME_H1 | 242/242 | -1.69 | -1.81 | -2.71 | 263/263 | +0.40 | +0.29 | -0.04 |
| TIME_H3 | 240/242 | -3.06 | -3.17 | -4.33 | 263/263 | -2.43 | -2.54 | -4.91 |
| TIME_H5 | 240/242 | -4.30 | -4.41 | -6.37 | 263/263 | -3.69 | -3.80 | -6.96 |
| TP5_H5 | 241/242 | -3.33 | -3.44 | +4.89 | 263/263 | +0.70 | +0.58 | +4.92 |
| TP10_H5 | 241/242 | -3.40 | -3.51 | -4.46 | 263/263 | -1.11 | -1.22 | +9.88 |
| TP5_SL5_H5 | 242/242 | -2.34 | -2.45 | -5.13 | 263/263 | -0.00 | -0.11 | +4.88 |
| TP10_SL10_H5 | 242/242 | -2.18 | -2.29 | -10.10 | 263/263 | -0.74 | -0.85 | +9.88 |
| TP10_SL5_H5 | 242/242 | -2.00 | -2.11 | -5.13 | 263/263 | -0.23 | -0.34 | -5.13 |

The normal-close research baseline has negative net mean in all eight
pre-registered variants. TP5/H5's positive median but negative mean shows
why reporting only hit rate or median would mislead. Positive conditional
board rows cannot establish a board strategy: their T-day fill has not been
qualified. Neither statement is an out-of-sample edge verdict.

## Audit and limits

The [quality receipt](../artifacts/stage1_exit_and_pnl_v1/exit_pnl_quality_receipt.json)
checks 4,040 unique keys, T+1 exit dates, population separation, nonnegative
costs, and excursion/return bounds. The [case audit](../artifacts/stage1_exit_and_pnl_v1/exit_pnl_case_audit.json)
contains deterministic real examples for normal/board fills, T+1 TP/SL,
both gap directions, dual hit, suspended timeout, locked limit-down stop,
ordinary timeout, D8 holding, and Entry A/B. Raw vendor trade-level paths
and Parquet stay local under `.local_research_data`, not in the PR.

The [run manifest](../artifacts/stage1_exit_and_pnl_v1/run_manifest.json) pins
parent, Entry and snapshot hashes, contracts, code, runtime, public hashes,
and private Parquet hash. The [reproducibility receipt](../artifacts/stage1_exit_and_pnl_v1/exit_pnl_reproducibility.json)
pins semantic row/trace and private Parquet hashes. A second identical run
must preserve all of them. Focused synthetic tests enforce T+1, timeouts,
thresholds, gaps, dual-hit precedence, suspension, limit-down, D8 price
semantics, costs, and no future mutation of an earlier exit.

The results reuse the 2024–2026 Event Study period for hypothesis design.
Chronological split and later untouched holdout/walk-forward are requirements
for a later robustness/edge assessment, not performed here. No parameter
selection, position sizing, portfolio NAV, board-fill probability, or live
deployment is claimed.
