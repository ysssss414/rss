# RSS Stage 1 Gate B — Hybrid Data Contract Closure

**GATE_B_STOP.** Gate A PR #4 was merged as a qualification artifact; its **PATH B** and **Gate STOP** remain unchanged. Gate B has verified diagnostic checks and a fail-closed contract design, but none of B1–B4 is closed for a causal Stage 1 study. No Stage 1 strategy, signal, backtest or production data pipeline was added.

## A. Gate A closure

PR [#4](https://github.com/ysssss414/rss/pull/4) was OPEN before this task. Its head was `7735f045d5de69b85172c3a1d083dff2625610c0`, base was `main`, GitHub reported MERGEABLE, and its 15 files were limited to Gate A scripts, tests, redacted artifacts and report. The three user Excel files and strategy implementation were absent. Before merge, manifest integrity was **14/14**, Gate A tests **15 passed**, and the full suite **162 passed**. PR #4 was merged with merge SHA `03dae647d1b450e40a9eb1f308111253b1fb1334`; local `main` was fetched and fast-forwarded to that SHA.

On merged `main`, the *committed Git blob bytes* of all 14 Gate A evidence targets match the manifest. Windows `core.autocrlf=true` converts all 14 text files to CRLF on checkout, so a raw hash of those **working-tree** bytes differs. This is a checkout representation difference, not a changed Git artifact. The Stage 0 `pipeline_reference.json` test has the same issue in the opposite direction: its frozen hash names the committed LF bytes. A direct raw-byte test on the initial merged checkout stopped after 111 passes and one reference-hash failure; temporary use of the already committed LF bytes produced **162 passed**. The original checkout bytes were restored afterwards. See [baseline receipt](../artifacts/stage1_gate_b/baseline_receipt.json).

## B. Frozen baseline

The Gate B branch `codex/rss-stage1-gate-b-hybrid-data-contract` starts directly from merged `main` at `03dae647d1b450e40a9eb1f308111253b1fb1334`. Stage 0 remains frozen at annotated tag `research-stage0-v0.1.0` → `fdf4e5a15f120f7cf1c533c38b8c9214cb2130de`; its source commit is `d856f6f84911e55e9ed6208d3610272cc323ee4d`. The working tree was clean apart from the three pre-existing user Excel states. The first Excel is **already Git-tracked** and modified; the other two are untracked. All three were read-only, unstaged and excluded from Gate B commits.

## C. B1 — volume and amount unit contract: OPEN

[Machine-readable unit contract](../artifacts/stage1_gate_b/field_unit_contract.json) records the qualified source as AmazingData 1.1.6 `query_kline(period=day)`. The local SDK manual (SHA-256 `8d2f671828140e1e16007beef218dc92d34bc81b9288afdcea8f6814cbbd8cd9`, PDF page 145) calls `volume` “成交总量” and `amount` “成交总金额”, without A-share Kline units. The installed Kline field metadata has no unit descriptions. A [public API transcription](https://amazing.ptradeapi.com/) describes the interface; its industry-index daily table states 股 and 元, but that is a **different endpoint** and cannot establish Kline units.

Three live-capture arithmetic checks reproduce the raw values:

| Security / date | raw amount ÷ raw volume | raw low–high |
|---|---:|---:|
| 000001.SZ / 2024-01-08 | 9.178084 | 9.11–9.30 |
| 300750.SZ / 2024-09-27 | 221.824640 | 211.67–231.68 |
| 600519.SH / 2024-06-18 | 1531.230952 | 1516.68–1554.54 |

The ratios are internally plausible. Equal rescaling of amount and volume leaves each ratio unchanged; this check **cannot** prove shares versus lots or yuan versus another amount scale. Raw units and conversion multipliers remain `null`; canonical targets are `share` and `CNY`. VWAP, turnover rate, amount thresholds, volume comparisons and liquidity metrics remain blocked. Historical float shares and market-cap fields are not part of the qualified Kline capture. OHLC and `PRECLOSE` are separate price fields; the ex-dividend case confirms that yesterday's raw close cannot always stand in for the reference price. Stage 0's `units_verified=false` remains appropriate.

## D. B2 — historical security status: OPEN

[Effective-dated schema](../artifacts/stage1_gate_b/historical_status_contract.json) requires exchange-qualified `security_id`, `status_type`, `effective_from`, exclusive `effective_to`, source document/hash/version, first publication and revision timestamps. The join is by security and local trade date, with source visibility at the actual decision time. Missing or conflicting intervals resolve to `UNKNOWN` and are excluded; a current name or status never backfills history.

The [issuer's 2024-07-03 notice](https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF) makes the 600518.SH risk-warning removal effective on **2024-07-04**. The captured historical SDK field `IS_ST_SEC` remains `1` that day, including an independent one-day requery. Its July 3 suspension flag is useful corroboration, but the ST transition cannot be accepted from the SDK as-is. The five synthetic effective-interval checks exercise normal→ST, ST→normal and *ST joins; they do **not** establish real historical coverage. IPO, delisting, suspension, relisting and board transitions require dated official events and completeness checks.

Exchange/issuer notices are the proposed authority for effective events; the exchange's per-security daily static reference is the proposed authority for trading parameters. Neither a complete historical event archive nor its publication/revision record has been acquired. [SZSE's technical notice](https://www.szse.cn/marketServices/technicalservice/notice/t20200529_577838.html) confirms that per-security limit parameters are distributed in static reference information, but does not supply the required historical archive. Vendor conflicts are quarantined; no universal one-day shift is applied.

## E. B3 — limit price and close-limit semantics: OPEN

[Machine-readable limit contract](../artifacts/stage1_gate_b/limit_price_contract.json) defines the proposed canonical close-limit test as **valid raw close equals the exchange's official upper limit at the applicable tick**. An intraday high touch is diagnostic only. `pct_chg >= 9.9%` is not an accepted substitute. A confirmed no-limit day has `null` official limits, not a zero-price limit.

| Captured example | Vendor ratio and upper limit | Diagnostic result |
|---|---|---|
| 000001.SZ 2024-10-09 | 10%, 14.17 | matches 12.88 reference rounded to 0.01; close 11.68 is not limit |
| 600518.SH 2024-07-02 | 5%, 2.02 | matches 1.92 reference; historical ST sample |
| 300750.SZ 2024-09-30 | 20%, 272.17 | matches 226.81 reference |
| 688981.SH 2024-09-30 | 20%, 59.99 | raw close is 59.99, equal to vendor upper limit |
| 603194.SH 2024-12-24 | 999, 0 | IPO special representation; official sentinel meaning unverified |
| 600519.SH 2024-06-19 | 10%, 1639.68 | reference 1490.62 differs from previous raw close 1521.50 |

The 600518.SH 2024-07-03 SDK rate is 10% while its price limits reflect about 5%. Diagnostic `Decimal` half-up rounding also passes a synthetic edge case; it is **not** a certified rule engine. A synthetic near-limit high touch with close one tick below the limit is correctly rejected as a close-limit event. No independent exchange daily limit/reference archive was available to establish authority for all examples.

Rules must be joined by effective date. [SSE's 2026 rule revision](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml) changes main-board risk-warning limits from 5% to 10% on **2026-07-06**; [SZSE's ChiNext reform explanation](https://investor.szse.cn/index/update/t20200807_580310.html) documents its earlier 10%→20% transition and IPO exceptions; [BSE's rule explanation](https://www.bse.cn/qt/200025234.html) describes 30% and exceptions. Consequently, Stage 1's 10% universe cannot be inferred from ticker prefix or today's ST flag. It needs historical board, risk status, IPO/relisting phase, corporate-action reference, rule version and official daily limit prices.

## F. B4 — point-in-time research tiers: OPEN

[Tier contract](../artifacts/stage1_gate_b/pit_research_tier_contract.json) defines:

| Tier | Admission rule | Current outcome |
|---|---|---|
| 0 | Raw fields with verified unit/identity, dated status, publication/revision time and frozen version | Bars, status and limits are **not yet admitted** |
| 1 | Deterministic <= decision-time derivations only from admitted Tier 0 | RSI, moving average and limit count are design examples only |
| 2 | Context with explicit first availability, revisions and at-time snapshot | Theme, sector, mainline, style, news and manual annotations need further evidence |
| 3 | Current/backfilled or post-hoc classifications without a frozen at-time snapshot | Forbidden in causal features; allowed only in labeled ex-post diagnostics |

The negative fixture has a record effective on July 4 but first published July 5; the guard rejects its use at July 4 EOD. Later revisions, missing publication times and timezone-naive timestamps are also rejected. This proves the *guard*, not historical PIT coverage. Gate A showed `kline_time` is a market date rather than a publication timestamp; factor refresh stability does not establish historical versions. `availability_verified=false` remains correct.

## G. Validation and tests

[Validation cases](../artifacts/stage1_gate_b/validation_cases.json) are deterministic and use only redacted Gate A capture plus labeled synthetic fixtures. [Offline validator](../scripts/validate_stage1_gate_b_contract.py) reproduces three unit ratios, six vendor-limit examples, five synthetic status assertions, two synthetic limit assertions, two live conflicts and a future-leak rejection. It deliberately reports `gate_b=STOP`; it neither logs in nor classifies unknown units as verified.

- `python -B scripts/validate_stage1_gate_b_contract.py`: PASS.
- `python -B -m pytest -q tests/test_gate_b_contract.py`: **6 passed**.
- `python -B -m pytest -q tests/test_gate_a_qualification.py tests/test_gate_b_contract.py`: **21 passed**.
- Full repository pytest suite: **168 passed**, zero failures/skips, after temporarily using the already committed LF bytes of `baselines/stage0/pipeline_reference.json`; a `finally` block restored its original checkout bytes. JUnit is in ignored `.test_tmp/gate_b_full_20260922.xml`. No frozen baseline or production file was committed as changed.

The qualification manifest on merged main verifies **14/14 committed Git blobs**. Raw working-tree bytes after Windows CRLF checkout are not an appropriate proxy for those immutable Git blob hashes; the [receipt](../artifacts/stage1_gate_b/baseline_receipt.json) records the distinction. Gate B's new hashed artifacts have explicit `-text` Git attributes so future checkouts retain their original bytes.

## H. Protected user files

| File | Before and after SHA-256 | Tracked | Staged / committed by this task |
|---|---|---|---|
| `inputs/three_board_daily_input.xlsx` | `38dabdc991d26d4d1b5873bfdcbfb5dd29db4671fa1760c20d932d3d17d9b9fe` | yes, pre-existing modified state | no / no |
| `inputs/three_board_daily_input_2026_backtest.xlsx` | `282c4abdbb5901f5e3ee33512e7256887c1e92ae129662f631397fbd6943cf22` | no, pre-existing untracked state | no / no |
| `手工统计.xlsx` | `2fdf23576a7d220e994cf74143236a0b3db34e9e495f769927d0a65c994f8277` | no, pre-existing untracked state | no / no |

All three after hashes were verified against the before values after the Gate B validation and full regression. They will be checked once more before commit and push.

## I. Gate B decision and Git scope

The independent Gate B branch is based on the PR #4 merge. Gate B adds only contract documents, machine-readable inventories/fixtures, an offline validator, tests and integrity attributes. The qualification result remains **PATH B** with **Gate A STOP**. Gate B is **STOP** because the four contract families below remain open. A separate PR is for review and must not be merged automatically.

| STOP_REASON / BLOCKING_CONTRACT | AVAILABLE_EVIDENCE | MISSING_EVIDENCE | WHY_CURRENT_DATA_IS_UNSAFE | MINIMUM_NEXT_ACTION |
|---|---|---|---|---|
| B1 units | Kline schema, three ratio checks | A-share Kline volume/amount unit and conversion authority | VWAP, turnover and liquidity could be scaled incorrectly | Obtain version-specific vendor unit contract or exact exchange per-security daily reconciliation |
| B2 status | Historical rows, issuer ST removal notice, dated join fixtures | Complete official effective event archive and first publication/revision times | SDK marks 600518.SH ST on a known non-ST day | Acquire dated official archive and quarantine/reconcile every conflict |
| B3 limits | Vendor prices/rates, exchange rules, six examples | Historical exchange official daily limits/reference, rule/exception versions | Vendor fields conflict and IPO sentinel is undefined | Acquire exchange reference archive, reconcile edge cases and certify rule table |
| B4 PIT tiers | Tier schema and negative leak guard | Verified first availability and revision snapshots for inputs | Current historical query may include later corrections or future-known labels | Freeze as-of source snapshots with timestamps and test historical replay |

## J. Next action

**Resolve only the identified Gate B evidence gaps. Do not begin Stage 1 implementation.** A Gate B PASS requires all four contracts closed with source-level proof, deterministic validation, full regression and protected Excel integrity; the current package intentionally does not claim that result.
