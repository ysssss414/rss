# Stage 1 CLOSE_LIMIT_UP_10PCT_V2 validation stop

`STAGE1_CLOSE_LIMIT_UP_V2_AND_TARGETED_STRATEGY_SMOKE = BLOCKED_CLOSE_LIMIT_UP_V2_VALIDATION_FAILED`.
This is a validation stop under the human-approved 9.91%–10.09% rule, not a
return to market-wide official absolute-limit-price backfill. The V2 definition
is recorded in the [blocked contract](../artifacts/stage1_close_limit_up_v2/contract.json)
and implemented separately from frozen `IS_CLOSE_LIMIT_UP_V1`. It is **not**
promoted to the strategy runtime or used to create formal events.

The frozen Gate B 13-case reconciliation found one decisive ordinary 10%
false negative. On 2024-07-04, `600518.SH` had already removed its risk warning:
the issuer's 2024-07-03 announcement states the stock would resume on July 4
under a 10% daily price limit. The qualified Gate B case records prior valid
raw close CNY 1.93, reconstructed official upper CNY 2.12, and official daily
close/high CNY 2.12. D5 was unchanged and the ex-right screen was false. The
V2 formula gives `(2.12 / 1.93 - 1) * 100 = 9.844559585492...%`, which is
below the fixed 9.91% floor. `1.93 * 1.10 = 2.123` rounds to a CNY 0.01 upper
price of CNY 2.12; the percentage shortfall is a tick-rounding effect. This
is not an IPO, ex-dividend, old 5% risk-warning, or vendor-ST-label exception.
The [case-by-case receipt](../artifacts/stage1_close_limit_up_v2/gate_b_reconciliation.json)
records V1, V2, raw observations, and other expected semantic differences.

The raw snapshot's main-board prices are stored as DOUBLE, but a fixed-point
audit found all 2,084,113 main-board bar prices on CNY 0.01 ticks after decimal
normalization; raw `close == high` and normalized-tick equality had identical
counts (115,093). Direct binary-float multiplication by 100 falsely labels
398,977 rows off tick, so V2 code uses `Decimal(str(value))` and rejects
genuine off-tick inputs. The snapshot has no frozen canonical raw-return
primitive, so the new code uses the previous valid trading bar, not the
previous calendar day. D5 factor changes or a positive ex-right screen return
`SPECIAL_REFERENCE_PRICE_REQUIRED`; D8 is not read by the predicate.

For discovery only, the 5%-floor/raw-bar detector plus special-reference
supplement produced 5,540 V2 provisional candidates on 1,377 securities and
13,958 dependency security-dates. Its scan observed zero ordinary V2 predicate
hits missed by the broad detector. The prior vendor-supplemented V1 provisional
inventory (6,388 candidates, 15,489 dependencies) remains untouched as an
audit baseline. Neither provisional set is a formal Observation. Because the
Gate B stop condition fired, the 15,489 old dates were **not** promoted or
reclassified into a final regime inventory, no formal five-day strict count
was materialized, and no lifecycle/Entry A/Entry B/Smoke run was attempted.

The frozen `LIMIT_UP_COUNT_5_V1` contract requires each of the five stock
observations to have a valid symmetric 10% daily constraint. In a 4/5 pattern,
the fifth non-hit day remains a targeted regime dependency. `OBSERVATION_POOL_V1`
also requires an eligible validated 10% universe on each Entry evaluation day.
The four or five V2 price-path hits therefore cannot alone certify an
Observation or Entry; unknown exception and risk-warning facts remain
fail-closed. No existing V1 primitive or lifecycle contract was edited.

No forward returns, D8 outcome research, event study, PnL, or parameter
optimization was performed. The next decision belongs to the user: retain
9.91% as an intentionally narrower *operational* event and explicitly waive
the known ordinary-limit false-negative stop, or authorize a revised versioned
predicate and validation criteria. Neither choice was inferred here.
