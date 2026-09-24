from __future__ import annotations

import builtins
from datetime import date
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from research.d8 import (
    D8_DATASET, D8_EXCLUSION_DATASET, canonicalize_d8, normalize_single_event_factors,
    planned_factor_refresh_codes, reconcile_d8_with_d5,
)
from research.snapshot import FrozenResearchSnapshot, freeze_snapshot, validate_snapshot
from scripts.sync_d8 import expected_keys, record_ok


def test_d8_bounded_normalization_and_identity():
    frame = pd.DataFrame({"A.SH": [1.0, 1.2, 1.0], "B.SZ": [1.0, 1.0, 1.1]},
                         index=pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24"]))
    result = normalize_single_event_factors(
        frame, ["A.SH", "B.SZ"], start=date(2026, 9, 22), end=date(2026, 9, 23))
    assert result[["security_id", "effective_date"]].to_dict("records") == [
        {"security_id": "A.SH", "effective_date": date(2026, 9, 23)}]
    with pytest.raises(ValueError, match="identities"):
        normalize_single_event_factors(frame, ["B.SZ", "A.SH"],
                                       start=date(2026, 9, 22), end=date(2026, 9, 23))


def test_d8_canonicalization_excludes_unresolved_revision_and_nonimplemented():
    factors = pd.DataFrame([
        {"security_id": "A.SH", "effective_date": "2026-06-01", "single_factor": 1.1},
        {"security_id": "B.SZ", "effective_date": "2026-06-02", "single_factor": 1.2},
        {"security_id": "C.SH", "effective_date": "2026-06-03", "single_factor": 1.3},
    ])
    dividends = [
        {"MARKET_CODE": "A.SH", "DIV_PROGRESS": "3", "DATE_EX": "20260601",
         "DATE_DVD_ANN": "20260525", "IS_CHANGED": 0, "REPORT_PERIOD": "20251231"},
        {"MARKET_CODE": "B.SZ", "DIV_PROGRESS": "3", "DATE_EX": "20260602",
         "DATE_DVD_ANN": "20260525", "IS_CHANGED": 1, "REPORT_PERIOD": "20251231"},
        {"MARKET_CODE": "C.SH", "DIV_PROGRESS": "1", "DATE_EX": "20260603",
         "DATE_DVD_ANN": "20260525", "IS_CHANGED": 0, "REPORT_PERIOD": "20251231"},
    ]
    result = canonicalize_d8(factors, dividends, (), cutoff=date(2026, 9, 23))
    assert result.canonical.security_id.tolist() == ["A.SH"]
    assert set(result.exclusions.reason) == {
        "DIVIDEND_REVISION_HISTORY_UNRESOLVED", "NO_IMPLEMENTED_ACTION_RECORD"}
    assert result.quality["null_key_rows"] == result.quality["duplicate_keys"] == 0


def test_d8_d5_reconciliation_and_incremental_refresh_plan():
    d8 = pd.DataFrame([{"security_id": "A.SH", "effective_date": "2026-09-23",
                        "single_factor": 1.2}])
    d5 = pd.DataFrame([
        {"security_id": "A.SH", "trade_date": "2026-09-22", "factor": 1.0},
        {"security_id": "A.SH", "trade_date": "2026-09-23", "factor": 1.2},
    ])
    assert reconcile_d8_with_d5(d8, d5)["status"] == "PASS"
    changed = d8.assign(single_factor=1.3)
    assert reconcile_d8_with_d5(changed, d5)["status"] == "FAIL"
    boundary = pd.DataFrame([{"security_id": "B.SZ", "effective_date": "2026-09-22",
                              "single_factor": 1.05}])
    combined = pd.concat([boundary, d8], ignore_index=True)
    allowed = {("B.SZ", date(2026, 9, 22))}
    result = reconcile_d8_with_d5(combined, d5, qualified_boundary_keys=allowed)
    assert result["status"] == "PASS" and result["qualified_window_boundary_events"] == 1
    assert planned_factor_refresh_codes(
        ({"MARKET_CODE": "B.SZ"},), ({"MARKET_CODE": "A.SH"},)) == ("A.SH", "B.SZ")


def test_checkpoint_hash_and_batch_plan(tmp_path):
    cached = tmp_path / "raw.json"
    cached.write_text("raw", encoding="utf-8")
    from hashlib import sha256
    record = {"status": "COMPLETED", "cache_file": str(cached),
              "sha256": sha256(cached.read_bytes()).hexdigest()}
    assert record_ok(record)
    cached.write_text("changed", encoding="utf-8")
    assert not record_ok(record)
    planned = expected_keys(["A.SH", "B.SZ", "C.SH"], 2)
    assert len(planned) == 6 and planned[0][0] == "single_event_factor:batch_001"


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.register("frame", frame)
        con.execute("COPY frame TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        con.close()


def _fixture_store(root: Path) -> Path:
    days = pd.to_datetime(["2026-09-21", "2026-09-22"])
    _write_parquet(root / "security_master" / "part.parquet", pd.DataFrame({
        "security_id": ["A.SH", "B.SZ"], "exchange": ["SH", "SZ"],
        "board": ["MAIN", "MAIN"], "listing_date": pd.to_datetime(["2020-01-01"] * 2),
        "delisting_date": pd.to_datetime([None, None]), "security_name": ["A", "B"]}))
    _write_parquet(root / "trading_calendar" / "part.parquet",
                   pd.DataFrame({"trade_date": days, "is_trading_day": [True, True]}))
    for dataset, columns in (
        ("daily_bars", {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
                        "volume": 100, "amount": 1000.0}),
        ("daily_status", {"preclose": 10.0, "high_limited": 11.0, "low_limited": 9.0,
                          "price_high_lmt_rate": .1, "price_low_lmt_rate": .1,
                          "is_st_sec": False, "is_susp_sec": False,
                          "is_wd_sec": False, "is_xr_sec": False}),
        ("adjustment_factor", {"factor": 1.0}),
    ):
        frame = pd.DataFrame({"security_id": ["A.SH", "B.SZ"], "trade_date": days, **{
            key: [value, value] for key, value in columns.items()}})
        _write_parquet(root / dataset / "year=2026" / "month=09" / "part.parquet", frame)
    _write_parquet(root / D8_DATASET / "part.parquet", pd.DataFrame({
        "security_id": ["A.SH"], "effective_date": pd.to_datetime(["2026-09-22"]),
        "single_factor": [1.1], "event_kind": ["DIVIDEND"],
        "known_date": pd.to_datetime(["2026-09-15"]), "source_record_count": [1],
        "source_event_hash": ["a" * 64]}))
    _write_parquet(root / D8_EXCLUSION_DATASET / "part.parquet", pd.DataFrame({
        "security_id": ["B.SZ"], "effective_date": pd.to_datetime(["2026-09-22"]),
        "single_factor": [1.2], "event_kind": ["DIVIDEND"],
        "reason": ["DIVIDEND_REVISION_HISTORY_UNRESOLVED"]}))
    datasets = ["security_master", "trading_calendar", "daily_bars", "daily_status",
                "adjustment_factor", D8_DATASET, D8_EXCLUSION_DATASET]
    manifest = {
        "store_schema_version": 1, "datasets": datasets, "security_count": 2,
        "trading_day_count": 2, "initial_sync_end": "2026-09-22",
        "dataset_row_counts": {name: 2 for name in datasets},
        "schema_versions": {name: 1 for name in datasets},
        "coverage": {name: {"rows": 2} for name in datasets},
        "lineage": {name: {"source": "fixture"} for name in datasets},
        "quality": {D8_DATASET: {"canonical_status": "PASS",
                                  "d5_reconciliation": {"status": "PASS"}}},
    }
    manifest["dataset_row_counts"][D8_DATASET] = 1
    manifest["dataset_row_counts"][D8_EXCLUSION_DATASET] = 1
    (root / "metadata").mkdir(parents=True)
    (root / "metadata" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_snapshot_freeze_offline_reopen_and_mutable_isolation(tmp_path, monkeypatch):
    store = _fixture_store(tmp_path / "store")
    frozen = tmp_path / "snapshot"
    freeze_snapshot(store, frozen, cutoff=date(2026, 9, 22), expected_universe=2,
                    expected_calendar=2, code_commit="a" * 40)
    first = validate_snapshot(frozen)
    source_bar = next((store / "daily_bars").rglob("*.parquet"))
    source_bar.write_bytes(b"mutable store changed")
    second = validate_snapshot(frozen)
    assert first == second
    original_import = builtins.__import__
    def offline_import(name, *args, **kwargs):
        if name.startswith("AmazingData"):
            raise AssertionError("snapshot loader attempted vendor import")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", offline_import)
    with FrozenResearchSnapshot(frozen) as snapshot:
        assert snapshot.snapshot_id == "REAL_RESEARCH_SNAPSHOT_V1"
        assert snapshot.load_universe().security_id.tolist() == ["A.SH", "B.SZ"]
        assert snapshot.load_d8().security_id.tolist() == ["A.SH"]
        assert not snapshot.d8_path_is_qualified("B.SZ", "2026-09-21", "2026-09-22")
    with pytest.raises(FileExistsError):
        freeze_snapshot(store, frozen, cutoff=date(2026, 9, 22), expected_universe=2,
                        expected_calendar=2, code_commit="a" * 40)


@pytest.mark.parametrize("mode", ["tamper", "missing"])
def test_snapshot_tamper_or_missing_constituent_fails_closed(tmp_path, mode):
    store = _fixture_store(tmp_path / "store")
    frozen = tmp_path / "snapshot"
    freeze_snapshot(store, frozen, cutoff=date(2026, 9, 22), expected_universe=2,
                    expected_calendar=2, code_commit="a" * 40)
    target = next((frozen / "daily_bars").rglob("*.parquet"))
    if mode == "tamper":
        target.write_bytes(target.read_bytes() + b"tamper")
    else:
        target.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_snapshot(frozen)
