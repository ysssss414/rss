# Stage 1 frozen Entry signal event study

## Boundary and result

`STAGE1_ENTRY_EVENT_STUDY` characterizes the price path after the 505 formal
Entry signals frozen by `REAL_STRATEGY_SMOKE_RUN_V1`. It is **not** a backtest of
executed trades. Neither normal-close intent nor limit-up board intent proves a
fill. No execution probability, next-open fill, slippage, fees, Exit, stop,
take-profit, PnL, annualization, parameter search, or overall edge verdict is
part of this stage. The earlier
[`stage1_entry_event_study_contract.md`](stage1_entry_event_study_contract.md)
specified a different modeled-fill study; its D0/open-price clock does not
govern this frozen real-signal study.

The [run manifest](../artifacts/stage1_entry_event_study_v1/run_manifest.json)
binds the parent smoke, snapshot, population, code, config, runtime, tests,
output hashes, and the outcome contract. The parent Entry CSV SHA-256 is
`82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1`.
Its 505 unique `(security, Entry date, Observation instance)` keys comprise
426 Entry A and 79 Entry B, with 263 board-close and 242 normal-close intents.
Every Entry joins exactly one of the 993 frozen formal Observations. The 21
review-required candidates and all provisional/rejected candidates remain
outside this population. The physical `REAL_RESEARCH_SNAPSHOT_V1` manifest
SHA-256 is `a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`;
its cutoff is 2026-09-23.

## Event clock and comparable price

T is the signal date, H0. `signal_anchor_close` is the D3 **raw close on T**,
cross-checked against the frozen Entry CSV; it is not a `fill_price`. H1, H3,
H5, H10 and H20 mean the kth **exchange trading session strictly after T**.
The private path also records every H1–H20 session. At an available endpoint,
`signal_forward_close_return_Hk = comparable_close_Hk / signal_anchor_close - 1`.
These are descriptive signal price returns, not realized trade returns or a
strategy win rate.

The qualified, frozen D8 `ENTRY_COMPARABLE_FORWARD_PATH_V1` single-event factors
multiply future raw high/low/close from each effective date onward, mapping
them to T's raw-price scale. The source event kind/hash and cumulative factor
are retained on the private daily path, beside raw and comparable OHLC.
Canonical D8 actions have pre-effective known dates and reconcile to D5 as
documented in the [D8 qualification](stage1_d8_and_research_snapshot.md).
D8 is loaded **only by the outcome runner**, never by candidate, Observation,
Entry, RSI, or MA code. This is comparable **price** behavior; cash-dividend
income is excluded, and no total-shareholder-return claim is made.

A D8 exclusion intersecting `(T, Hk]` makes that horizon
`ADJUSTMENT_UNRESOLVED`, with null comparable price/return/MFE/MAE. Three frozen
Entry paths intersect such a row within H20; the other, earlier horizons are
still retained. Invalid factors similarly fail closed. Raw future prices are
never silently substituted for unresolved comparable prices.

`RIGHT_CENSORED` is assigned independently at each horizon if that exchange
session lies beyond the frozen cutoff; its return and full-window excursions
are null. An Hk suspension or missing/invalid quote is `NO_VALID_QUOTE`, with
null endpoint return and no forward fill; valid observed sessions elsewhere
in the window may still contribute MFE/MAE. An absent D4/D6 status is
`OTHER_MISSING`. A verified delisting date at or before a horizon is
`TERMINAL_NO_QUOTE`, not an invented -100% liquidation value. A one-day
`is_wd_sec` flag with a valid quote is not, on its own, a terminal event.

For Hk, MFE is the maximum comparable high over valid T+1…T+k sessions
divided by the anchor minus one; MAE is the minimum comparable low on the same
window divided by the anchor minus one. They are **not clipped at zero**.
Source dates, session indices and valid-session counts are retained. This
defines observed excursions, not an executable take-profit or stop-loss.

## Descriptive observations

The [horizon summary](../artifacts/stage1_entry_event_study_v1/entry_event_horizon_summary.csv)
reports each return distribution's mean, median, population standard
deviation, p10/p25/p50/p75/p90 and positive/zero/negative fractions. These
fractions use the available-return N, not all 505. Percentiles linearly
interpolate positions `(N-1)*p`; no IID standard errors or p-values are used.
The [excursion summary](../artifacts/stage1_entry_event_study_v1/entry_event_path_excursion_summary.csv)
separately reports MFE/MAE distributions and source-session frequencies.

| Horizon | Available / 505 | Right censored | No valid Hk quote | Other missing | Mean close return | Median close return | Median MFE | Median MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H1 | 504 | 0 | 1 | 0 | -0.53% | -1.06% | +6.88% | -6.30% |
| H3 | 502 | 2 | 1 | 0 | -2.63% | -4.64% | +8.99% | -10.01% |
| H5 | 503 | 2 | 0 | 0 | -3.88% | -6.55% | +9.98% | -13.66% |
| H10 | 500 | 2 | 0 | 3 | -5.86% | -11.59% | +10.97% | -18.41% |
| H20 | 486 | 15 | 1 | 3 | -6.77% | -14.33% | +14.06% | -22.01% |

The non-monotone available N is expected: a suspended H3 endpoint can have a
valid H5 endpoint. The three H20 `other missing` outcomes are D8 exclusions;
the [coverage receipt](../artifacts/stage1_entry_event_study_v1/entry_event_outcome_coverage.json)
keeps that category explicit. MFE/MAE N can exceed close-return N when Hk
itself has no quote but other sessions in the window do.

## Prespecified slices and limitations

The [subgroup table](../artifacts/stage1_entry_event_study_v1/entry_event_subgroup_summary.csv)
contains only the prespecified partitions: Entry A/B, 4/5 versus 5/5,
board-close versus normal-close intent, V3 price-path versus operational-status
trigger qualification, and truncated/non-canonical versus full-prefix RSI.
Each partition reports its N, horizon availability and the same descriptive
return statistics; `N < 20` is explicitly marked
`SMALL_SAMPLE_DESCRIPTIVE_ONLY`. The [provenance sensitivity table](../artifacts/stage1_entry_event_study_v1/entry_event_provenance_sensitivity.csv)
compares all formal Entries with each provenance subset without selecting a
new sample or altering the signal contract.

Among Entries, 496 are 4/5 and only **9** are 5/5; 498 are V3-qualified and
only **7** operational-qualified; 502 have truncated/non-canonical RSI replay
and only **3** have a full available listing prefix (2 with H20 outcomes).
The parent Observation counts (972/21, 973/20, 987/6) are a different grain,
not subgroup denominators here. These small slices cannot support a strong
comparison. The 505 events span **434 securities**: 61 securities have more
than one Entry, 71 events are repeats beyond first-per-security, and the
maximum is 3. Thus event rows are not independent-company observations. The
year counts are 155 in 2024, 234 in 2025, 116 in 2026; per-year horizon
availability is in the [summary](../artifacts/stage1_entry_event_study_v1/entry_event_study_summary.json).
No regime-performance or significance study is implied.

## Reproduction, privacy and next stage

Run the offline study with the local frozen snapshot and Python dependencies:

```powershell
python scripts/run_entry_event_study_v1.py --focused-pass --full-preexisting
```

The runner refuses an input/population hash drift, cross-checks the D3 anchor,
materializes 505 event rows and 10,100 daily-path rows, and refuses different
Parquet bytes on an unchanged-code rerun. The
[quality receipt](../artifacts/stage1_entry_event_study_v1/entry_event_study_quality_receipt.json),
[reproducibility receipt](../artifacts/stage1_entry_event_study_v1/entry_event_study_reproducibility.json)
and [case audit](../artifacts/stage1_entry_event_study_v1/entry_event_study_case_audit.json)
record the checks and representative outcomes. Event-level
`entry_event_outcome_v1.parquet` and H1–H20
`entry_forward_path_h1_h20_v1.parquet` are written under ignored
`.local_research_data/stage1_entry_event_study_v1/`, not committed: they
contain vendor-derived per-security price paths. The public manifest includes
their SHA-256 fingerprints, while the PR contains only aggregate/statistical
artifacts and bounded case audits. Reproduction elsewhere requires the private
frozen snapshot; public receipts alone do not recreate the prices.

The only proposed next stage is `STAGE1_EXIT_AND_PNL_CONTRACT`; it is not
implemented here. That stage would need its own execution/fillability,
Exit, price, fees/slippage and realized PnL semantics. None are inferred from
this event study.
