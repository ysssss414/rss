# Stage 1 trading-regime rulebook preflight

Result: `BLOCKED_PIT_RISK_WARNING_STATE_UNQUALIFIED`.

The official, effective-dated price-limit matrix is in
`artifacts/stage1_trading_regime/trading_regime_rulebook_v1.json`; source receipts
and the reproducible V1 preflight are beside it. This is a **source-qualified
rulebook candidate**, not an authorization to materialize canonical daily
constraints. No DailyTradingConstraint or V2 snapshot has been written.

## Rule source and precedence

The [SSE 2023 rule](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml)
and [SSE 2026 rule](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
cover the two Shanghai epochs. The corresponding Shenzhen rules are the
[2023 text](https://docs.static.szse.cn/www/lawrules/rule/trade/W020230217564423808793.pdf)
and [2026 text](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf).
The [BSE 2021 rule](https://www.bse.cn/jygl_list/200010919.html) and
[BSE 2026 rule](https://www.bse.cn/jygl_list/200028217.html) cover Beijing.
The [CSRC Shanghai bureau explanation](https://www.csrc.gov.cn/shanghai/c105566/c7643909/content.shtml)
independently confirms the 2026-07-06 main-board risk-warning change from 5%
to 10%, while ChiNext and STAR remain at 20% and BSE at 30%.

| Board | Through 2026-07-05 | From 2026-07-06 | IPO no-limit |
| --- | --- | --- | --- |
| SSE/SZSE main ordinary | 10% | 10% | first 5 exchange sessions |
| SSE/SZSE main risk warning (ST or *ST) | 5% | 10% | not a status-derived shortcut |
| SZSE ChiNext | 20% | 20% | first 5 exchange sessions |
| SSE STAR | 20% | 20% | first 5 exchange sessions |
| BSE | 30% | 30% | listing first exchange session |

All boards have no daily price limit on the first delisting-arrangement day;
continuing delisting days use the relevant board limit, with a distinct
main-board risk-warning override in the 2023 epoch. Main-board relisting day
one is also a documented no-limit exception. These are **daily price limits**,
not intraday valid-order-price ranges. The rulebook uses explicit `null`
rates when `has_price_limit=false`.

Resolution uses exact exchange, board, effective date and qualified
security-day facts. Explicit no-limit phase overrides take precedence, then
continuing delisting, then pre-2026 main-board risk warning, then the board
base rule. Equal-priority matches fail closed. Supplier limit rates and ST
labels do not participate in rule selection.

## PIT fact preflight

V1 has 5,222 securities and 3,379,281 supplier daily-status rows, including
91,033 rows labeled `is_st_sec=true`. Its master has only SH/SZ boards: 1,702
SSE main, 618 STAR, 1,494 SZSE main and 1,408 ChiNext; BSE validation can
therefore only use a rule fixture, not a V1 security-day. The master has no
`security_type` column and no populated delisting date; the daily status has
neither `status_effective_date` nor `available_at`.
Four securities listed in the last four exchange sessions of 2023 can still
be in their five-session IPO exception in early 2024, but V1's calendar starts
on 2024-01-02; the preceding official sessions must be supplied before a
listing-phase engine can classify them.

The decisive conflict is 600518.SH on 2024-07-04. The
[issuer's exchange-disclosed notice](https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF)
specifies that risk warning was removed from market open on 2024-07-04 and
the applicable daily limit became 10%. V1's `is_st_sec` remains `true` that
day and becomes `false` on 2024-07-05. Separately, 600187.SH on 2026-07-06
has a V1 supplier rate of 5% although the effective Shanghai rule specifies
10%; its supplier absolute upper limit is consistent with 10%. These are
reconciliation anomalies, **not** grounds to promote supplier prices to
canonical authority.

The existing Gate B contract qualified only 13 representative security-days,
not the 3.38 million V1 rows. An issuer/exchange event ledger covering
introductions, removals and conversions with effective dates and as-of
provenance is still required before daily status can be qualified. It must
reconcile against, rather than silently replace, the supplier labels. Once
that ledger exists, the daily engine can address listing-session counting,
board/security-type validation, suspension, vendor reconciliation, PIT tests,
full-market materialization and V2 freezing. Until then, those steps are not
run and Real Strategy Smoke remains out of scope.

Reproduce this preflight with
`python -m scripts.audit_trading_regime_rulebook_v1` from the repository root.

The focused rulebook tests pass (17/17). The full repository suite currently
reports 509 passed and one failure in the unchanged Stage 0 baseline:
`pipeline_reference.json` hashes to
`c56f2129ab620f4943e66c300895c81620c313902d4e58bece3ebbfaecfdfeae`,
whereas its committed `.sha256` sidecar contains
`f021c169b1e3739f5b9aa227251057c7313f32b0ce2b57d5f3e2d486ab29fcda`.
This work does not rewrite the protected baseline.
