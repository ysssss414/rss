"""Fail-closed validation of the D4 acquisition-feasibility STOP artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_d4_authority_acquisition"


def read(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def validate() -> dict:
    registry = read("acquisition_registry.json")
    summary = read("acquisition_feasibility_summary.json")
    requests = read("source_request_matrix.json")
    reconciliation = read("reconciliation_summary.json")
    receipt = read("qualification_receipt.json")
    previous = json.loads((ROOT / "artifacts/stage1_d4_authority_closure/qualification_receipt.json").read_text(encoding="utf-8"))
    dates = registry["target_dates"]
    if dates != requests["target_dates"] or len(dates) != 4 or dates[1:3] != ["2026-07-03", "2026-07-06"]:
        raise ValueError("boundary target dates differ")
    if registry["candidate_window"] != requests["candidate_window"] or registry["candidate_window"] != ["2024-01-01", "2026-09-23"]:
        raise ValueError("candidate window differs")
    sources = registry["sources"]
    if len({s["source_id"] for s in sources}) != len(sources):
        raise ValueError("duplicate source ID")
    if {s["source_id"] for s in sources} != {"SSE_CPX_0201", "SSE_CPX_0202", "SSEINFO_HIST_BASIC", "SZSE_CASH_AUCTION", "SZSE_SECURITIES", "SZSE_RULE_2023_ORIGINAL"}:
        raise ValueError("missing acquisition source")
    raw_count = 0
    for source in sources:
        if source["sample_acquired"]:
            if not source["raw_present_locally"] or not source["sample_dates"] or not source["raw_sha256"]:
                raise ValueError("unfrozen acquired sample")
            for raw in source["raw_sha256"]:
                path = ROOT / raw["repository_relative_path"]
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != raw["sha256"]:
                    raise ValueError("raw sample hash mismatch")
            raw_count += len(source["raw_sha256"])
        elif source["raw_present_locally"] or source["raw_committed"] or source["sample_dates"] or source["raw_sha256"] or source["parser_available"]:
            raise ValueError("unacquired source claims raw evidence")
        if source["candidate_window_coverage"] != "UNKNOWN" or source["retrieval_reproducible"]:
            raise ValueError("unsupported coverage or repeatability claim")
    if raw_count != 0 or summary["raw_historical_preopen_files_acquired"] != 0 or summary["raw_historical_preopen_rows_acquired"] != 0:
        raise ValueError("STOP requires zero raw evidence")
    if any(r["access_status"] == "ACQUIRED" for r in requests["requests"]):
        raise ValueError("request matrix falsely claims acquisition")
    if reconciliation["reconciliation_executed"] or reconciliation["raw_input_count"] or reconciliation["resolver_comparison_count"] or any(reconciliation["counts"].values()):
        raise ValueError("reconciliation falsely claims execution")
    if len(reconciliation["pending_exchange_days"]) != 8 or summary["boundary_exchange_days_requested"] != 8:
        raise ValueError("missing exchange-day target")
    if receipt["gate_result"] != "STAGE1_D4_AUTHORITY_ACQUISITION_STOP" or any(receipt["gates"].values()) or receipt["raw_sha256_verified_count"] != 0:
        raise ValueError("unsupported PASS claim")
    if previous["gate_result"] != receipt["prior_d4_closure_result_unchanged"] or previous["counts"] != {"VALID": 1, "INVALID": 0, "MISSING": 15, "CONFLICT": 0}:
        raise ValueError("prior D4 closure changed")
    return {"validation": "PASS", "gate_result": receipt["gate_result"], "raw_files": raw_count, "pending_exchange_days": 8}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
