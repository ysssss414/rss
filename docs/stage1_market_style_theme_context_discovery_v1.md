# Stage 1 market/style/theme context discovery

## RESULT

`STAGE1_MARKET_STYLE_THEME_CONTEXT_DISCOVERY = COMPLETED` as a **post-outcome exploratory** study. Market/Style: `NO_STABLE_MARKET_STYLE_CONTEXT`; Theme: `THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED`; zero admitted context hypotheses. No Entry or Exit rule changes follow from this result.

## A. Baseline / frozen population

The [run manifest](../artifacts/stage1_context_discovery_v1/run_manifest.json) is stacked on `EXIT_ROBUSTNESS_AND_EDGE_ASSESSMENT_RUN_V1`, preserving Entry CSV SHA-256 `82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1`: 505 Formal Entries, 242 normal-close, 263 board-intent. Development is 2024–2025 (174 normal); 2026 to September 23 has 68 normal and is descriptive only. All 4,040 Entry × fixed-Exit keys reconcile.

## B. Research boundary

All 2024–2026 outcomes had already been inspected. This is neither out-of-sample validation nor filter approval. T-day context is finalized end-of-day information. Vendor publication latency and a same-T-close order fill are **not** proved; normal-close PnL retains the parent's same-close research counterfactual. Board PnL remains `CONDITIONAL_ON_FILL` only.

## C. Context feature contract

The [contract](../artifacts/stage1_context_discovery_v1/context_feature_contract_v1.json) pre-registers eight primary variables, per-variable source/date/window/missing/version, 2024–2025 normal-Entry [terciles](../artifacts/stage1_context_discovery_v1/context_bins_v1.json), fixed regimes, one qualified interaction, and H1/H2 admission gates before outcome join. The independent [builder](../scripts/build_entry_context_features_v1.py) uses frozen Entry, D3, D4/D6 and calendar only; it does not load Event Study, PnL, or D8 economic rows. Snapshot-open hash validation includes D8 for integrity, not as a feature source. Private Entry and daily context Parquets are excluded from Git.

## D. Market liquidity

The 20-day turnover percentile is the primary liquidity measure, not a fixed CNY 2 trillion threshold. Normal Entry-day SH/SZ A turnover averaged CNY 1.772T / 1.764T / 2.713T in 2024/2025/2026, whereas mean trailing-20 percentile was 0.598 / 0.576 / 0.550. The source **excludes Beijing**, so this is SH/SZ A turnover, not literal all-China A. Full fields: T turnover, MA20, MA20 ratio, 20/60-day percentiles, and three-session slope. The [market univariate table](../artifacts/stage1_context_discovery_v1/market_context_univariate_summary.csv) reports fixed bins and outcomes.

## E. Market breadth

Daily limit-up/down use the date-specific positive D4/D6 bound and D3 close; strong-up/down use D3 close / D4 preclose ±5%. `(limit_up + strong_up - limit_down - strong_down) / valid-status stocks` intentionally double-counts a stock satisfying both a limit and strength test. Normal Entry-day mean breadth scores were 0.077 / 0.033 / 0.028 by year. An independent D3/D4 recomputation for 2024-07-19 matches the builder's amount, valid count, limit counts and score. Price-limit arrangements vary by board and status; the vendor's dated bound is used rather than a universal 10% assumption. [SSE trading rules](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml) and [SZSE trading rules](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf) provide mechanism context, not historical vendor-row validation.

## F. Prior strong-stock feedback

The prior session's closing-limit-up securities' T-day median return is computed with a minimum five valid securities. Normal Entry-day mean of this daily median was +2.78% / +1.24% / +0.98% by year. The HIGH bin (71 Entries) had TP5/H5 net mean -3.72% versus LOW (85) -1.62%, with worse H5 <=-10% incidence; a positive-feedback necessary-condition hypothesis is not supported. This is a comparison of Entry dates, not a causal effect of prior winners.

## G. High-position negative feedback

The T-day <=-5% fraction among T-1 stocks with at least three consecutive closing limit-ups has a minimum-five denominator. It is missing in 83/505 Entries (40/242 normal), never imputed zero. Normal Entry-day average among available observations was 17.6% / 19.1% / 17.9% by year. Counter to the proposed favorable direction, LOW negative-feedback normal Entries averaged -5.98% TP5/H5 versus HIGH -1.45%; annual contrast direction was inconsistent.

## H. Shortline style

Five equal-weight, trailing-60-session percentile components are high-board count, maximum height, >=2-board advancement rate, prior-limit-up median T return, and inverse high-position negative-feedback rate. At least three components are required. Normal Entry-day advancement rates averaged 53.0% / 42.8% / 36.3% by year; the score averaged 0.124 / 0.001 / 0.001. Component values, denominators and percentile fields are in the private feature dataset; [inventory](../artifacts/stage1_context_discovery_v1/context_feature_inventory.csv) exposes missing counts.

## I. Trend / capacity style

The T-day Top 100 by D3 amount, tie-broken by security ID, is fixed before outcomes. Its T median return, fraction above a 20-session synthetic-index MA and median five-session compounded return form an equal-weight trailing-percentile score. Daily D4 preclose returns compound the synthetic index across adjacent sessions, avoiding raw-close discontinuities at ex-right dates. Normal Entry-day trend score averaged 0.095 / 0.061 / 0.020 by year. This is a capacity-trend *proxy*, not an independently observed institutional-flow measure.

## J. Style differential / regime

`shortline - trend` uses fixed ±0.10 thresholds. Normal TP5/H5 net mean: TREND_DOMINANT 99 Entries, -3.02%; MIXED 65, -3.37%; SHORTLINE_DOMINANT 78, -4.02%. The expected shortline-dominance improvement is absent. [Style summaries](../artifacts/stage1_context_discovery_v1/style_context_summary.csv) include terciles, and [regime summaries](../artifacts/stage1_context_discovery_v1/market_style_regime_summary.csv) include yearly and split cuts.

## K. Theme data qualification

The immutable snapshot lists no theme dataset, and the inspected repository/local research paths do not provide historically dated security-theme membership together with daily strength and rank. The [qualification audit](../artifacts/stage1_context_discovery_v1/theme_context_data_qualification.json) records `THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED`; this does not block Market/Style work. No future or retrospectively curated “mainline” labels are substituted.

## L. Theme strength / persistence

Deferred: no PIT-qualified rank or longitudinal theme-strength series. No `theme_context_summary.csv` is asserted as measured.

## M. Candidate theme position

Deferred: no PIT-qualified dated membership, hence no defensible within-theme board/amount/return ranks or CORE/SECONDARY/FOLLOWER category.

## N. Univariate diagnostics

All eight variables are evaluated in pre-outcome development terciles (plus explicit MISSING), with N, TP5/H5 net mean/median/positive share, TIME_H1/H3/H5, H5 signal, left tails, and TP5 success. The normal population's TP5/H5 baseline remains -3.44% (241/242 closed). Turnover and breadth show no monotonic TP5 relationship; neither do prior feedback, advancement or style-differential terciles. An isolated best bin is not treated as a new rule.

## O. Market × Style

The [nine-cell interaction table](../artifacts/stage1_context_discovery_v1/context_interaction_summary.csv) is the sole tested two-way interaction. Risk-on × Shortline has N=41 and normal TP5/H5 mean -5.35%. Risk-off × Mixed has N=7 and +4.30%, explicitly `N_LT_10_NO_SHORTLIST`; it is not promoted. Risk regime alone: RISK_OFF N=28, -1.55%; NEUTRAL N=84, -2.31%; RISK_ON N=130, -4.58%. These signs do not validate a new risk-off filter.

## P. Theme interactions

The two preregistered Theme interactions were not run because their necessary PIT source failed qualification. This is an explicit data deferral, not a null measured effect.

## Q. Temporal robustness

[Year, development and descriptive-2026 rows](../artifacts/stage1_context_discovery_v1/context_temporal_robustness.csv) are present for every numeric bucket and regime. H1A HIGH-versus-LOW TP5 net contrasts were about 0 / -1 / -7 percentage points in 2024/2025/2026; H1B LOW-versus-HIGH about +1 / -5 / -10; H2 shortline-versus-trend about -3 / -1 / +1. None has consistently favorable net and left-tail directions. The 2026 slice is **not** clean validation.

## R. Board conditional context

Board-intent results are kept separate throughout. Conditional TP5/H5 net means were +1.14% in RISK_ON (N=136), -0.51% in RISK_OFF (N=24), +1.16% in TREND_DOMINANT (N=95), and -0.34% in SHORTLINE_DOMINANT (N=85). These are counterfactual *conditional-on-fill* results, not executable board edge or fill probability.

## S. Cluster bootstrap

The [fixed-seed bootstrap](../artifacts/stage1_context_discovery_v1/context_cluster_bootstrap.json) resamples securities with all their repeated Entries for four pre-specified contrasts in both separate populations, 2,000 draws. Normal TP5 net favorable-minus-adverse contrasts: H1A -2.10 pp (95% percentile interval -5.52 to +1.27 pp); H1B -4.54 pp (-8.12 to -0.84 pp); H2 -1.00 pp (-4.35 to +2.30 pp). Intervals describe within-sample uncertainty; they do not correct prior outcome inspection, multiple testing or execution assumptions.

## T. Hypothesis candidates

The [shortlist](../artifacts/stage1_context_discovery_v1/context_hypothesis_candidates.json) evaluates three preregistered priority questions and admits **zero**. Each fails material improvement and tail-reduction gates; H1A/H1B also fail monotonicity, and annual directions are not stable. `NO_STABLE_CONTEXT_HYPOTHESIS` is the substantive result. No approved filter exists.

## U. Multiple-testing / caveats

Eight correlated numeric features, two regimes and one interaction were inspected. No p-value or best-exit search is used; even the registered contrasts were conceived after earlier outcome exposure. Entry events are not independent securities, 2026 is not untouched, T same-close fills are unverified, board fillability remains unqualified, and the SH/SZ source excludes Beijing. The surprising inverse H1B interval cannot be promoted into a “trade high-negative-feedback” rule.

## V. Quality / no-lookahead

The [quality receipt](../artifacts/stage1_context_discovery_v1/context_discovery_quality_receipt.json) is PASS: 505 unique Entry-feature/outcome joins, 4040 fixed trade keys, exact regime/bin coverage, and all eight exits independently reconciled with the parent baseline in the [fixed-exit regime table](../artifacts/stage1_context_discovery_v1/context_all_exits_regime_summary.csv). One daily status gap, 2026-07-10 (1007 of D3 stocks, 80.60% coverage), is explicitly null-gated with its adjacent streak window; no frozen Entry is on that date. The daily D3/D4 scan stops at the last frozen Entry T (2026-09-22), and each earlier Entry receives only its chronological <=T prefix; the as-of guard rejects T+1 access. Source end-of-day timestamp is not historical order-execution evidence.

## W. Determinism

Two feature-builder runs produced byte-identical private Entry/daily Parquets, inventory and bins; two assessment runs produced byte-identical public outputs and manifest. See [reproducibility receipt](../artifacts/stage1_context_discovery_v1/context_discovery_reproducibility.json).

## X. Run manifest

`MARKET_STYLE_THEME_CONTEXT_DISCOVERY_RUN_V1` freezes parent/source/private/public hashes, eight variables, fixed formulas/bins/regimes/interactions, admission rules, runtime and test receipt in the [manifest](../artifacts/stage1_context_discovery_v1/run_manifest.json).

## Y. Tests

Focused: 9 passed, including an independent source-day reconciliation. Full suite: 625 passed, one **pre-existing** `tests/test_research_integration.py::test_frozen_original_head_full_pipeline_through_snapshot` failure: `pipeline_reference.json` actual SHA `c56f2129ab620f4943e66c300895c81620c313902d4e58bece3ebbfaecfdfeae`, `.sha256` expected `f021c169b1e3739f5b9aa227251057c7313f32b0ce2b57d5f3e2d486ab29fcda`. It is not changed in this stage. See [test receipt](../artifacts/stage1_context_discovery_v1/context_test_receipt.json).

## Z. Protected files

`inputs/three_board_daily_input.xlsx`, `inputs/three_board_daily_input_2026_backtest.xlsx`, and `手工统计.xlsx` retain their provided SHA-256 values and are neither staged nor committed.

## AA. Git delivery

This stage is delivered on `codex/rss-stage1-market-style-theme-context`, stacked as a Draft PR on PR #31. Neither the parent PR nor previous commits are rewritten or merged.

## AB. Explicitly not completed

No new Observation/Entry filter, TP/SL optimization, ML, portfolio NAV, live deployment, independent validation, edge confirmation or board fillability proof is claimed.

## AC. NEXT_ACTION

`STAGE1_STRATEGY_REASSESSMENT`: decide whether normal-close Entry timing itself should be revised or discontinued, and whether board-intent should remain conditional research only. No forward-validation hypothesis is available to freeze from this study. This next stage is a recommendation, not initiated here.
