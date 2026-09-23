"""Synthetic qualification of Entry forward event-study semantics."""

from datetime import date, datetime, time
from decimal import Decimal as D

import pytest

from research.entry_signals import ENTRY_A, ENTRY_B, EntrySignal
from research.event_study import (
    ForwardEventStudy, PriceBar, PricePath, SignalAttempt, StudyEvent, bootstrap_mean_ci,
    build_cohorts, entry_groups, fillability_by_intent, selection_bias_diagnostic, summarize,
)
from research.foundation import ExecutionResult, SHANGHAI, TradingCalendar


SESSIONS = tuple(date(2025, 1, day) for day in (
    2, 3, 6, 7, 9, 10, 13, 14, 15, 16, 17, 20, 21, 22, 23, 24,
    27, 28, 29, 30, 31))  # Weekend and Jan 8 exchange holiday omitted.
CALENDAR = TradingCalendar(SESSIONS, "synthetic-calendar/1")
START = SESSIONS[1]


def bar(day, high, low, close, *, factor=None):
    return PriceBar(day, D(str(high)), D(str(low)), D(str(close)),
                    adjustment_factor=D(str(factor)) if factor is not None else None,
                    factor_available_at=datetime.combine(day, time(15, 5), SHANGHAI)
                    if factor is not None else None)


def path(values, *, actions=(), termination=(), factor_qualified=False, factor=1):
    bars = tuple(bar(SESSIONS[i + 1], *row, factor=factor if factor_qualified else None)
                 for i, row in enumerate(values))
    return PricePath("S", "snap", bars, tuple(actions), tuple(termination), factor_qualified,
                     "a" * 64 if factor_qualified else None,
                     D(str(factor)) if factor_qualified else None,
                     datetime.combine(START, time(15, 5), SHANGHAI) if factor_qualified else None)


def event(*, entry_date=START, price="100"):
    return StudyEvent("E", "S", SESSIONS[0], entry_date, D(price), ("GENERIC",),
                      "snap", ("synthetic/1",))


def one(values, h=5, **kwargs):
    return ForwardEventStudy(CALENDAR, (h,)).evaluate(event(), path(values, **kwargs)).horizons[0]


@pytest.mark.parametrize("values,expected_mfe,expected_mae,expected_ret,expected_time", [
    ([(103, 99, 102), (105, 101, 104), (108, 103, 107)], D("0.08"), D("-0.01"), D("0.07"), 3),
    ([(101, 98, 99), (100, 95, 96), (98, 92, 93)], D("0.01"), D("-0.08"), D("-0.07"), 1),
    ([(108, 99, 105), (110, 101, 108), (102, 94, 95)], D("0.10"), D("-0.06"), D("-0.05"), 2),
    ([(101, 90, 92), (105, 94, 103), (112, 100, 110)], D("0.12"), D("-0.10"), D("0.10"), 3),
])
def test_path_shapes(values, expected_mfe, expected_mae, expected_ret, expected_time):
    row = one(values, h=3)
    assert (row.mfe, row.mae, row.ret, row.time_to_mfe) == (
        expected_mfe, expected_mae, expected_ret, expected_time)


def test_first_and_final_day_mfe_and_horizon_off_by_one():
    values = [(110, 95, 100), (105, 95, 99), (104, 94, 98),
              (103, 93, 97), (120, 92, 119)]
    record = ForwardEventStudy(CALENDAR, (1, 3, 5)).evaluate(event(), path(values))
    assert [(r.horizon, r.time_to_mfe, r.ret) for r in record.horizons] == [
        (1, 1, D(0)), (3, 1, D("-0.02")), (5, 5, D("0.19"))]
    assert record.horizons[2].mae == D("-0.08")


def test_weekend_holiday_and_suspension_advance_exchange_clock():
    values = [(101, 99, 100), (102, 98, 101),
              PriceBar(SESSIONS[3], None, None, None, "SUSPENDED"),
              (104, 97, 103)]
    bars = (bar(SESSIONS[1], *values[0]), bar(SESSIONS[2], *values[1]),
            values[2], bar(SESSIONS[4], *values[3]))
    record = ForwardEventStudy(CALENDAR, (3, 4)).evaluate(
        event(), PricePath("S", "snap", bars))
    h3, h4 = record.horizons
    assert SESSIONS[2] == date(2025, 1, 6)  # Friday -> Monday, no weekend index.
    assert SESSIONS[4] == date(2025, 1, 9)  # Jan 8 holiday is skipped.
    assert (h3.observed_bar_count, h3.expected_session_count, h3.coverage_ratio) == (2, 3, D(2) / 3)
    assert h3.ret is None and h3.mfe == D("0.02") and h3.coverage_status == "PARTIAL"
    assert h4.ret == D("0.03") and h4.observed_bar_count == 3


def test_missing_and_invalid_endpoint_are_not_carried_forward():
    bars = (bar(SESSIONS[1], 105, 95, 102),
            PriceBar(SESSIONS[2], None, None, None, "INVALID"))
    row = ForwardEventStudy(CALENDAR, (2,)).evaluate(event(), PricePath("S", "snap", bars)).horizons[0]
    assert row.ret is None and row.mfe == D("0.05") and row.mae == D("-0.05")
    missing = ForwardEventStudy(CALENDAR, (3,)).evaluate(event(), PricePath("S", "snap", bars)).horizons[0]
    assert missing.ret is None and missing.observed_bar_count == 1


def test_unqualified_corporate_action_excludes_comparable_metrics():
    row = one([(105, 95, 100), (55, 48, 50)], h=2, actions=(SESSIONS[2],))
    assert row.coverage_status == "CORPORATE_ACTION_UNRESOLVED"
    assert row.exclusion_reason == "UNQUALIFIED_COMPARABLE_PRICE"
    assert row.mfe is row.mae is row.ret is None


def test_qualified_pit_factor_converts_future_raw_prices_to_entry_scale():
    original = path([(105, 95, 100), (55, 48, 50)], actions=(SESSIONS[2],), factor_qualified=True)
    bars = (bar(SESSIONS[1], 105, 95, 100, factor=1),
            bar(SESSIONS[2], 55, 48, 50, factor=2))
    qualified = PricePath("S", "snap", bars, original.corporate_action_dates, (), True,
                          "a" * 64, D(1), original.entry_factor_available_at)
    row = ForwardEventStudy(CALENDAR, (2,)).evaluate(event(), qualified).horizons[0]
    assert (row.mfe, row.mae, row.ret) == (D("0.10"), D("-0.05"), D(0))


def test_factor_timing_and_termination_fail_closed():
    late = path([(105, 95, 100), (55, 48, 50)], actions=(SESSIONS[2],), factor_qualified=True)
    bars = (late.bars[0], bar(SESSIONS[2], 55, 48, 50, factor=2))
    late = PricePath("S", "snap", bars, late.corporate_action_dates, (), True,
                     "a" * 64, D(1), datetime.combine(SESSIONS[2], time(15), SHANGHAI))
    assert ForwardEventStudy(CALENDAR, (2,)).evaluate(event(), late).horizons[0].ret is None
    terminated = one([(105, 95, 100), (108, 96, 107)], h=2,
                     termination=(SESSIONS[2],))
    assert terminated.coverage_status == "PATH_UNRESOLVED" and terminated.ret is None


def test_horizon_beyond_frozen_calendar_is_unresolved():
    row = ForwardEventStudy(CALENDAR, (20,)).evaluate(
        event(entry_date=SESSIONS[2]), path([(101, 99, 100)])).horizons[0]
    assert row.exclusion_reason == "INSUFFICIENT_CALENDAR"


def attempt(signal_id, signal_day, status, *, obs="pool", reasons=(ENTRY_A,),
            intent="NORMAL_CLOSE_INTENT", rsi="74", count=4, pool_day=1,
            fill_day=None, price="101"):
    signal_at = datetime.combine(signal_day, time(15, 10), SHANGHAI)
    signal = EntrySignal(obs, "OBSERVATION_POOL_V1", reasons,
                         ("RSI14_PROJECT_V1",), "snap", signal_day, signal_at,
                         reasons, intent, "a" * 64,
                         execution_fidelity_gap=intent == "LIMIT_UP_CLOSE_BOARD_INTENT")
    execution = ExecutionResult(status, fill_day if status == "MODELLED_FILL" else None,
                                None, None, D(price) if status == "MODELLED_FILL" else None,
                                None, "NEXT_SESSION_V1", D(0))
    return SignalAttempt(signal_id, "S", signal, execution, D(100), count, D(rsi), pool_day)


def test_all_attempts_preserved_first_fill_per_observation_instance():
    attempts = [attempt("1", SESSIONS[0], "NO_FILL"),
                attempt("2", SESSIONS[1], "MODELLED_FILL", fill_day=SESSIONS[2]),
                attempt("3", SESSIONS[2], "MODELLED_FILL", fill_day=SESSIONS[3]),
                attempt("4", SESSIONS[3], "EXECUTION_UNRESOLVED", obs="pool2")]
    cohorts = build_cohorts(attempts, CALENDAR)
    assert (cohorts.signal_count, cohorts.fill_count, cohorts.no_fill_count,
            cohorts.unresolved_count, cohorts.fill_rate) == (4, 2, 1, 1, D(2) / 3)
    assert [e.signal_id for e in cohorts.signal_events] == ["1", "2", "3", "4"]
    assert [e.signal_id for e in cohorts.primary_filled_events] == ["2"]
    assert cohorts.primary_filled_events[0].entry_price == D(101)
    assert cohorts.signal_events[0].entry_date == SESSIONS[1]
    assert fillability_by_intent(attempts)["NORMAL_CLOSE_INTENT"]["fill_rate"] == D(2) / 3
    with pytest.raises(ValueError, match="Duplicate signal"):
        build_cohorts([attempts[0], attempts[0]], CALENDAR)


def test_entry_groups_distribution_bootstrap_and_selection_bias():
    attempts = [attempt("a", SESSIONS[0], "MODELLED_FILL", fill_day=SESSIONS[1]),
                attempt("b", SESSIONS[0], "NO_FILL", obs="pool2", reasons=(ENTRY_B,),
                        intent="LIMIT_UP_CLOSE_BOARD_INTENT", rsi="77", count=5, pool_day=7),
                attempt("ab", SESSIONS[0], "MODELLED_FILL", obs="pool3",
                        reasons=(ENTRY_A, ENTRY_B), rsi="81", fill_day=SESSIONS[1])]
    cohorts = build_cohorts(attempts, CALENDAR)
    engine = ForwardEventStudy(CALENDAR, (1,))
    paths = {
        "a": path([(101, 99, 101)]),
        "b": path([(111, 99, 110)]),
        "ab": path([(102, 98, 100)]),
    }
    records = [engine.evaluate(e, paths[e.signal_id]) for e in cohorts.signal_events]
    groups = entry_groups(records)
    assert {key: len(groups[key]) for key in ("ENTRY_A", "ENTRY_B", "A_ONLY", "B_ONLY", "A_AND_B")} == {
        "ENTRY_A": 2, "ENTRY_B": 2, "A_ONLY": 1, "B_ONLY": 1, "A_AND_B": 1}
    assert len(groups["RSI_70_75"]) == len(groups["RSI_75_80"]) == len(groups["RSI_GT_80"]) == 1
    assert len(groups["LIMIT_UP_COUNT_4"]) == 2 and len(groups["LIMIT_UP_COUNT_5"]) == 1
    assert len(groups["POOL_SESSION_7"]) == 1 and len(groups["LIMIT_UP_CLOSE_BOARD_INTENT"]) == 1
    summary = summarize(records, (1,))
    assert summary["event_count"] == 3 and summary["unique_security_count"] == 1
    assert summary["events_per_security"]["median"] == D(3)
    assert summary["horizons"][1]["ret"]["N"] == 3
    assert bootstrap_mean_ci([D(1), D(2)], seed=7) == bootstrap_mean_ci([D(1), D(2)], seed=7)
    bias = selection_bias_diagnostic(records, (1,))[1]
    assert bias["status"] == "EXECUTION_SELECTION_BIAS"
    assert bias["no_fill_mean_ret"] > bias["fill_mean_ret"]


def test_no_fill_and_unresolved_never_enter_primary():
    cohorts = build_cohorts([attempt("nf", SESSIONS[0], "NO_FILL"),
                             attempt("u", SESSIONS[0], "EXECUTION_UNRESOLVED", obs="p2")], CALENDAR)
    assert cohorts.primary_filled_events == () and cohorts.fill_rate == D(0)


def test_signal_reference_catches_action_between_signal_and_d0():
    cohorts = build_cohorts([attempt("nf", SESSIONS[0], "NO_FILL")], CALENDAR)
    signal_event = cohorts.signal_events[0]
    assert signal_event.price_reference_date == SESSIONS[0]
    raw_path = PricePath("S", "snap", (bar(SESSIONS[1], 55, 48, 50),),
                         corporate_action_dates=(SESSIONS[1],))
    row = ForwardEventStudy(CALENDAR, (1,)).evaluate(signal_event, raw_path).horizons[0]
    assert row.coverage_status == "CORPORATE_ACTION_UNRESOLVED" and row.ret is None


def test_board_intent_fillability_keeps_fidelity_gap_and_unresolved():
    attempts = [attempt("board_nf", SESSIONS[0], "NO_FILL", intent="LIMIT_UP_CLOSE_BOARD_INTENT"),
                attempt("board_u", SESSIONS[0], "EXECUTION_UNRESOLVED", obs="pool2",
                        intent="LIMIT_UP_CLOSE_BOARD_INTENT")]
    board = fillability_by_intent(attempts)["LIMIT_UP_CLOSE_BOARD_INTENT"]
    assert board == {"signal_N": 2, "fill_N": 0, "no_fill_N": 1, "unresolved_N": 1,
                     "executable_attempt_N": 1, "fill_rate": D(0), "execution_fidelity_gap": True}


def test_final_calendar_signal_remains_visible_without_forward_session():
    cohorts = build_cohorts([attempt("tail", SESSIONS[-1], "NO_FILL")], CALENDAR)
    assert cohorts.signal_count == 1 and cohorts.signal_events[0].entry_date is None
    row = ForwardEventStudy(CALENDAR, (1,)).evaluate(
        cohorts.signal_events[0], PricePath("S", "snap", ())).horizons[0]
    assert row.coverage_status == "PATH_UNRESOLVED" and row.ret is None


def test_provenance_conflict_rejected():
    with pytest.raises(ValueError, match="provenance"):
        ForwardEventStudy(CALENDAR, (1,)).evaluate(event(), PricePath("OTHER", "snap", ()))
