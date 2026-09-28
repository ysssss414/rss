"""Frozen V3 audit receipts must remain internally consistent and fail closed."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/stage1_close_limit_up_v3"
PROVISIONAL = ROOT / "artifacts/stage1_targeted_regime"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_rounding_sources_and_reference_firewall():
    qualification = _read(OUT / "contract_qualification.json")
    assert qualification["mechanical_contract_status"] == "QUALIFIED_FOR_STRATEGY_RESEARCH"
    assert qualification["formal_strategy_runtime_status"] == "BLOCKED_TARGETED_REGIME_EVIDENCE_INSUFFICIENT"
    assert qualification["active_runtime_switched"] is False
    rounding = _read(OUT / "rounding_contract.json")
    assert rounding["version"] == "PRICE_LIMIT_TICK_ROUNDING_V1"
    assert rounding["tick_cny"] == "0.01"
    for source in rounding["official_sources"]:
        if "frozen_local_copy" in source:
            assert _sha(ROOT / source["frozen_local_copy"]) == source["frozen_local_sha256"]
    reference = _read(OUT / "reference_day_contract.json")
    assert reference["version"] == "REFERENCE_PRICE_DAY_CLASSIFICATION_V1"
    assert reference["d8_policy"].startswith("OUTCOME_ONLY_NEVER_SIGNAL")


def test_gate_b_reconciles_and_600518_is_true():
    receipt = _read(OUT / "gate_b_reconciliation.json")
    assert receipt["status"] == "GATE_B_RECONCILED"
    assert len(receipt["cases"]) == receipt["case_count"] == 13
    assert not receipt["ordinary_case_conflicts"]
    case = next(row for row in receipt["cases"]
                if row["case"] == "sse_risk_removal_2024_07_04")
    assert (case["reference_day_class"], case["v1_result"],
            case["v2_result"], case["v3_result"]) == ("ORDINARY", True, "FALSE", "TRUE")
    assert case["v3_calculated_limit_up_price"] == "2.12"


def test_targeted_receipt_has_no_formal_upgrades_or_d8_signal_dependency():
    lead = _read(PROVISIONAL / "provisional_candidate_inventory_v3.json")
    audit = _read(OUT / "targeted_qualification_receipt.json")
    assert lead["candidate_count"] == audit["provisional_candidate_count"] == 5540
    assert lead["dependency_security_dates"] == audit["dependency_security_dates"] == 13958
    assert lead["quality"]["v3_ordinary_hit_not_detected"] == 0
    assert len(lead["v3_daily_classification_semantic_sha256"]) == 64
    assert audit["v3_daily_classification_semantic_sha256"] == lead["v3_daily_classification_semantic_sha256"]
    assert audit["rounding_contract_sha256"] == _sha(OUT / "rounding_contract.json")
    assert audit["reference_day_contract_sha256"] == _sha(OUT / "reference_day_contract.json")
    assert audit["v3_implementation_sha256"] == hashlib.sha256(
        (ROOT / "research/close_limit_up_v3.py").read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    assert audit["status"] == "BLOCKED_TARGETED_REGIME_EVIDENCE_INSUFFICIENT"
    assert audit["formal_observations"] == 0
    assert audit["reference_and_tier_counts"]["REGIME_UNRESOLVED"] == 13958
    assert not audit["official_case_documents_intersecting"]
    assert sum(audit["candidate_disposition_counts"].values()) == 5540
    assert all("ORDINARY" not in overlap
               for overlap in audit["posthoc_outcome_only_d8_overlap_by_reference_class"].values())
    assert _sha(PROVISIONAL / "provisional_observation_candidate_v3.parquet") == lead["candidate_parquet_sha256"]
    assert _sha(PROVISIONAL / "targeted_regime_dependency_inventory_v3.csv") == lead["dependency_inventory_sha256"]
    assert _sha(OUT / "v3_dependency_classification.csv") == audit["dependency_csv_sha256"]
    assert _sha(OUT / "v3_candidate_disposition.csv") == audit["candidate_csv_sha256"]
