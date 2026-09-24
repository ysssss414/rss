# Stage 1 D8 and `REAL_RESEARCH_SNAPSHOT_V1`

## Status and scope

`STAGE1_D8_AND_RESEARCH_SNAPSHOT = COMPLETED` for the fixed 2024-01-01 through
2026-09-23 research baseline. This stage qualifies and materializes D8, freezes
the research inputs, and proves the offline data path. It does **not** run a
strategy, Entry Event Study, Exit/PnL, parameter optimization, or any edge
assessment.

The machine-readable evidence is under
[`artifacts/stage1_d8_research_snapshot/`](../artifacts/stage1_d8_research_snapshot/).
Raw vendor responses, checkpoints, the mutable market store, and the physical
snapshot stay in ignored local directories.

## D8 definition and observed API behavior

D8 is `ENTRY_COMPARABLE_FORWARD_PATH_V1`: a sparse corporate-action factor
series used only to map realized post-entry high/low/close values back to the
entry-date price scale. It does not credit cash dividends as income and is never
available to universe, indicator, observation-pool, or entry-signal code.

The AmazingData 1.1.6 qualification used three endpoints:

| Endpoint | Observed input/range behavior | Returned grain and time fields |
| --- | --- | --- |
| `BaseData.get_adj_factor` | Batched security list; `local_path` and `is_local`; no date range or pagination parameter | Trading-date × security matrix. A non-event is `1`; a non-one value is the single-event price-scale factor. |
| `InfoData.get_dividend` | Batched security list; begin/end filter announcement dates; no exposed pagination | Security/report-period distribution record. `DATE_DVD_ANN` is the known date and `DATE_EX` the effective date. |
| `InfoData.get_right_issue` | Batched security list; begin/end filter announcement dates; no exposed pagination | Rights-event record. `EXECUTE_DATE` is the known date and `EX_DIVIDEND_DATE` the effective date. |

Two controlled calls over the same five securities returned equal content.
Factor dates were unique, ordered, and extended through 2026-09-24; the
canonicalizer nevertheless enforces the frozen cutoff 2026-09-23. The complete
observations and endpoint contract are in
[`d8_qualification_receipt.json`](../artifacts/stage1_d8_research_snapshot/d8_qualification_receipt.json).

## D8 canonical and PIT contract

The canonical `outcome_adjustment` key is `(security_id, effective_date)`.
Its schema is:

| Field | Type/nullability | Meaning |
| --- | --- | --- |
| `security_id` | string, non-null | Exact SH/SZ market code returned for the requested universe. |
| `effective_date` | date, non-null | Corporate action ex/effective date. |
| `single_factor` | positive finite number, non-null | Single-event price-scale factor. |
| `event_kind` | string, non-null | `DIVIDEND`, `RIGHTS`, or their joined event type. |
| `known_date` | date, non-null in canonical | Latest qualifying implementation-known date, strictly before `effective_date`. |
| `source_record_count` | integer, non-null | Number of action records supporting the event. |
| `source_event_hash` | SHA-256 string, non-null | Stable hash of the supporting raw action records. |

The raw factor matrix is bounded to 2024-01-01 through 2026-09-23, checked for
ordered unique dates and finite positive values, and reduced to non-one events.
An event enters canonical data only when a matching implemented action exists,
its implementation was known before effectiveness, and its revision/result
state is resolved. Changed dividends, unresolved rights results, late or unknown
implementation dates, unmatched factor events, and implemented actions lacking
a factor event are retained in `outcome_adjustment_exclusions`, never guessed or
silently repaired. A research outcome path that intersects one of those audit
rows fails closed through `d8_path_is_qualified`.

D8 is outcome-only, so a future effective action may normalize a subsequently
realized price without becoming a signal input. This is the required PIT
separation: decision inputs remain truncated to what was available at the
decision time; D8 acts only after entry on an observed outcome path.

The D8 event factors also reconcile to the frozen D5 cumulative-factor ratios.
All 13,052 D5 changes match. The sole additional D8 event is 002937.SZ on
2024-01-02, the first D5 date, where no preceding in-window D5 row can form a
ratio; the event has a qualifying 2023-12-25 implementation announcement and is
the one admitted window-boundary case. There are no unexplained or mismatched
events.

## D8 storage, acquisition, and refresh

D8 follows the existing architecture: AmazingData acquisition → ignored raw
cache → canonical transform → Parquet → DuckDB/offline loader → committed audit
receipts. Because the canonical grain contains only 12,457 events, it is one
ordered ZSTD Parquet file rather than monthly tiny files. The 718 excluded rows
are stored separately in one audit-only Parquet file.

The real acquisition divided 5,222 securities into 53 batches of at most 100
and issued one request per endpoint per batch: 159 successful requests, zero
failed attempts, and zero retries. Each completed response is written and
SHA-256 recorded before the next request. A controlled stop and two native-host
exits were resumed from the checkpoint without reacquiring completed records.
The final same-parameter run skipped all 159 records and did not create a vendor
session; the second materialization was also a no-op with unchanged hashes.

Normal maintenance is a bounded announcement-date refresh of dividend and
rights records. Only securities returned by that bounded action refresh are
eligible for a full-history `get_adj_factor` refresh, because that endpoint has
no date range. A daily 5,222-security full-history factor pull is explicitly not
the maintenance policy. Any resulting revision is re-canonicalized under the
same exclusion rules and must create a new snapshot identity if research inputs
change.

The quality gates require complete unique keys, ordered dates, positive finite
factors, canonical uniqueness, raw/canonical/exclusion reconciliation, D5/D8
factor-ratio agreement, and an explicit reason for every exclusion. Actual
coverage and quality counts are in
[`d8_dataset_coverage.csv`](../artifacts/stage1_d8_research_snapshot/d8_dataset_coverage.csv)
and
[`d8_dataset_quality_summary.json`](../artifacts/stage1_d8_research_snapshot/d8_dataset_quality_summary.json).

## Frozen research snapshot

`REAL_RESEARCH_SNAPSHOT_V1` is a physically copied, exclusive-create research
input boundary. It is **not** the current mutable market store. Later store
updates cannot change V1. Reusing the same identity is rejected if its directory
already exists.

The snapshot freezes:

| Dataset | Rows | Dataset fingerprint |
| --- | ---: | --- |
| D1 `security_master` | 5,222 | `9f8d05f1aac027d850581b32b79604d8b5201f5f06245b66cec307d1241f5347` |
| `trading_calendar` | 662 | `79b601366ade53456a86747c8eb72b9302a0b6e9bfbfb2ff391817fe0c3ba037` |
| D3 `daily_bars` | 3,375,103 | `b592907e2d9edbad106edd63b3b62ea9983a8cc54f9c406539d04b240e724a98` |
| D4/D6 `daily_status` | 3,379,281 | `10a62607ddbd6b448f70762ff6ffcc95e62eea8ff1bf779644f1ca34dda0c6d4` |
| D5 `adjustment_factor` | 3,380,290 | `c343305415f88c40e12e4cda7c6fbb4521409883035eba7a2174a34565359998` |
| D8 `outcome_adjustment` | 12,457 | `4e1811ae70548605265f28dfad9a63b899b74fe92a5bc9d41043853895e68cbf` |
| D8 exclusion audit | 718 | `b43a05e1fd1b7757882ca8bb4a5549a25c6af23f6064ba7222651c555c4fecf8` |

The cutoff is 2026-09-23. The calendar has 662 sessions from 2024-01-02, and
the universe is the 5,222-name AmazingData `EXTRA_STOCK_A` SH/SZ set at the
cutoff, with pre-listing rows excluded. The committed manifest copy records all
103 Parquet constituents, their byte sizes, row counts, SHA-256 hashes, schema
versions, coverage, and provenance, plus the frozen source-store manifest.
Its own SHA-256 is
`a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`.

The manifest binds `PIT_ADJUSTED_CLOSE_V1` and
`HISTORICAL_FACTOR_RATIO_V1`. A common multiplication of a historical
cumulative-factor series is permitted because it preserves ratios; a non-common
historical ratio change is forbidden. Raw D3 prices remain separate from D5.
Cash-dividend income is excluded, and unresolved D8 rows remain outside the
canonical research layer.

## Validation and offline use

Every `FrozenResearchSnapshot` open first validates the identity, exact
constituent set, every file SHA-256, every file row count, every dataset total,
and the required research datasets. A missing, added, or modified file therefore
fails closed before any data is returned. Unit tests exercise both tampering and
missing-file failure. The actual V1 validation checked 104 constituents (103
Parquet files plus the source-store manifest) and deterministic reopen returned
identical identity, counts, fingerprints, and key coverage.

Validate the local frozen copy without a vendor session:

```powershell
python scripts/validate_research_snapshot.py `
  --snapshot-root data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1
```

Use the single offline research entry point explicitly:

```python
from pathlib import Path
from research.snapshot import FrozenResearchSnapshot

root = Path("data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1")
with FrozenResearchSnapshot(root) as snapshot:
    assert snapshot.snapshot_id == "REAL_RESEARCH_SNAPSHOT_V1"
    universe = snapshot.load_universe()
    calendar = snapshot.load_calendar()
    bars = snapshot.load_daily_bars(["600519.SH"], "2024-01-01", "2026-09-23")
    status = snapshot.load_daily_status(["600519.SH"], "2024-01-01", "2026-09-23")
    factors = snapshot.load_adjustment_factor(["600519.SH"], "2024-01-01", "2026-09-23")
    d8 = snapshot.load_d8(["600519.SH"], "2024-01-01", "2026-09-23")
    assert snapshot.d8_path_is_qualified("600519.SH", "2024-01-01", "2026-09-23")
```

The loader imports neither the AmazingData adapter nor the SDK and contains no
network fallback. The real data-path smoke loaded universe, calendar, D3,
D4/D6, D5, and D8, validated schemas and keys, and opened no network or vendor
session. It intentionally computed no strategy metric.
