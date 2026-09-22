"""Synthetic contract fixtures for strategy-independent Stage 1 foundation."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from research.foundation import (
    HistoricalUniverse, NextSessionV1, RunContext, TradingCalendar, execution_policy,
)
from scripts.validate_stage1_gate_c_contract import load
from scripts.verify_stage1_foundation_manifest import verify_manifest


FIXTURES = load("universe_validation_cases.json")
CASES = FIXTURES["cases"]
CALENDAR = TradingCalendar(
    tuple(date(2019, 1, 1) + timedelta(days=n) for n in range(2922)
          if (date(2019, 1, 1) + timedelta(days=n)).weekday() < 5),
    "synthetic-weekday/1",
)


def context(case=CASES[0]) -> RunContext:
    return RunContext(case["data_snapshot_id"], "gate-b-retry3-daily-contract/1",
                      "1", "NEXT_SESSION_V1", "1", CALENDAR.version)


def observations(case, *, extra_before_relisting=0):
    day = date.fromisoformat(case["trade_date"])
    active = [date.fromisoformat(event["effective_date"]) for event in case["lifecycle_events"]
              if event["kind"] in {"LISTED", "RELISTED"} and event["effective_date"] <= case["trade_date"]]
    reset = max(active, default=day)
    admitted = [session for session in CALENDAR.sessions if reset <= session <= day]
    bars = admitted[-case["valid_history_sessions"]:] if case["valid_history_sessions"] else []
    if case["trading_status"] != "TRADING" or not case["bar_present"]:
        bars = [bar for bar in bars if bar != day]
    if extra_before_relisting:
        bars = [session for session in CALENDAR.sessions if session < reset][-extra_before_relisting:] + bars
    return [{"security_id": case["security_id"], "trade_date": bar.isoformat(),
             "data_snapshot_id": case["data_snapshot_id"],
             "available_at": f"{bar.isoformat()}T15:00:00+08:00",
             "trading_status": "TRADING", "valid_bar": True, "source_hash": "a" * 64}
            for bar in bars]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_gate_c_universe_boundaries(case):
    result = HistoricalUniverse(context(case), CALENDAR,
                                minimum_history_required=FIXTURES["minimum_history_required"]).evaluate(
                                    case, observations(case))
    assert result.eligible is case["expected"]["eligible"]
    assert (result.exclusion_reasons[0] if result.exclusion_reasons else None) == case["expected"]["exclusion_reason"]
    assert not result.eligible or not result.exclusion_reasons
    assert result.data_snapshot_id == case["data_snapshot_id"]


def test_history_is_counted_since_relisting_and_can_have_multiple_exclusions():
    case = deepcopy(next(case for case in CASES if case["case_id"] == "relisted_with_insufficient_new_history"))
    result = HistoricalUniverse(context(case), CALENDAR, minimum_history_required=7).evaluate(
        case, observations(case, extra_before_relisting=150))
    assert result.history_observation_count == 1
    assert "INSUFFICIENT_HISTORY" in result.exclusion_reasons
    eligible = deepcopy(CASES[0])
    eligible["security_type"] = "ETF"
    eligible["daily_constraint"]["validation_status"] = "INVALID"
    reasons = HistoricalUniverse(context(eligible), CALENDAR, minimum_history_required=0).evaluate(
        eligible, observations(eligible)).exclusion_reasons
    assert reasons == ("UNSUPPORTED_SECURITY_TYPE", "INVALID_DAILY_CONSTRAINT")


def test_zero_dependency_requirement_does_not_impose_legacy_150_bar_filter():
    case = deepcopy(next(case for case in CASES if case["case_id"] == "post_ipo_insufficient_history"))
    result = HistoricalUniverse(context(case), CALENDAR, minimum_history_required=0).evaluate(case, observations(case))
    assert result.eligible and result.history_observation_count == 6


def test_duplicate_history_fails_closed():
    case = CASES[0]
    bars = observations(case)
    result = HistoricalUniverse(context(case), CALENDAR, minimum_history_required=0).evaluate(case, bars + bars[:1])
    assert not result.eligible and "DATA_CONFLICT" in result.exclusion_reasons


def test_late_and_suspended_history_bars_do_not_count():
    case = CASES[0]
    bars = observations(case)
    bars[0]["available_at"] = "2024-10-10T15:00:00+08:00"
    bars[1]["trading_status"] = "SUSPENDED"
    result = HistoricalUniverse(context(case), CALENDAR, minimum_history_required=149).evaluate(case, bars)
    assert result.history_observation_count == 148
    assert not result.history_sufficient and "INSUFFICIENT_HISTORY" in result.exclusion_reasons


@pytest.mark.parametrize("security_type", ["ETF", "FUND", "BOND", "CONVERTIBLE_BOND", "B_SHARE",
                                          "PREFERRED_STOCK", "REIT", "OTHER", "UNKNOWN"])
def test_non_common_stock_type_never_enters_universe(security_type):
    case = deepcopy(CASES[0])
    case["security_type"] = security_type
    result = HistoricalUniverse(context(case), CALENDAR, minimum_history_required=0).evaluate(case, observations(case))
    assert not result.eligible and "UNSUPPORTED_SECURITY_TYPE" in result.exclusion_reasons


EXEC_CALENDAR = TradingCalendar((date(2024, 10, 9), date(2024, 10, 10),
                                 date(2024, 10, 11), date(2024, 10, 14),
                                 date(2024, 10, 16)), "synthetic-execution/1")


def execution_context():
    return RunContext("frozen-snapshot", "gate-b-retry3-daily-contract/1", "1",
                      "NEXT_SESSION_V1", "1", EXEC_CALENDAR.version)


def next_day(day: str, opening="10.50", upper="11.00"):
    return {"security_id": "600000.SH", "trade_date": day, "trading_status": "TRADING",
            "raw_open": opening, "daily_constraint": {
                "security_id": "600000.SH", "trade_date": day,
                "available_at": f"{day}T09:00:00+08:00", "validation_status": "VALID",
                "trading_allowed": True, "suspended": False, "limit_applicable": True,
                "limit_up_price": upper, "price_tick": "0.01",
                "source_provenance": "synthetic-qualified-gate-b-constraint"}}


def execute(day="2024-10-09", payload=None, calendar=EXEC_CALENDAR):
    if payload is None:
        following = calendar.next_after(date.fromisoformat(day))
        payload = next_day(following.isoformat()) if following else None
    return execution_policy("NEXT_SESSION_V1").execute(
        context=RunContext("frozen-snapshot", "gate-b-retry3-daily-contract/1", "1",
                           "NEXT_SESSION_V1", "1", calendar.version), calendar=calendar,
        security_id="600000.SH", signal_date=date.fromisoformat(day),
        signal_at=datetime.fromisoformat(f"{day}T15:10:00+08:00"),
        latest_input_at=datetime.fromisoformat(f"{day}T15:00:00+08:00"),
        side="BUY", next_day=payload)


@pytest.mark.parametrize("day,following", [("2024-10-09", "2024-10-10"),
                                            ("2024-10-11", "2024-10-14"),
                                            ("2024-10-14", "2024-10-16")])
def test_next_exchange_session_handles_normal_weekend_and_holiday(day, following):
    result = execute(day)
    assert result.status == "MODELLED_FILL"
    assert result.execution_date.isoformat() == following
    assert result.order_at.hour == 9 and result.order_at.minute == 15
    assert result.execution_at.hour == 9 and result.execution_at.minute == 25
    assert result.execution_date > date.fromisoformat(day)
    assert result.execution_price == Decimal("10.50") and result.slippage_bps == 0


@pytest.mark.parametrize("opening,upper,status", [
    ("10.50", "11.00", "MODELLED_FILL"),
    ("11.00", "11.00", "NO_FILL"),
    ("11.01", "11.00", "EXECUTION_UNRESOLVED"),
    (None, "11.00", "EXECUTION_UNRESOLVED"),
    ("10.505", "11.00", "EXECUTION_UNRESOLVED"),
])
def test_fill_and_tick_boundaries(opening, upper, status):
    result = execute(payload=next_day("2024-10-10", opening, upper))
    assert result.status == status
    assert (result.execution_price is not None) is (status == "MODELLED_FILL")


@pytest.mark.parametrize("change", ["suspended", "missing_constraint", "invalid_constraint", "no_limit", "late_constraint"])
def test_missing_or_invalid_next_day_context_is_unresolved(change):
    payload = next_day("2024-10-10")
    if change == "suspended":
        payload["trading_status"] = "SUSPENDED"
    elif change == "missing_constraint":
        payload.pop("daily_constraint")
    elif change == "invalid_constraint":
        payload["daily_constraint"]["validation_status"] = "INVALID"
    elif change == "no_limit":
        payload["daily_constraint"]["limit_applicable"] = False
    else:
        payload["daily_constraint"]["available_at"] = "2024-10-10T09:16:00+08:00"
    result = execute(payload=payload)
    assert result.status == "EXECUTION_UNRESOLVED" and result.execution_price is None


def test_no_following_session_and_unqualified_policies():
    result = execute(day="2024-10-16")
    assert result.status == "EXECUTION_UNRESOLVED" and result.execution_date is None
    for name in ("CLOSING_AUCTION_V1", "PRE_CLOSE_SNAPSHOT_V1", "UNKNOWN"):
        with pytest.raises(ValueError, match="NOT_QUALIFIED"):
            execution_policy(name)


def test_signal_on_non_session_is_unresolved():
    result = execute(day="2024-10-12", payload=next_day("2024-10-14"))
    assert result.status == "EXECUTION_UNRESOLVED" and result.reason == "SIGNAL_NOT_IN_CALENDAR"


def test_future_input_cannot_trigger_a_prior_order():
    with pytest.raises(ValueError, match="Invalid causal"):
        NextSessionV1().execute(
            context=execution_context(), calendar=EXEC_CALENDAR, security_id="600000.SH",
            signal_date=date(2024, 10, 9), signal_at=datetime.fromisoformat("2024-10-09T15:10:00+08:00"),
            latest_input_at=datetime.fromisoformat("2024-10-09T15:11:00+08:00"),
            side="BUY", next_day=next_day("2024-10-10"))


def test_foundation_manifest_integrity():
    assert verify_manifest() == 7
