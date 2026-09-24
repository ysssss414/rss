"""Immutable, fully offline REAL_RESEARCH_SNAPSHOT_V1 freeze and loader."""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Iterable
from uuid import uuid4

import duckdb
import pandas as pd

from .d8 import D8_CONTRACT, D8_DATASET, D8_EXCLUSION_DATASET


SNAPSHOT_ID = "REAL_RESEARCH_SNAPSHOT_V1"
SNAPSHOT_SCHEMA = "real-research-snapshot/1"
SNAPSHOT_SCHEMA_VERSION = 1
RESEARCH_DATASETS = (
    "security_master", "trading_calendar", "daily_bars", "daily_status",
    "adjustment_factor", D8_DATASET,
)
AUDIT_DATASETS = (D8_EXCLUSION_DATASET,)
ALL_DATASETS = RESEARCH_DATASETS + AUDIT_DATASETS
DAY_DATASETS = {"daily_bars", "daily_status", "adjustment_factor"}
DATE_COLUMN = {**{name: "trade_date" for name in DAY_DATASETS},
               "trading_calendar": "trade_date", D8_DATASET: "effective_date",
               D8_EXCLUSION_DATASET: "effective_date"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       default=str) + "\n").encode("utf-8")


def _digest(path: Path) -> str:
    block = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            block.update(chunk)
    return block.hexdigest()


def _safe_relative(value: str) -> Path:
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise ValueError("Unsafe snapshot constituent path")
    return Path(*posix.parts)


def _dataset_paths(root: Path, dataset: str) -> list[Path]:
    directory = root / dataset
    monthly = sorted(directory.glob("year=*/month=*/part.parquet"))
    return monthly or ([directory / "part.parquet"] if (directory / "part.parquet").is_file() else [])


def _dataset_fingerprint(files: Iterable[dict[str, object]]) -> str:
    identity = [{"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
                for item in files]
    return sha256(_json_bytes(identity)).hexdigest()


def _copy_constituents(store_root: Path, target: Path) -> dict[str, list[dict[str, object]]]:
    con = duckdb.connect()
    inventory: dict[str, list[dict[str, object]]] = {}
    try:
        for dataset in ALL_DATASETS:
            paths = _dataset_paths(store_root, dataset)
            if not paths:
                raise ValueError(f"Missing snapshot dataset: {dataset}")
            inventory[dataset] = []
            for source in paths:
                relative = source.relative_to(store_root)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(destination)]).fetchone()[0]
                inventory[dataset].append({
                    "path": relative.as_posix(), "bytes": destination.stat().st_size,
                    "sha256": _digest(destination), "row_count": rows,
                })
    finally:
        con.close()
    return inventory


def freeze_snapshot(
    store_root: Path,
    snapshot_root: Path,
    *,
    cutoff: date = date(2026, 9, 23),
    snapshot_id: str = SNAPSHOT_ID,
    expected_universe: int = 5222,
    expected_calendar: int = 662,
    code_commit: str,
) -> dict[str, object]:
    """Physically copy a qualified store into an exclusive immutable identity."""
    store_root, snapshot_root = Path(store_root).resolve(), Path(snapshot_root).resolve()
    if snapshot_root.exists():
        raise FileExistsError(f"Snapshot already exists: {snapshot_root}")
    store_manifest_path = store_root / "metadata" / "manifest.json"
    store_manifest = json.loads(store_manifest_path.read_text(encoding="utf-8"))
    missing = set(RESEARCH_DATASETS) - set(store_manifest.get("datasets", ()))
    if missing:
        raise ValueError(f"Mutable store lacks D8 snapshot inputs: {sorted(missing)}")
    if (store_manifest.get("security_count") != expected_universe
            or store_manifest.get("trading_day_count") != expected_calendar
            or store_manifest.get("initial_sync_end") != cutoff.isoformat()):
        raise ValueError("Universe, calendar, or cutoff differs from the freeze contract")
    d8_quality = store_manifest.get("quality", {}).get(D8_DATASET, {})
    if (d8_quality.get("canonical_status") != "PASS"
            or d8_quality.get("d5_reconciliation", {}).get("status") != "PASS"):
        raise ValueError("D8 qualification has not passed")
    temporary = snapshot_root.with_name(f".{snapshot_root.name}.tmp-{uuid4().hex}")
    temporary.mkdir(parents=True)
    try:
        inventory = _copy_constituents(store_root, temporary)
        source_copy = temporary / "metadata" / "source_store_manifest.json"
        source_copy.parent.mkdir(parents=True, exist_ok=True)
        source_copy.write_bytes(_json_bytes(store_manifest))
        source_item = {"path": source_copy.relative_to(temporary).as_posix(),
                       "bytes": source_copy.stat().st_size, "sha256": _digest(source_copy),
                       "row_count": None}
        dataset_inventory = {}
        for dataset, files in inventory.items():
            source_coverage = store_manifest.get("coverage", {}).get(dataset, {})
            dataset_inventory[dataset] = {
                "schema_version": store_manifest.get("schema_versions", {}).get(dataset, 1),
                "row_count": sum(int(item["row_count"]) for item in files),
                "coverage": source_coverage,
                "fingerprint": _dataset_fingerprint(files),
                "files": files,
                "source": store_manifest.get("lineage", {}).get(dataset, {}),
            }
        manifest: dict[str, object] = {
            "schema": SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "snapshot_version": 1,
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "created_at": _now(),
            "cutoff_date": cutoff.isoformat(),
            "code_commit": code_commit,
            "source_store_manifest": source_item,
            "universe": {
                "count": expected_universe,
                "inclusion_rule": "AmazingData EXTRA_STOCK_A SH/SZ at cutoff; rows before listing excluded",
                "dataset": "security_master", "d1_provenance": "AmazingData 1.1.6 D1",
            },
            "calendar": {
                "count": expected_calendar, "start": "2024-01-02",
                "end": cutoff.isoformat(), "dataset": "trading_calendar",
            },
            "datasets": dataset_inventory,
            "pit_policy": {
                "factor_contract": "PIT_ADJUSTED_CLOSE_V1",
                "factor_ratio_contract": "HISTORICAL_FACTOR_RATIO_V1",
                "d8_contract": D8_CONTRACT,
                "d8_usage": "OUTCOME_ONLY_NEVER_SIGNAL",
                "d8_revision_policy": "UNRESOLVED_ROWS_EXCLUDED_AND_PATH_FAILS_CLOSED",
                "as_of_policy": "decision inputs use rows available by decision time; D8 only normalizes realized outcome",
                "future_leakage_prevention": "signal modules cannot import outcome_path or event_study",
            },
            "adjustment_policy": {
                "raw_price_dataset": "daily_bars",
                "factor_dataset": "adjustment_factor",
                "canonical_adjusted_close": "PIT_ADJUSTED_CLOSE_V1",
                "allowed_revision": "COMMON_FACTOR_RENORMALIZATION_ONLY",
                "forbidden_revision": "NON_COMMON_HISTORICAL_RATIO_CHANGE",
                "cash_dividend_income": "EXCLUDED",
            },
            "quality": {
                "status": "PASS",
                "store_quality": store_manifest.get("quality", {}),
                "d8_excluded_rows": dataset_inventory[D8_EXCLUSION_DATASET]["row_count"],
                "evidence": "dataset inventory, constituent SHA256, source store quality",
            },
            "immutability": {
                "mode": "PHYSICAL_COPY_EXCLUSIVE_CREATE",
                "validation": "EVERY_OPEN_SHA256_AND_ROW_COUNT",
                "mutable_store_updates_affect_snapshot": False,
            },
        }
        manifest_path = temporary / "metadata" / "manifest.json"
        manifest_path.write_bytes(_json_bytes(manifest))
        temporary.replace(snapshot_root)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    validate_snapshot(snapshot_root, expected_id=snapshot_id)
    return manifest


def validate_snapshot(root: Path, *, expected_id: str = SNAPSHOT_ID) -> dict[str, object]:
    root = Path(root).resolve()
    manifest_path = root / "metadata" / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get("schema") != SNAPSHOT_SCHEMA
            or manifest.get("snapshot_id") != expected_id
            or manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION):
        raise ValueError("Unsupported or unexpected research snapshot identity")
    declared = {item["path"] for dataset in manifest.get("datasets", {}).values()
                for item in dataset.get("files", [])}
    source_item = manifest.get("source_store_manifest", {})
    declared.add(source_item.get("path"))
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
              and path != manifest_path}
    if None in declared or declared != actual:
        raise ValueError("Snapshot constituent set differs from manifest")
    con = duckdb.connect()
    checked_files = 0
    try:
        for dataset_name, dataset in manifest["datasets"].items():
            rows = 0
            for item in dataset["files"]:
                path = root / _safe_relative(item["path"])
                if (path.stat().st_size != item["bytes"] or _digest(path) != item["sha256"]):
                    raise ValueError(f"Snapshot fingerprint mismatch: {item['path']}")
                observed = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
                if observed != item["row_count"]:
                    raise ValueError(f"Snapshot row count mismatch: {item['path']}")
                rows += observed
                checked_files += 1
            if rows != dataset["row_count"]:
                raise ValueError(f"Snapshot dataset row count mismatch: {dataset_name}")
        source_path = root / _safe_relative(source_item["path"])
        if source_path.stat().st_size != source_item["bytes"] or _digest(source_path) != source_item["sha256"]:
            raise ValueError("Source-store manifest fingerprint mismatch")
    finally:
        con.close()
    if set(RESEARCH_DATASETS) - set(manifest["datasets"]):
        raise ValueError("Snapshot is missing a required research dataset")
    return {
        "status": "PASS", "snapshot_id": manifest["snapshot_id"],
        "cutoff_date": manifest["cutoff_date"], "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "constituent_files_checked": checked_files + 1,
        "dataset_row_counts": {name: value["row_count"]
                               for name, value in manifest["datasets"].items()},
        "dataset_fingerprints": {name: value["fingerprint"]
                                 for name, value in manifest["datasets"].items()},
    }


class FrozenResearchSnapshot:
    """Single offline research entry point; validation precedes every open."""

    def __init__(self, root: Path, *, expected_id: str = SNAPSHOT_ID):
        self.root = Path(root).resolve()
        self.validation = validate_snapshot(self.root, expected_id=expected_id)
        self.manifest = json.loads((self.root / "metadata" / "manifest.json").read_text(encoding="utf-8"))
        self.con = duckdb.connect()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "FrozenResearchSnapshot":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @property
    def snapshot_id(self) -> str:
        return self.manifest["snapshot_id"]

    def _files(self, dataset: str) -> list[str]:
        if dataset not in RESEARCH_DATASETS:
            raise ValueError(f"Dataset is not in the canonical research layer: {dataset}")
        return [str(self.root / _safe_relative(item["path"]))
                for item in self.manifest["datasets"][dataset]["files"]]

    def _load(self, dataset: str, codes: list[str] | None = None,
              start: date | str | None = None, end: date | str | None = None) -> pd.DataFrame:
        filters, args = [], [self._files(dataset)]
        columns = {row[0] for row in self.con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [args[0]]).fetchall()}
        if codes is not None:
            if "security_id" not in columns:
                raise ValueError(f"Dataset has no security identity: {dataset}")
            filters.append("security_id IN (SELECT UNNEST(?))")
            args.append(codes)
        day = DATE_COLUMN.get(dataset)
        if start is not None:
            if day is None:
                raise ValueError(f"Dataset has no date field: {dataset}")
            filters.append(f"{day} >= ?")
            args.append(str(start))
        if end is not None:
            if day is None:
                raise ValueError(f"Dataset has no date field: {dataset}")
            filters.append(f"{day} <= ?")
            args.append(str(end))
        predicate = " WHERE " + " AND ".join(filters) if filters else ""
        order = ([day, "security_id"] if day and "security_id" in columns else
                 [day] if day else ["security_id"])
        return self.con.execute(
            f"SELECT * FROM read_parquet(?, hive_partitioning=false){predicate} ORDER BY {', '.join(order)}",
            args).df()

    def load_universe(self, codes: list[str] | None = None) -> pd.DataFrame:
        return self._load("security_master", codes)

    def load_calendar(self, start=None, end=None) -> pd.DataFrame:
        return self._load("trading_calendar", start=start, end=end)

    def load_daily_bars(self, codes=None, start=None, end=None) -> pd.DataFrame:
        return self._load("daily_bars", codes, start, end)

    def load_daily_status(self, codes=None, start=None, end=None) -> pd.DataFrame:
        return self._load("daily_status", codes, start, end)

    def load_adjustment_factor(self, codes=None, start=None, end=None) -> pd.DataFrame:
        return self._load("adjustment_factor", codes, start, end)

    def load_d8(self, codes=None, start=None, end=None) -> pd.DataFrame:
        return self._load(D8_DATASET, codes, start, end)

    def d8_path_is_qualified(self, security_id: str, start: date | str, end: date | str) -> bool:
        files = [str(self.root / _safe_relative(item["path"]))
                 for item in self.manifest["datasets"][D8_EXCLUSION_DATASET]["files"]]
        count = self.con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE security_id=? AND effective_date BETWEEN ? AND ?",
            [files, security_id, str(start), str(end)]).fetchone()[0]
        return count == 0
