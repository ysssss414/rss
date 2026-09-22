from __future__ import annotations

import hashlib
from copy import deepcopy
from decimal import Decimal

import pytest

import scripts.validate_stage1_gate_b_retry3 as c2


def cases():
    return {case["case"]: case for case in c2.load("historical_validation_cases.json")["cases"]}


def rules():
    return c2.load("exchange_constraint_semantics.json")["rules"]


def test_all_representative_cases_reconstruct_from_public_sources_and_hash_receipts():
    results = [c2.validate_case(case, rules()) for case in cases().values()]
    assert len(results) == 13
    assert {r["limit_applicable"] for r in results} == {True, False}
    assert all(case["public_payload"] is False and "observed_vendor" not in case
               and "vendor_capture" not in case for case in cases().values())


def test_frozen_source_registry_hashes():
    sources = c2.load("source_registry.json")["frozen_bytes"]
    assert len(sources) >= 15
    for source in sources:
        assert hashlib.sha256((c2.ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    private = c2.load("source_registry.json")["private_sources"]
    assert len(private) == 3
    assert all(item["source_type"] == "PRIVATE_LOCAL_EVIDENCE" and
               item["public_payload"] is False and "path" not in item for item in private)


def test_sse_limit_type_and_final_preopen_rule_documented():
    data = c2.load("exchange_constraint_semantics.json")["sse_interface"]
    assert data["cpxx0201_limit_type"]["N"].startswith("daily limit control")
    assert "last applicable" in data["cpxx0202_precedence"]
    assert data["historical_daily_rows_obtained"] is False


def test_sse_rounding_half_up_and_minimum_tick():
    assert c2.rate_limits("1.93", "0.10", "0.01") == (Decimal("2.12"), Decimal("1.74"))
    assert c2.rate_limits("0.02", "0.05", "0.01") == (Decimal("0.03"), Decimal("0.01"))
    with pytest.raises(ValueError):
        c2.rate_limits("0.01", "0.05", "0.01")


def test_600518_removal_uses_official_normal_regime_not_vendor_st_label():
    case = cases()["sse_risk_removal_2024_07_04"]
    result = c2.validate_case(case, rules())
    assert (result["reference_price"], result["limit_up_price"], result["limit_down_price"]) == ("1.93", "2.12", "1.74")
    assert result["vendor_status_label_admission"] == "UNTRUSTED"
    assert result["vendor_absolute_admission"] == "QUALIFIED"
    assert result["close_limit_up"] is True


def test_sse_normal_to_risk_warning_transition_has_effective_event_and_two_rates():
    before, after = cases()["sse_normal_2026_04_29"], cases()["sse_risk_intro_2026_05_06"]
    assert after["event_sources"]
    a, b = c2.validate_case(before, rules()), c2.validate_case(after, rules())
    assert a["rule_id"] != b["rule_id"]
    assert (a["limit_up_price"], b["limit_up_price"]) == ("1.94", "1.93")
    assert before["vendor_comparison"]["status_label_matches_regime"]
    assert after["vendor_comparison"]["status_label_matches_regime"]


def test_2026_rule_version_boundary_keeps_st_label_but_changes_limit_rate():
    before, after = cases()["sse_risk_before_rule_change"], cases()["sse_risk_after_rule_change"]
    assert c2.select_rule(rules(), "SSE", "MAIN", "RISK_WARNING", "2026-07-05")["limit_rate"] == "0.05"
    assert c2.select_rule(rules(), "SSE", "MAIN", "RISK_WARNING", "2026-07-06")["limit_rate"] == "0.10"
    a, b = c2.validate_case(before, rules()), c2.validate_case(after, rules())
    assert a["vendor_status_label_admission"] == b["vendor_status_label_admission"] == "CONSISTENT_ONLY"
    assert a["vendor_rate_admission"] == "QUALIFIED"
    assert b["vendor_rate_admission"] == "UNTRUSTED"
    assert b["vendor_absolute_admission"] == "QUALIFIED"


def test_unknown_or_overlapping_historical_rule_fails_closed():
    with pytest.raises(ValueError):
        c2.select_rule(rules(), "SSE", "MAIN", "RISK_WARNING", "2022-01-01")
    duplicate = rules() + [rules()[0]]
    with pytest.raises(ValueError):
        c2.select_rule(duplicate, "SSE", "MAIN", "NORMAL", "2024-07-04")


def test_sse_and_szse_no_limit_sentinels_normalize_to_null():
    for name in ("sse_ipo_first_day", "szse_ipo_first_day"):
        result = c2.validate_case(cases()[name], rules())
        assert result["limit_applicable"] is False
        assert result["limit_up_price"] is result["limit_down_price"] is None
        assert result["vendor_absolute_admission"] == "NORMALIZE_TO_NULL"


def test_ex_dividend_uses_adjusted_reference_not_raw_close():
    case = cases()["sse_ex_dividend"]
    origin = case["reference_origin"]
    assert Decimal(origin["raw_prior_close"]) - Decimal(origin["cash_dividend_per_share"]) == Decimal("1490.624")
    assert c2.official_reference(case) == Decimal("1490.62")
    assert c2.validate_case(case, rules())["limit_up_price"] == "1639.68"
    assert c2.rate_limits(origin["raw_prior_close"], "0.10", "0.01")[0] != Decimal("1639.68")


def test_szse_rate_mode_ignores_absolute_fields_even_if_absurd():
    row = {"HasPriceLimit": "Y", "ReferPriceType": "1", "LimitType": "1",
           "LimitUpRate": "0.10", "LimitDownRate": "0.10", "PriceTick": "0.01",
           "LimitUpAbsolute": "9999", "LimitDownAbsolute": "0"}
    assert c2.szse_cashauctionparams(row, "12.88") == (Decimal("14.17"), Decimal("11.59"))


def test_szse_absolute_mode_interprets_fields_as_limit_prices():
    row = {"HasPriceLimit": "Y", "ReferPriceType": "1", "LimitType": "2",
           "PriceTick": "0.01", "LimitUpAbsolute": "14.17", "LimitDownAbsolute": "11.59"}
    assert c2.szse_cashauctionparams(row, "12.88") == (Decimal("14.17"), Decimal("11.59"))
    with pytest.raises(ValueError):
        c2.szse_cashauctionparams({**row, "LimitDownAbsolute": "14.18"}, "12.88")


def test_szse_no_limit_and_unknown_mode_fail_closed():
    assert c2.szse_cashauctionparams({"HasPriceLimit": "N"}, "2.30") == (None, None)
    with pytest.raises(ValueError):
        c2.szse_cashauctionparams({"HasPriceLimit": "Y", "ReferPriceType": "1",
                                   "LimitType": "9", "PriceTick": "0.01"}, "12.88")


def test_szse_main_chinext_and_ipo_day_six_from_official_qss():
    for name, reference, upper in (("szse_main_10", "12.88", "14.17"),
                                   ("szse_chinext_20", "205.51", "246.61"),
                                   ("szse_post_ipo_day_six", "7.44", "8.18")):
        result = c2.validate_case(cases()[name], rules())
        assert (result["reference_price"], result["limit_up_price"]) == (reference, upper)
        assert result["vendor_absolute_admission"] == "CORROBORATING_ONLY"


def test_missing_reference_or_vendor_conflict_fails_closed():
    case = deepcopy(cases()["sse_risk_removal_2024_07_04"])
    case["reference_price"] = "1.92"
    with pytest.raises(ValueError, match="reference mismatch"):
        c2.validate_case(case, rules())
    case["reference_price"] = "1.93"
    case["vendor_comparison"]["absolute_matches_reconstruction"] = False
    with pytest.raises(ValueError, match="vendor absolute"):
        c2.validate_case(case, rules())


def test_invalid_sentinel_fails_closed():
    case = deepcopy(cases()["szse_ipo_first_day"])
    case["vendor_comparison"]["no_limit_sentinel_normalized"] = False
    with pytest.raises(ValueError, match="sentinel"):
        c2.validate_case(case, rules())


def test_synthetic_private_row_precedence_does_not_require_real_capture():
    case = {"regime": "NORMAL", "suspended": False}
    rule = {"limit_rate": "0.10"}
    synthetic = {"PRECLOSE": "10.00", "HIGH_LIMITED": "11.00", "LOW_LIMITED": "9.00",
                 "PRICE_HIGH_LMT_RATE": "0.05", "PRICE_LOW_LMT_RATE": "0.05",
                 "IS_ST_SEC": "1", "IS_SUSP_SEC": "0"}
    comparison = c2.private_comparison(case, rule, Decimal("10.00"),
                                       Decimal("11.00"), Decimal("9.00"), synthetic)
    assert comparison["absolute_matches_reconstruction"] is True
    assert comparison["rate_matches_rule"] is False
    assert comparison["status_label_matches_regime"] is False


def test_structured_invalid_reason_excludes_security_day():
    case = deepcopy(cases()["sse_risk_removal_2024_07_04"])
    case["reference_price"] = "1.92"
    result = c2.qualify_security_day(case, rules())
    assert (result["validation_status"], result["causal_admitted"],
            result["reason"]["code"]) == ("INVALID", False, "REFERENCE_MISMATCH")
    valid = c2.qualify_security_day(cases()["sse_risk_removal_2024_07_04"], rules())
    assert valid["validation_status"] == "VALID" and valid["causal_admitted"]
    suspended = c2.qualify_security_day(cases()["sse_suspension_2026_04_30"], rules())
    assert not suspended["causal_admitted"] and suspended["reason"]["code"] == "SUSPENDED"


def test_suspended_near_limit_and_exact_close_limit_tick_equality():
    assert c2.validate_case(cases()["sse_suspension_2026_04_30"], rules())["close_limit_up"] is False
    assert c2.validate_case(cases()["sse_risk_after_rule_change"], rules())["close_limit_up"] is False
    assert c2.validate_case(cases()["sse_risk_removal_2024_07_04"], rules())["close_limit_up"] is True
    assert c2.close_limit_up("2.12", Decimal("2.12"), "0.01", True)
    with pytest.raises(ValueError):
        c2.close_limit_up("2.1201", Decimal("2.12"), "0.01", True)


def test_manifest_and_frozen_gate_results():
    assert all(c2.verify_manifest().values())
    result = c2.validate()
    assert result["C1"] == result["C2"] == result["C3"] == "CLOSED"
    assert result["A1"] == "UNTRUSTED"
    assert result["gate_b"] == "GATE_B_CORE_DATA_CONTRACT_PASS"
