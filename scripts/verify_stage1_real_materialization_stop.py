"""Replay the public STOP receipt without private market rows or credentials."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.real_materialization import max_full_horizon_signal_day


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def verify() -> dict[str, object]:
    base = "artifacts/stage1_real_data_materialization/"
    manifest = load(base + "qualification_manifest.json")
    baseline = load(base + "baseline_receipt.json")
    registry = load(base + "source_registry.json")
    quality = load(base + "real_data_quality_summary.json")
    receipt = load(base + "qualified_range.json")
    for relative, expected in manifest["source_hashes"].items():
        if sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Source receipt changed: {relative}")
    factor = load("artifacts/stage1_factor_pit/qualification_manifest.json")
    revisions = load("artifacts/stage1_factor_pit/revision_semantics.json")
    if (factor["FACTOR_SOURCE_STATUS"] != "QUALIFIED"
            or factor["unresolved_revision_dates_fail_closed"] != 7
            or len(revisions["other_vendor_changed_implemented_dates"]) != 7
            or any(row["status"] != "UNRESOLVED_FOR_FUTURE_MATERIALIZATION"
                   for row in revisions["other_vendor_changed_implemented_dates"])):
        raise ValueError("D5 qualification or seven fail-closed revisions changed")
    calendar = load("artifacts/stage1_real_data_event_study/official_calendar_2024_2026.json")
    calendar_receipt = load("artifacts/stage1_real_data_event_study/calendar_qualification.json")
    closed = set()
    for first, last in calendar["closed_intervals"]:
        cursor = date.fromisoformat(first)
        while cursor <= date.fromisoformat(last):
            closed.add(cursor)
            cursor += timedelta(days=1)
    start, end = date.fromisoformat(receipt["data_start"]), date.fromisoformat(receipt["data_end"])
    sessions = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in closed:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    signal = max_full_horizon_signal_day(sessions, end)
    index = sessions.index(signal)
    research_start = date.fromisoformat(receipt["candidate_research_start"])
    research_index = sessions.index(research_start)
    if (len(sessions) != 662 or receipt["calendar_session_count"] != 662
            or calendar_receipt["validated_window"]["missing"] != 0
            or calendar_receipt["validated_window"]["extra"] != 0
            or signal.isoformat() != receipt["max_full_h20_signal_date"]
            or sessions[index + 1].isoformat() != receipt["boundary_execution_d0"]
            or sessions[index + 20].isoformat() != receipt["boundary_h20_last_session"]
            or research_index != receipt["candidate_start_prior_validated_sessions"]
            or sessions[research_index + 119].isoformat() != receipt["candidate_120_session_smoke_end"]
            or sessions[research_index + 239].isoformat() != receipt["candidate_240_session_smoke_end"]):
        raise ValueError("Frozen calendar boundary does not replay")
    if (manifest["status"] != "STAGE1_REAL_DATA_MATERIALIZATION_STOP"
            or baseline["status"] != "STAGE1_REAL_DATA_MATERIALIZATION_STOP"
            or manifest["real_research_snapshot_id"] is not None
            or manifest["real_smoke_status"] != "NOT_RUN"
            or manifest["historical_universe_status"] != "NOT_RUN"
            or manifest["full_event_study_run"]
            or quality["candidate_security_days"] is not None
            or any(value is not None for value in quality["coverage_percent"].values())
            or any(value is not None for value in quality["counts"].values())
            or registry["sources"]["D4"]["status"] != "SOURCE_AUTHORITY_GAP"
            or receipt["qualified_research_start"] is not None):
        raise ValueError("STOP receipt promotes unmeasured or unqualified data")
    return {"status": manifest["status"], "calendar_sessions": len(sessions),
            "max_full_h20_signal_date": signal.isoformat(), "unresolved_changed_events": 7,
            "source_hashes_verified": len(manifest["source_hashes"])}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
