"""Public-only fail-closed manifest check for the stopped real-data study."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "stage1_real_data_event_study"


def load(name: str) -> dict[str, object]:
    return json.loads((ARTIFACTS / name).read_bytes())


def validate() -> dict[str, object]:
    manifest = load("qualification_manifest.json")
    expected = manifest["public_evidence_sha256"]
    actual_names = {path.name for path in ARTIFACTS.glob("*.json")}
    if actual_names != set(expected) | {"qualification_manifest.json"}:
        raise ValueError("Public evidence file set changed")
    for name, digest in expected.items():
        if sha256((ARTIFACTS / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Public evidence hash changed: {name}")
    sources = load("source_qualification.json")
    coverage = load("real_data_coverage.json")
    factor = load("factor_qualification.json")
    calendar = load("calendar_qualification.json")
    smoke = load("smoke_validation.json")
    study = load("event_study_summary.json")
    if [row["id"] for row in sources["sources"]] != [f"D{index}" for index in range(1, 9)]:
        raise ValueError("D1-D8 source list changed")
    if (calendar["validated_window"]["missing"] != 0
            or calendar["validated_window"]["extra"] != 0
            or calendar["validated_window"]["exchange_symmetric_difference"] != 0):
        raise ValueError("Calendar not exactly reconciled")
    if (factor["signal_generation_admitted"]
            or factor["qualification_status"] != "FACTOR_SOURCE_PARTIALLY_QUALIFIED"
            or coverage["real_study_range"] is not None
            or smoke["status"] != "NOT_RUN_DATA_CHAIN_BLOCKED"
            or study["status"] != "NOT_RUN_DATA_CHAIN_BLOCKED"
            or study["signal_n"] is not None
            or manifest["status"] != "STAGE1_REAL_DATA_EVENT_STUDY_STOP"
            or manifest["data_chain_status"] != "BLOCKED"
            or manifest["entry_edge_status"] != "REAL_DATA_BLOCKED"):
        raise ValueError("STOP conclusion contradicts source evidence")
    return {"status": manifest["status"], "data_chain_status": manifest["data_chain_status"],
            "engine_status": manifest["engine_status"], "entry_edge_status": manifest["entry_edge_status"],
            "hashed_public_evidence_files": len(expected), "source_gates": len(sources["sources"])}


if __name__ == "__main__":
    print(json.dumps(validate(), sort_keys=True))
