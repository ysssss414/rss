"""Materialize the complete cached sync and emit small, source-backed receipts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.market_store import DATASETS, materialize


PRIVATE = ROOT / ".local_research_data" / "market_store"
STORE = ROOT / "data" / "market_store"
ARTIFACTS = ROOT / "artifacts" / "stage1_local_market_store"
CALENDAR = ROOT / ".local_research_data" / "calendar_20260923_retry.json"


def save(name: str, value: dict) -> None:
    (ARTIFACTS / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                  encoding="utf-8")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    state = json.loads((PRIVATE / "checkpoint.json").read_text(encoding="utf-8"))
    counts = {phase: sum(row["status"] == "COMPLETED" for row in state["records"].values()
                         if row["dataset"] == phase)
              for phase in ("D1_basic", "D3_bars", "D4_D6_status", "D5_factor")}
    expected = {"D1_basic": 66, "D3_bars": 5222, "D4_D6_status": 5222, "D5_factor": 53}
    if counts != expected:
        raise ValueError(f"Incomplete acquisition checkpoint: {counts}")
    manifest = materialize(STORE, PRIVATE, CALENDAR)
    stats = manifest["coverage"]
    with (ARTIFACTS / "dataset_coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("dataset", "rows", "min_trade_date",
            "max_trade_date", "unique_securities", "unique_trade_dates", "disk_bytes"),
            lineterminator="\n")
        writer.writeheader()
        for dataset in DATASETS:
            writer.writerow({"dataset": dataset, **stats[dataset]})
    segments = state["segments"]
    times = {phase: round(sum(row["elapsed_seconds"] for row in segments if row["phase"] == phase), 3)
             for phase in counts}
    total = sum(times.values())
    estimate = 2.015 * 3600
    save("timing_comparison.json", {"unit": "seconds", "component_active_seconds": times,
        "total_active_seconds": round(total, 3), "previous_eta_seconds": estimate,
        "previous_eta_hours": 2.015, "buffered_eta_hours": 2.419,
        "actual_hours": round(total / 3600, 4),
        "eta_error_pct": round((total - estimate) / estimate * 100, 2),
        "active_time_is_lower_bound": any(not row.get("finished_at") for row in segments),
        "definition": "sum of checkpointed phase acquisition segments; excludes engineering pauses, materialization, and uncheckpointed in-flight time of interrupted requests"})
    reused = {phase: {"securities": 0, "rows": 0, "remote_calls_avoided": 0}
              for phase in ("D3_bars", "D4_D6_status", "D5_factor")}
    reused_codes = set()
    for record in state["records"].values():
        if record["dataset"] not in reused or not record.get("reused") or record["status"] != "COMPLETED":
            continue
        entry = reused[record["dataset"]]
        entry["securities"] += len(record["security_ids"])
        entry["rows"] += record["rows"]
        entry["remote_calls_avoided"] += 1
        reused_codes.update(record["security_ids"])
    save("cache_reuse_summary.json", {"by_dataset": reused,
        "unique_securities": len(reused_codes),
        "security_endpoint_instances": sum(row["securities"] for row in reused.values()),
        "reused_rows": sum(row["rows"] for row in reused.values()),
        "remote_calls_avoided": sum(row["remote_calls_avoided"] for row in reused.values()),
        "separate_status_validation_probe_calls": 5,
        "D1_note": "100 prior D1 rows were valid; display encoding was misread and all D1 batches were refetched. Zero D1 remote calls avoided."})
    save("dataset_quality_summary.json", {"quality": manifest["quality"],
        "classification": "raw anomalous rows retained in ignored staging; null-key rows excluded from canonical without default values"})
    save("store_manifest_summary.json", {key: manifest[key] for key in (
        "store_schema_version", "created_at", "updated_at", "source", "sdk_version",
        "initial_sync_start", "initial_sync_end", "security_count", "trading_day_count",
        "dataset_row_counts", "latest_trade_date", "schema_versions")})
    resumed = [row for row in segments if row["phase"] == "D1_basic"]
    status_resume = [row for row in segments if row["phase"] == "D4_D6_status"]
    factor_resume = [row for row in segments if row["phase"] == "D5_factor"]
    save("resume_receipt.json", {"D1_basic": {"segments": resumed,
        "checkpoint_records_completed": counts["D1_basic"],
        "bounded_interruption_verified": (len(resumed) >= 2 and resumed[0]["new_completed"] == 2
            and resumed[1]["skipped_completed"] >= 2)},
        "D4_D6_status": {"segments": status_resume,
            "checkpoint_records_completed": counts["D4_D6_status"],
            "interrupted_then_batch_resumed": (len(status_resume) >= 2
                and not status_resume[0].get("finished_at")
                and status_resume[-1]["skipped_completed"] >= 769)},
        "D5_factor": {"segments": factor_resume,
            "checkpoint_records_completed": counts["D5_factor"],
            "interrupted_then_resumed": (len(factor_resume) >= 2
                and not factor_resume[0].get("finished_at")
                and factor_resume[-1]["skipped_completed"] >= 41)}})
    save("initial_sync_summary.json", {"status": "COMPLETED", "source": "AmazingData",
        "date_start": "2024-01-01", "date_end": "2026-09-23",
        "security_count": manifest["security_count"], "trading_days": manifest["trading_day_count"],
        "checkpoint_completed": counts, "dataset_row_counts": manifest["dataset_row_counts"],
        "canonical_disk_bytes": sum(row["disk_bytes"] for row in stats.values()),
        "total_local_store_bytes": sum(path.stat().st_size for path in STORE.rglob("*") if path.is_file()),
        "D8_status": "DEFERRED"})
    print(json.dumps({"status": "COMPLETED", "rows": manifest["dataset_row_counts"],
        "disk_bytes": sum(row["disk_bytes"] for row in stats.values()),
        "active_seconds": round(total, 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
