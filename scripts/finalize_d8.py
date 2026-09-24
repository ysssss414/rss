"""Materialize qualified D8 outcome adjustments into the existing market store."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.d8 import (
    D8_CANONICALIZER_VERSION, D8_DATASET, D8_EXCLUSION_DATASET, D8_SCHEMA_VERSION, canonicalize_d8,
    normalize_single_event_factors, reconcile_d8_with_d5,
)
from research.market_store import _write_json
from scripts.sync_d8 import CUTOFF, ENDPOINTS, PRIVATE, STORE, expected_keys, load_universe, record_ok


START = date(2024, 1, 1)
END = date(2026, 9, 23)


def _fingerprint(state: dict[str, object]) -> str:
    records = [{"key": key, "sha256": value["sha256"]}
               for key, value in sorted(state["records"].items())]
    payload = {"schema": state["schema"], "cutoff_date": state["cutoff_date"],
               "batch_size": state["batch_size"], "records": records}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _completed_records(state: dict[str, object], codes: list[str]) -> list[dict[str, object]]:
    expected = expected_keys(codes, int(state["batch_size"]))
    if set(state["records"]) != {key for key, _, _ in expected}:
        raise ValueError("D8 checkpoint record set is incomplete or unexpected")
    records = []
    for key, group, endpoint in expected:
        record = state["records"][key]
        if (record.get("endpoint") != endpoint or record.get("security_ids") != group
                or not record_ok(record)):
            raise ValueError(f"D8 acquisition record is incomplete: {key}")
        records.append(record)
    return records


def _read_action(record: dict[str, object]) -> list[dict[str, object]]:
    payload = json.loads(Path(record["cache_file"]).read_text(encoding="utf-8"))
    if not isinstance(payload.get("rows"), list):
        raise ValueError("D8 action cache schema invalid")
    return payload["rows"]


def _write_parquet(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame, path: Path,
                   order: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    if temporary.exists():
        temporary.unlink()
    con.register("d8_output", frame)
    con.execute(f"COPY (SELECT * FROM d8_output ORDER BY {order}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(temporary)])
    if con.execute("SELECT count(*) FROM read_parquet(?)", [str(temporary)]).fetchone()[0] != len(frame):
        temporary.unlink()
        raise ValueError("D8 Parquet readback row count differs")
    temporary.replace(path)
    con.unregister("d8_output")


def finalize(acquisition_root: Path = PRIVATE, store_root: Path = STORE) -> dict[str, object]:
    state = json.loads((acquisition_root / "checkpoint.json").read_text(encoding="utf-8"))
    if state.get("cutoff_date") != CUTOFF or state.get("schema") != "stage1-d8-acquisition/1":
        raise ValueError("Unsupported D8 acquisition checkpoint")
    codes = load_universe(store_root)
    records = _completed_records(state, codes)
    acquisition_fingerprint = _fingerprint(state)
    manifest_path = store_root / "metadata" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = manifest.get("lineage", {}).get(D8_DATASET, {})
    canonical_path = store_root / D8_DATASET / "part.parquet"
    exclusion_path = store_root / D8_EXCLUSION_DATASET / "part.parquet"
    if (existing.get("acquisition_fingerprint") == acquisition_fingerprint
            and existing.get("canonicalizer_version") == D8_CANONICALIZER_VERSION
            and canonical_path.is_file() and exclusion_path.is_file()
            and _file_hash(canonical_path) == existing.get("canonical_sha256")
            and _file_hash(exclusion_path) == existing.get("exclusion_sha256")):
        return {"status": "NO_OP", "remote_calls": 0,
                "canonical_rows": manifest["dataset_row_counts"][D8_DATASET],
                "acquisition_fingerprint": acquisition_fingerprint}
    factors, dividends, rights = [], [], []
    raw_factor_cells = 0
    raw_factor_dates = set()
    for record in records:
        if record["endpoint"] == "single_event_factor":
            frame = pd.read_hdf(record["cache_file"], "single_event_factor")
            raw_factor_cells += int(frame.size)
            raw_factor_dates.update(pd.to_datetime(frame.index, errors="raise").date)
            factors.append(normalize_single_event_factors(
                frame, record["security_ids"], start=START, end=END))
        elif record["endpoint"] == "dividend":
            dividends.extend(_read_action(record))
        else:
            rights.extend(_read_action(record))
    sparse = pd.concat(factors, ignore_index=True)
    if (sparse.duplicated(["security_id", "effective_date"]).any()
            or set(sparse.security_id) - set(codes)):
        raise ValueError("D8 sparse factor identities are invalid")
    normalized = canonicalize_d8(sparse, dividends, rights, start=START, cutoff=END)
    con = duckdb.connect()
    try:
        d5_paths = sorted((store_root / "adjustment_factor").glob("year=*/month=*/part.parquet"))
        d5 = con.execute("SELECT security_id, trade_date, factor FROM read_parquet(?)",
                         [[str(path) for path in d5_paths]]).df()
        boundary_keys = {(row.security_id, row.effective_date)
                         for row in normalized.canonical.itertuples()}
        reconciliation = reconcile_d8_with_d5(
            sparse, d5, qualified_boundary_keys=boundary_keys)
        if reconciliation["status"] != "PASS":
            raise ValueError(f"D8 and D5 event factors disagree: {reconciliation}")
        _write_parquet(con, normalized.canonical, canonical_path, "effective_date, security_id")
        _write_parquet(con, normalized.exclusions, exclusion_path, "effective_date, security_id, reason")
    finally:
        con.close()
    quality = {**normalized.quality, "d5_reconciliation": reconciliation,
               "raw_factor_cells": raw_factor_cells,
               "raw_factor_trading_dates": len(raw_factor_dates),
               "raw_dividend_rows": len(dividends), "raw_rights_rows": len(rights),
               "cutoff_enforced": CUTOFF}
    action_without_factor = normalized.quality["exclusion_reasons"].get(
        "IMPLEMENTED_ACTION_WITHOUT_FACTOR_EVENT", 0)
    quality["raw_to_canonical_reconciled"] = (
        normalized.quality["canonical_rows"] + normalized.quality["excluded_rows"]
        - action_without_factor == normalized.quality["raw_factor_events"])
    if not quality["raw_to_canonical_reconciled"]:
        raise ValueError("D8 raw/canonical/exclusion accounting does not reconcile")
    row_counts = {D8_DATASET: len(normalized.canonical),
                  D8_EXCLUSION_DATASET: len(normalized.exclusions)}
    coverage = {
        D8_DATASET: {"rows": len(normalized.canonical),
                     "unique_securities": int(normalized.canonical.security_id.nunique()),
                     "unique_effective_dates": int(normalized.canonical.effective_date.nunique()),
                     "min_effective_date": (str(normalized.canonical.effective_date.min())
                                            if len(normalized.canonical) else None),
                     "max_effective_date": (str(normalized.canonical.effective_date.max())
                                            if len(normalized.canonical) else None),
                     "disk_bytes": canonical_path.stat().st_size},
        D8_EXCLUSION_DATASET: {"rows": len(normalized.exclusions),
                              "unique_securities": int(normalized.exclusions.security_id.nunique()),
                              "disk_bytes": exclusion_path.stat().st_size},
    }
    retrieved = max(record["finished_at"] for record in records)
    for dataset in (D8_DATASET, D8_EXCLUSION_DATASET):
        if dataset not in manifest["datasets"]:
            manifest["datasets"].append(dataset)
        manifest["dataset_row_counts"][dataset] = row_counts[dataset]
        manifest["schema_versions"][dataset] = D8_SCHEMA_VERSION
        manifest["coverage"][dataset] = coverage[dataset]
    manifest["quality"][D8_DATASET] = quality
    manifest["quality"][D8_EXCLUSION_DATASET] = {
        "rows": len(normalized.exclusions), "status": "AUDIT_ONLY_NOT_RESEARCH_CANONICAL"}
    manifest["latest_trade_date"][D8_DATASET] = coverage[D8_DATASET]["max_effective_date"]
    manifest["latest_trade_date"][D8_EXCLUSION_DATASET] = CUTOFF
    manifest["lineage"][D8_DATASET] = {
        "source": "AmazingData 1.1.6 get_adj_factor/get_dividend/get_right_issue",
        "retrieved_at": retrieved, "date_start": START.isoformat(), "date_end": CUTOFF,
        "row_count": len(normalized.canonical), "schema_version": D8_SCHEMA_VERSION,
        "canonicalizer_version": D8_CANONICALIZER_VERSION,
        "acquisition_fingerprint": acquisition_fingerprint,
        "canonical_sha256": _file_hash(canonical_path),
        "exclusion_sha256": _file_hash(exclusion_path),
    }
    manifest["lineage"][D8_EXCLUSION_DATASET] = {
        "source": "D8 fail-closed exclusion audit", "retrieved_at": retrieved,
        "row_count": len(normalized.exclusions), "schema_version": D8_SCHEMA_VERSION}
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest_path, manifest)
    return {"status": "COMPLETED", "remote_calls": 0,
            "canonical_rows": len(normalized.canonical),
            "excluded_rows": len(normalized.exclusions),
            "quality": quality, "coverage": coverage,
            "acquisition_fingerprint": acquisition_fingerprint}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acquisition-root", type=Path, default=PRIVATE)
    parser.add_argument("--store-root", type=Path, default=STORE)
    args = parser.parse_args()
    print(json.dumps(finalize(args.acquisition_root, args.store_root), ensure_ascii=False,
                     sort_keys=True, default=str))


if __name__ == "__main__":
    main()
