"""Trigger-only audit is source-bound, one-row-per-T, and never a smoke run."""

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/stage1_observation_contract_v2"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_versioned_contract_does_not_rewrite_v1():
    current = json.loads((OUT / "observation_pool_contract_v2.json").read_text(encoding="utf-8"))
    legacy = json.loads((ROOT / "artifacts/stage1_observation_entry/observation_pool_contract.json")
                        .read_text(encoding="utf-8"))
    assert legacy["version"] == "OBSERVATION_POOL_V1"
    assert current["version"] == "OBSERVATION_POOL_CONTRACT_V2"
    assert current["regime_qualification_scope"] == "TRIGGER_DATE_ONLY"
    assert current["lookback_limit_up_predicate"] == "CLOSE_LIMIT_UP_10PCT_V3"
    assert current["lookback_non_limit_days_require_regime_qualification"] is False
    assert current["entry_date_regime_requalification"] is False
    assert json.loads((OUT / "episode_start_eligibility_v1.json").read_text(encoding="utf-8"))[
        "version"] == "EPISODE_START_ELIGIBILITY_V1"


def test_frozen_trigger_audit_grain_and_fail_closed_receipt():
    receipt = json.loads((OUT / "trigger_qualification_receipt.json").read_text(encoding="utf-8"))
    inventory = OUT / "observation_v2_trigger_regime_dependency_inventory.csv"
    with inventory.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert receipt["status"] == "BLOCKED_TRIGGER_DATE_REGIME_EVIDENCE_INSUFFICIENT"
    assert receipt["old_all_five_day_dependency_count"] == 13958
    assert receipt["new_trigger_only_dependency_count"] == len(rows) == 5540
    assert receipt["trigger_qualification_calls"] == 5540
    assert receipt["lookback_regime_qualification_calls"] == 0
    assert len({(row["security_id"], row["trigger_date"]) for row in rows}) == 5540
    assert receipt["counts"]["TRIGGER_REGIME_UNRESOLVED"] == 5540
    assert receipt["formal_observation_count"] is None
    assert receipt["formal_observation_materialized"] is False
    assert receipt["formal_lifecycle_and_entries_run"] is False
    assert receipt["official_case_documents_intersecting_triggers"] == []
    assert receipt["v3_self_qualified_count"] == 0
    assert receipt["direct_rule_qualified_count"] == 0
    assert receipt["targeted_official_status_qualified_count"] == 0
    assert _sha(inventory) == receipt["inventory_sha256"]
    assert _sha(OUT / "blocked_case_audit.json") == receipt["blocked_case_audit_sha256"]
    assert _sha(OUT / "observation_pool_contract_v2.json") == receipt["observation_contract_sha256"]
    assert _sha(OUT / "episode_start_eligibility_v1.json") == receipt["episode_contract_sha256"]
    assert all(row["trigger_regime_status"] == "UNRESOLVED" for row in rows)
    assert all(row["observation_v2_status"] != "QUALIFIED" for row in rows)
    cases = json.loads((OUT / "blocked_case_audit.json").read_text(encoding="utf-8"))
    assert cases["status"] == "DIAGNOSTIC_NOT_FORMAL_EVENT_AUDIT"
    assert {"POSSIBLE_4_OF_5", "POSSIBLE_5_OF_5", "TRIGGER_NON_LIMIT",
            "TRIGGER_SPECIAL_REFERENCE", "RSI_WARMUP_PENDING"} <= set(cases["cases"])
    assert all(max(item["five_observation_dates"]) == item["trigger_date"]
               for item in cases["cases"].values())


def test_signal_audit_never_reads_outcome_only_d8():
    source = (ROOT / "scripts/audit_observation_contract_v2_triggers.py").read_text(encoding="utf-8")
    assert "outcome_adjustment" not in source
    assert "research.d8" not in source
