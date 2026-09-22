from __future__ import annotations

import hashlib

from scripts.validate_stage1_gate_b_retry1 import (
    ROOT, admitted, diagnostic_round, effective_status, load, normalize_limit, select_rule, validate,
)


def test_retry_evidence_does_not_promote_unknown_units():
    assert validate()["B1"] == "OPEN"
    units = load("unit_reconciliation.json")
    assert units["volume"]["conversion_multiplier"] is None
    assert units["amount"]["conversion_multiplier"] is None


def test_official_effective_event_beats_conflicting_vendor_status():
    events = load("historical_status_reconciliation.json")["validated_events"]
    assert effective_status(events, "2024-07-04", "2024-07-04T15:00:00+08:00") == "NORMAL"
    assert effective_status(events, "2024-07-04", "2024-07-02T15:00:00+08:00") == "UNKNOWN"


def test_status_conflict_and_missing_version_fail_closed():
    event = load("historical_status_reconciliation.json")["validated_events"][0]
    cutoff = "2024-07-04T15:00:00+08:00"
    assert effective_status([event, event], "2024-07-04", cutoff) == "UNKNOWN"
    assert effective_status([{**event, "revision_state": "UNKNOWN"}], "2024-07-04", cutoff) == "UNKNOWN"


def test_rule_version_boundary_and_unknown_regime():
    rules = load("limit_price_rule_registry.json")["rules"]
    assert [select_rule(rules, "SSE", "MAIN", "RISK_WARNING", day)["limit_rate"]
            for day in ("2026-07-03", "2026-07-06", "2026-07-07")] == ["0.05", "0.10", "0.10"]
    assert select_rule(rules, "SZSE", "MAIN", "RISK_WARNING", "2026-07-06") is None


def test_no_limit_requires_official_confirmation_and_null_prices():
    assert normalize_limit(official_no_limit=None, official_upper=None, official_lower=None,
                           vendor_upper="0", vendor_rate="999") is None
    normalized = normalize_limit(official_no_limit=True, official_upper=None, official_lower=None)
    assert normalized["is_limit_applicable"] is False
    assert normalized["limit_up_price"] is None and normalized["limit_rate"] is None
    assert normalize_limit(official_no_limit=True, official_upper="10.00", official_lower=None) is None


def test_official_prices_take_precedence_over_vendor_rate_and_bad_prices_rejected():
    row = normalize_limit(official_no_limit=False, official_upper="2.02", official_lower="1.82",
                          vendor_upper="0", vendor_rate="0.10")
    assert row["limit_up_price"] == "2.02"
    assert normalize_limit(official_no_limit=False, official_upper="0", official_lower="0") is None


def test_rounding_edge_is_diagnostic_not_certified_exchange_price():
    assert diagnostic_round("1.05", "0.10") == "1.16"
    assert load("limit_price_validation.json")["rounding_certified"] is False


def test_real_official_source_positive_future_and_unverified_cases():
    fixtures = load("pit_admission_registry.json")["real_source_fixtures"]
    assert [admitted(f["source"], f["decision_at"], f["target_date"]) for f in fixtures] == [
        f["expected_admission"] for f in fixtures]
    assert [f["expected_admission"] for f in fixtures] == [True, False, False]


def test_unverified_core_source_and_naive_timestamp_rejected():
    source = load("pit_admission_registry.json")["sources"]["amazingdata_kline"]
    assert not admitted(source, "2024-07-04T15:00:00+08:00", "2024-07-04")
    official = load("pit_admission_registry.json")["real_source_fixtures"][0]["source"]
    assert not admitted(official, "2026-07-06T15:00:00", "2026-07-06")


def test_retry_manifest_literal_evidence_hashes_and_baseline():
    manifest = load("qualification_manifest.json")
    assert manifest["baseline_main_sha"] == manifest["round1_merge_sha"]
    assert manifest["gate_verdict"] == "GATE_B_STOP"
    for path, expected in manifest["evidence_hashes"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
