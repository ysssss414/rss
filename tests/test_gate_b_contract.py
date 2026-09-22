from __future__ import annotations

from scripts.validate_stage1_gate_b_contract import admitted_asof, effective_status, load, validate


def test_captured_unit_arithmetic_is_diagnostic_only():
    result = validate()
    units = load("field_unit_contract.json")
    assert result["unit_ratio_checks"] == 3
    assert units["volume"]["conversion_multiplier"] is None
    assert units["amount"]["conversion_multiplier"] is None
    assert units["status"] == "OPEN"


def test_historical_st_disagreement_is_preserved():
    result = validate()
    assert result["status_conflict_detected"]
    assert result["synthetic_status_checks"] == 5
    assert load("historical_status_contract.json")["status"] == "OPEN"


def test_missing_late_and_overlapping_effective_status_rejected():
    fixture = load("validation_cases.json")["synthetic_effective_status"][0]
    records = fixture["records"]
    assert effective_status(records, "2024-02-01", "2024-01-31T15:00:00+08:00") == "UNKNOWN"
    assert effective_status(records, "2024-03-01", "2024-03-01T15:00:00+08:00") == "UNKNOWN"
    import pytest
    with pytest.raises(ValueError, match="Overlapping"):
        effective_status(records + [records[1]], "2024-02-01", "2024-02-01T15:00:00+08:00")


def test_limit_samples_are_diagnostics_and_no_limit_is_not_zero_price():
    result = validate()
    assert result["vendor_limit_checks"] == 6
    assert result["rate_price_conflict_detected"]
    assert result["synthetic_limit_checks"] == 2
    assert any(item["observation"] == "VENDOR_NO_LIMIT_SENTINEL_ONLY" for item in result["limits"])
    assert load("limit_price_contract.json")["status"] == "OPEN"


def test_future_publication_and_revision_are_not_admitted():
    leak = load("validation_cases.json")["pit_negative"]
    assert not admitted_asof(leak, leak["decision_at"])
    assert not admitted_asof({**leak, "first_published_at": "2024-07-03T09:00:00+08:00",
                              "revision_published_at": "2024-07-05T09:00:00+08:00"}, leak["decision_at"])
    assert not admitted_asof({**leak, "first_published_at": None}, leak["decision_at"])
    assert not admitted_asof({**leak, "first_published_at": "2024-07-03T09:00:00"}, leak["decision_at"])


def test_all_four_contract_families_remain_open():
    for name in ("field_unit_contract.json", "historical_status_contract.json",
                 "limit_price_contract.json", "pit_research_tier_contract.json"):
        assert load(name)["status"] == "OPEN"
