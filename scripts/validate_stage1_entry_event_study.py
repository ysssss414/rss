"""Read-only integrity and data-readiness check for Stage 1 event-study evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "stage1_entry_event_study"


def verify_manifest() -> int:
    manifest = json.loads((ARTIFACTS / "qualification_manifest.json").read_text(encoding="utf-8"))
    normalized = set(manifest["lf_normalized_paths"])
    for relative, expected in manifest["evidence_hashes"].items():
        data = (ROOT / relative).read_bytes()
        if relative in normalized:
            data = data.replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != expected:
            raise AssertionError(f"Event-study evidence mismatch: {relative}")
    return len(manifest["evidence_hashes"])


def validate() -> dict[str, object]:
    contract = json.loads((ARTIFACTS / "event_study_contract.json").read_text(encoding="utf-8"))
    readiness = json.loads((ARTIFACTS / "data_readiness.json").read_text(encoding="utf-8"))
    manifest = json.loads((ARTIFACTS / "qualification_manifest.json").read_text(encoding="utf-8"))
    if contract["horizons_exchange_sessions"] != [1, 3, 5, 7, 10, 20]:
        raise AssertionError("Pre-registered horizons changed")
    if (readiness["real_event_study"] != "REAL_EVENT_STUDY_BLOCKED"
            or readiness["qualified_date_range"] is not None
            or readiness["real_indicator_factor_source"]["status"] != "NOT_INDEPENDENTLY_PIT_QUALIFIED"
            or manifest["entry_edge_status"] != "REAL_DATA_BLOCKED"
            or manifest["engine_status"] != "PASS"):
        raise AssertionError("Real-data gate or result changed without qualification")
    return {"engine_status": "PASS", "entry_edge_status": "REAL_DATA_BLOCKED",
            "horizons": contract["horizons_exchange_sessions"],
            "manifest_hashes": verify_manifest()}


if __name__ == "__main__":
    print(json.dumps(validate(), sort_keys=True))
