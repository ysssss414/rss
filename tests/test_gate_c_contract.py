"""Synthetic, offline checks for Gate C contracts; no strategy or SDK calls."""

from copy import deepcopy

import pytest

from scripts.validate_stage1_gate_c_contract import (
    classify_daily_open_fill,
    classify_security_day,
    load,
    validate,
    validate_next_session_timeline,
    verify_manifest,
)


FIXTURES = load("universe_validation_cases.json")
CASES = FIXTURES["cases"]
MINIMUM = FIXTURES["minimum_history_required"]


def classify(case):
    return classify_security_day(case, minimum_history_required=MINIMUM)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_security_day_boundary(case):
    assert classify(case) == case["expected"]


def test_future_delisting_cannot_rewrite_past_universe():
    case = deepcopy(next(c for c in CASES if c["case_id"] == "future_delisting_does_not_rewrite_past"))
    assert classify(case)["eligible"]
    case["lifecycle_events"][-1]["available_at"] = "2024-10-08T09:00:00+08:00"
    assert classify(case)["eligible"]  # Effective date remains T+1.


def test_board_and_vendor_st_label_cannot_override_validated_ten_percent_constraint():
    case = deepcopy(next(c for c in CASES if c["case_id"] == "sse_main_10_eligible"))
    case["board"] = "CHINEXT"
    case["IS_ST_SEC"] = 1
    assert classify(case)["eligible"]
    case["daily_constraint"]["limit_up_rate"] = "0.20"
    assert classify(case)["exclusion_reason"] == "NON_10_PERCENT_REGIME"


def test_unknown_type_and_incomplete_lifecycle_fail_closed():
    case = deepcopy(next(c for c in CASES if c["case_id"] == "sse_main_10_eligible"))
    case["security_type"] = "UNKNOWN"
    assert classify(case)["exclusion_reason"] == "UNSUPPORTED_SECURITY_TYPE"
    case["security_type"] = "A_SHARE_COMMON_STOCK"
    case["lifecycle_coverage_verified"] = False
    assert classify(case)["exclusion_reason"] == "UNKNOWN_LISTING_LIFECYCLE"


def test_required_lookback_is_a_frozen_contract_input_not_an_indicator_calculation():
    case = deepcopy(next(c for c in CASES if c["case_id"] == "sse_main_10_eligible"))
    case["valid_history_sessions"] = MINIMUM - 1
    assert classify(case)["exclusion_reason"] == "INSUFFICIENT_HISTORY"
    case["valid_history_sessions"] = MINIMUM
    assert classify(case)["eligible"]
    assert classify_security_day(case, minimum_history_required=0)["eligible"]


def test_next_session_timeline_admits_after_eod_signal_and_opening_auction():
    validate_next_session_timeline(
        trade_date="2024-10-09", next_session_date="2024-10-10",
        verified_next_session_date="2024-10-10",
        signal_at="2024-10-09T15:10:00+08:00",
        input_available_at="2024-10-09T15:00:00+08:00",
        order_at="2024-10-10T09:15:00+08:00",
        execution_at="2024-10-10T09:25:00+08:00",
    )


@pytest.mark.parametrize("change", ["same_close", "input_too_late", "naive_time", "wrong_auction", "wrong_calendar"])
def test_next_session_timeline_rejects_lookahead_or_wrong_order(change):
    context = dict(
        trade_date="2024-10-09", next_session_date="2024-10-10",
        verified_next_session_date="2024-10-10",
        signal_at="2024-10-09T15:10:00+08:00",
        input_available_at="2024-10-09T15:00:00+08:00",
        order_at="2024-10-10T09:15:00+08:00",
        execution_at="2024-10-10T09:25:00+08:00",
    )
    if change == "same_close":
        context.update(order_at="2024-10-09T15:00:00+08:00", execution_at="2024-10-09T15:00:00+08:00")
    elif change == "input_too_late":
        context["input_available_at"] = "2024-10-09T15:11:00+08:00"
    elif change == "naive_time":
        context["signal_at"] = "2024-10-09T15:10:00"
    elif change == "wrong_auction":
        context["execution_at"] = "2024-10-10T15:00:00+08:00"
    else:
        context["verified_next_session_date"] = "2024-10-11"
    with pytest.raises(ValueError):
        validate_next_session_timeline(**context)


def test_daily_open_below_limit_is_only_a_modelled_fill():
    assert classify_daily_open_fill(trading_allowed=True, constraint_valid=True,
                                    limit_applicable=True, opening_price="10.50",
                                    limit_up_price="11.00") == {"status": "MODELLED_FILL", "price": "10.50"}


@pytest.mark.parametrize("trading,valid,applicable,opening,upper,status", [
    (True, True, True, "11.00", "11.00", "NO_FILL"),
    (False, True, True, "10.50", "11.00", "EXECUTION_UNRESOLVED"),
    (True, False, True, "10.50", "11.00", "EXECUTION_UNRESOLVED"),
    (True, True, False, "10.50", "11.00", "EXECUTION_UNRESOLVED"),
    (True, True, True, None, "11.00", "EXECUTION_UNRESOLVED"),
])
def test_limit_up_and_invalid_open_never_imply_actual_fill(trading, valid, applicable, opening, upper, status):
    result = classify_daily_open_fill(trading_allowed=trading, constraint_valid=valid,
                                      limit_applicable=applicable, opening_price=opening,
                                      limit_up_price=upper)
    assert result == {"status": status, "price": None}


def test_unqualified_intraday_interfaces_do_not_become_qualified_policies():
    probe = load("execution_capability_probe.json")
    policy = load("execution_policy_contract.json")
    assert probe["capabilities"]["one_minute_bars"]["api_surface"] == "AVAILABLE"
    assert probe["capabilities"]["one_minute_bars"]["research_usable"] == "UNKNOWN"
    assert all(policy["policies"][name]["qualification"] == "NOT_QUALIFIED"
               for name in ("CLOSING_AUCTION_V1", "PRE_CLOSE_SNAPSHOT_V1"))


def test_malformed_pit_time_and_asymmetric_regime_fail_closed():
    case = deepcopy(next(c for c in CASES if c["case_id"] == "sse_main_10_eligible"))
    case["trading_status_available_at"] = "not-a-timestamp"
    assert classify(case)["exclusion_reason"] == "MISSING_PIT_EVIDENCE"
    case["trading_status_available_at"] = "2024-10-09T09:00:00+08:00"
    case["daily_constraint"]["limit_down_rate"] = "0.20"
    assert classify(case)["exclusion_reason"] == "NON_10_PERCENT_REGIME"
    case["daily_constraint"]["limit_down_rate"] = "NaN"
    assert classify(case)["exclusion_reason"] == "INVALID_DAILY_CONSTRAINT"


def test_manifest_and_gate_verdict():
    assert verify_manifest() >= 6
    assert validate()["gate_c"] == "GATE_C_CONTRACT_PASS"
