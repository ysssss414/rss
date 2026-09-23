# Stage 1 RSI warm-up and trade lifecycle contract

**STAGE1_EXIT_LIFECYCLE_STOP.** The strategy-independent foundation is complete, but no
Stage 1 exit strategy has been authorized. The stop reason is
`EXIT_STRATEGY_SEMANTICS_UNAUTHORIZED`. This is not a failure of the RSI warm-up,
sell-execution, or lifecycle foundation.

## Observation/Entry closure

PR #12 was verified at head
`267828033ef59b4662e9169d089614c69d765713`, base `main`, and state `OPEN`.
Observation/Entry (40), primitives (27), foundation (50), Gate C (39), and Gate A/B
(72) targeted tests passed. The complete pre-merge suite passed 375 tests after the
documented temporary use of the committed LF Stage 0 reference bytes; the original
Windows checkout bytes were restored. PR #12 was merged as
`47e2c609d9c5c4d6b5ecb0b8ee5e05a8b8d6e0fc`, which became the public `main`
used for this branch.

## RSI_WARMUP_POLICY_V1

`RSI14_PROJECT_V1` is unchanged: first-valid-delta seed, Decimal recurrence, flat
sequence undefined, and `PIT_ADJUSTED_CLOSE_V1`. If the complete valid history from
the latest listing/relisting through T is available, the canonical value always
replays that full prefix. It is never replaced by a 120- or 150-observation suffix.
The numerical minimum remains two price observations.

Only a slice that begins after the true reset uses a warm-up convergence policy.
The minimum acceptable truncated warm-up is 120 observations and the default target
is 150. A default slice with 120–149 observations is usable but discloses that the
150 target was not met; fewer than 120 is
`INSUFFICIENT_TRUNCATED_WARMUP`. These limits do not enter
`HistoricalUniverse`: a security with only 80 observations since listing uses all
80 in `FULL_AVAILABLE_PREFIX` mode.

The deterministic convergence replay covers trend, high-volatility, alternating,
near-flat, and threshold-near-70 sequences. Across 505 evaluated endpoints:

| Slice | Max absolute error | P95 absolute error | >70 mismatch | <70 mismatch | recross-70 mismatch |
|---|---:|---:|---:|---:|---:|
| 60 | 1.285875764523 | 1.071157594383 | 0 | 0 | 0 |
| 90 | 0.141633082405 | 0.123657092968 | 0 | 0 | 0 |
| 120 | 0.015361837322 | 0.012642248827 | 0 | 0 | 0 |
| 150 | 0.001663327154 | 0.001329090482 | 0 | 0 | 0 |
| 180 | 0.000234995659 | 0.000162850817 | 0 | 0 | 0 |

The threshold sequence's canonical RSI spans 69.121701492134 to 73.655551388658
and contains 33 strict below-70 to above-70 recrosses, so zero mismatch is a tested
decision result rather than the absence of threshold crossings. Windows 60 and 90
are diagnostic only and remain inadmissible under the policy.

## TRADE_LIFECYCLE_V1

The lifecycle consumes, but does not generate:

`EntrySignal -> EntryExecution -> Position -> ExitSignal -> ExitExecution -> ClosedTrade`.

Its states are `NO_POSITION`, `ENTRY_PENDING`, `OPEN`, `EXIT_PENDING`,
`CLOSED`, `ENTRY_FAILED`, and `EXIT_UNRESOLVED`. Only an entry
`MODELLED_FILL` creates a deterministic `position_id`; `NO_FILL` and
`EXECUTION_UNRESOLVED` create no position. A second entry while an economic
position exists is recorded as `IGNORED_WHILE_POSITION_OPEN`; pyramiding,
averaging, lots, and T trading are absent.

A position records security, entry signal/execution dates, raw execution price,
entry reasons, observation instance, execution policy, snapshot, contract versions,
factor lineage, indicator basis, and the explicit quantity semantic
`ONE_BASELINE_POSITION_NO_QUANTITY_OR_LOTS`. A `ClosedTrade` only closes the
event/provenance chain. It has no return, profit, loss, win-rate, drawdown, alpha,
or other performance field.

Exit `NO_FILL` returns the lifecycle to `OPEN`, retains the position, increments
the attempt count when the signal is accepted, and requires an external exit rule
to emit any later signal. Exit `EXECUTION_UNRESOLVED` retains the position and
pending signal in `EXIT_UNRESOLVED` with a structured reason. The lifecycle never
auto-sells.

Splits, dividends, rights, or security-code changes fail closed as
`POSITION_CORPORATE_ACTION_UNRESOLVED`. Delisting, relisting, long suspension,
or trading-lifecycle termination fail closed as
`POSITION_TRADING_LIFECYCLE_UNRESOLVED`. Neither path invents a last-close, zero,
delisting, or other exit price.

## NEXT_SESSION_SELL_V1

An external exit signal finalized at T EOD maps to an order at the next frozen
exchange session at 09:15 and an opening-print decision at 09:25. A valid,
on-tick raw open strictly above the validated lower limit produces
`MODELLED_FILL` at raw open with zero modeled slippage. An open exactly at the
lower limit produces `NO_FILL`. Missing session/data/open, suspension,
non-trading state, invalid/late constraint, or an off-tick/out-of-range open
produces `EXECUTION_UNRESOLVED`. Decimal/tick arithmetic is used; no float
equality or automatic rollover is allowed.

## Exit discovery

The current RSS repository contains fixed-holding-period outcome analysis from
Stage 0, but that is explicitly a descriptive backtest layer rather than an
authorized Stage 1 exit signal. It also says the entry project is not merged with
any `rsi_exit` business module.

A read-only review of `ysssss414/rsi-exit` at public-main commit `cb7a7d3`
found related legacy logic: base-state exits from hard-exit, RSI/MA20 thresholds,
third formal bearish-divergence exit, position caps, and a later RSI/MA20 re-entry
condition. That repository has different state, data, position, peak/divergence,
priority, and re-entry semantics and does not define an RSS `ExitSignal` mapped
to `NEXT_SESSION_SELL_V1`. It is therefore
`EXIT_CANDIDATE_FOUND_NOT_AUTHORIZED`, not a Stage 1 contract.

`EXIT_SIGNAL_V1` is not implemented. A human must choose or authorize the
Stage 1 exit contract before any exit-rule implementation. PnL and performance
work remain prohibited.
