"""Hand-computable causal fixtures for Stage 1 primitives; no trading actions."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
import pandas as pd

from research.dependencies import GRAPH_VERSION, PrimitiveRunContext, required_history
from research.foundation import TradingCalendar
from research.indicator_prices import (
    AdjustmentFactor, RawCloseObservation, build_pit_adjusted_close,
)
from research.primitives import LimitObservation, is_close_limit_up, limit_up_count5, ma5, rsi14
from scripts.verify_stage1_primitives_manifest import verify_manifest
from three_board_rsi_entry.indicators import calculate_rsi


CODE = "600000.SH"
SNAPSHOT = "synthetic-snapshot-v1"
DAYS = tuple(day for n in range(12) if (day := date(2024, 10, 7) + timedelta(days=n)).weekday() < 5)
CALENDAR = TradingCalendar(DAYS, "synthetic-primitive-calendar/1")


def cutoff(day):
    return datetime.fromisoformat(f"{day.isoformat()}T15:10:00+08:00")


def raw_prices(prices, *, start=0, invalid=(), suspended=()):
    return [RawCloseObservation(CODE, DAYS[start + n],
                                None if n in invalid or n in suspended else str(price),
                                "SUSPENDED" if n in suspended else "TRADING",
                                n not in invalid and n not in suspended,
                                None if n in suspended else f"{DAYS[start+n]}T15:00:00+08:00",
                                SNAPSHOT)
            for n, price in enumerate(prices)]


def factor(day, value="1", *, available=None, verified=True):
    return AdjustmentFactor(CODE, day, value, available or f"{day}T09:00:00+08:00",
                            SNAPSHOT, "a" * 64, verified)


def series(prices, *, invalid=(), suspended=(), reset=None, factors=None):
    as_of = DAYS[len(prices) - 1]
    return build_pit_adjusted_close(
        security_id=CODE, as_of_date=as_of, decision_at=cutoff(as_of),
        reset_date=reset or DAYS[0], source_snapshot_id=SNAPSHOT,
        factor_schema="effective_events", calendar=CALENDAR,
        raw=raw_prices(prices, invalid=invalid,
                                                           suspended=suspended),
        factors=factors or [factor(DAYS[0])])


def limit_observation(day, *, close="11.00", upper="11.00", rate="0.10",
                      valid=True, suspended=False):
    constraint = {"security_id": CODE, "trade_date": day.isoformat(),
                  "available_at": f"{day}T09:00:00+08:00", "validation_status": "VALID" if valid else "INVALID",
                  "limit_applicable": True, "trading_allowed": not suspended,
                  "suspended": suspended, "price_tick": "0.01", "limit_up_price": upper,
                  "limit_up_rate": rate, "limit_down_rate": rate,
                  "source_provenance": "synthetic-qualified-gate-b"}
    return LimitObservation(CODE, "A_SHARE_COMMON_STOCK", day, None if suspended else close,
                            "SUSPENDED" if suspended else "TRADING", not suspended,
                            None if suspended else f"{day}T15:00:00+08:00",
                            constraint, SNAPSHOT)


def count(rows, *, reset=None):
    day = rows[-1].trade_date
    return limit_up_count5(security_id=CODE, as_of_date=day,
                           reset_date=reset or DAYS[0], source_snapshot_id=SNAPSHOT,
                           decision_at=cutoff(day), calendar=CALENDAR, observations=rows)


def test_legacy_rsi_first_delta_seed_and_hand_calculated_recursion():
    # Legacy formula: gain / absolute change, both first seeded from delta +1.
    # Deltas +1,-1,+2 give (avg_gain, avg_abs) = (1,1), (13/14,1), (197/196,15/14).
    first = rsi14(series([10]), decision_at=cutoff(DAYS[0]))
    assert first.status == "NOT_ENOUGH_HISTORY" and first.value is None
    second = rsi14(series([10, 11]), decision_at=cutoff(DAYS[1]))
    assert second.ready and second.value == Decimal(100)
    third = rsi14(series([10, 11, 10]), decision_at=cutoff(DAYS[2]))
    assert third.ready and third.value == Decimal(100) * Decimal(13) / Decimal(14)
    fourth = rsi14(series([10, 11, 10, 12]), decision_at=cutoff(DAYS[3]))
    assert fourth.ready and abs(fourth.value - Decimal(197) / Decimal(210) * 100) < Decimal("1e-24")
    assert fourth.qualified_observation_count == 4 and fourth.window_start == DAYS[0]


@pytest.mark.parametrize("prices,status,value", [
    ([10, 11, 12, 13], "READY", Decimal(100)),
    ([10, 9, 8, 7], "READY", Decimal(0)),
    ([10, 10, 10, 10], "DEPENDENCY_INVALID", None),
])
def test_rsi_rising_falling_and_flat(prices, status, value):
    result = rsi14(series(prices), decision_at=cutoff(DAYS[len(prices) - 1]))
    assert result.status == status and result.value == value


def test_new_rsi_formula_matches_the_existing_project_definition():
    prices = [10, 11, 10, 12, 11, 14]
    result = rsi14(series(prices), decision_at=cutoff(DAYS[5]))
    legacy = calculate_rsi(pd.Series(prices, dtype=float), period=14).iloc[-1]
    assert result.ready and abs(float(result.value) - legacy) < 1e-12


def test_ma5_first_valid_flat_and_increasing_values():
    early = ma5(series([1, 2, 3, 4]), decision_at=cutoff(DAYS[3]))
    assert early.status == "NOT_ENOUGH_HISTORY" and early.value is None
    increasing = ma5(series([1, 2, 3, 4, 5]), decision_at=cutoff(DAYS[4]))
    assert increasing.ready and increasing.value == Decimal(3)
    assert increasing.window_start == DAYS[0] and increasing.available_at == cutoff(DAYS[4]).replace(minute=0)
    flat = ma5(series([7] * 5), decision_at=cutoff(DAYS[4]))
    assert flat.ready and flat.value == Decimal(7)


def test_invalid_price_is_not_silently_skipped_but_suspension_is_explicit_gap():
    invalid = series([1, 2, 3, 4, 5], invalid=(2,))
    assert ma5(invalid, decision_at=cutoff(DAYS[4])).status == "DEPENDENCY_INVALID"
    assert rsi14(invalid, decision_at=cutoff(DAYS[4])).status == "DEPENDENCY_INVALID"
    suspended = series([1, 2, 99, 3, 4, 5], suspended=(2,))
    result = ma5(suspended, decision_at=cutoff(DAYS[5]))
    assert result.ready and result.value == Decimal(3)


def test_pit_adjusted_price_is_as_of_t_and_separate_from_raw_limit_close():
    # Two-for-one split effective on day 5: historical raw 10 becomes indicator 5.
    raw = raw_prices([10, 10, 10, 10, 5])
    events = [factor(DAYS[0], "1"), factor(DAYS[4], "2", available=f"{DAYS[4]}T09:00:00+08:00")]
    adjusted = build_pit_adjusted_close(
        security_id=CODE, as_of_date=DAYS[4], decision_at=cutoff(DAYS[4]),
        reset_date=DAYS[0], source_snapshot_id=SNAPSHOT, factor_schema="effective_events",
        calendar=CALENDAR, raw=raw, factors=events + [factor(DAYS[5], "100")])
    assert [row.price for row in adjusted.observations] == [Decimal(5)] * 5
    assert ma5(adjusted, decision_at=cutoff(DAYS[4])).value == Decimal(5)
    assert raw[0].raw_close == "10" and adjusted.observations[0].price == Decimal(5)
    event = is_close_limit_up(limit_observation(DAYS[4], close="5.00", upper="5.00"),
                              decision_at=cutoff(DAYS[4]))
    assert event.ready and event.value is True


def test_future_or_unverified_factors_never_enter_a_t_indicator():
    raw = raw_prices([10, 11, 12])
    safe = build_pit_adjusted_close(
        security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
        reset_date=DAYS[0], source_snapshot_id=SNAPSHOT, factor_schema="effective_events",
        calendar=CALENDAR, raw=raw, factors=[factor(DAYS[0]), factor(DAYS[3], "999")])
    assert safe.observations[-1].price == Decimal(12)
    for bad in (factor(DAYS[0], available=f"{DAYS[3]}T09:00:00+08:00"),
                factor(DAYS[0], verified=False)):
        with pytest.raises(ValueError, match="Unqualified or unavailable"):
            build_pit_adjusted_close(
                security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
                reset_date=DAYS[0], source_snapshot_id=SNAPSHOT,
                factor_schema="effective_events", calendar=CALENDAR, raw=raw, factors=[bad])


def test_missing_security_day_cannot_be_silently_skipped():
    raw = raw_prices([10, 11, 12, 13, 14])
    with pytest.raises(ValueError, match="Missing security-day"):
        build_pit_adjusted_close(
            security_id=CODE, as_of_date=DAYS[4], decision_at=cutoff(DAYS[4]),
            reset_date=DAYS[0], source_snapshot_id=SNAPSHOT,
            factor_schema="effective_events", calendar=CALENDAR,
            raw=raw[:2] + raw[3:], factors=[factor(DAYS[0])])
    rows = [limit_observation(DAYS[n]) for n in range(5)]
    assert count(rows[:2] + rows[3:]).status == "INVALID_INPUT"


def test_future_raw_bar_cannot_rewrite_t_price_prefix():
    raw = raw_prices([10, 11, 12, 999])
    before = build_pit_adjusted_close(
        security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
        reset_date=DAYS[0], source_snapshot_id=SNAPSHOT,
        factor_schema="effective_events", calendar=CALENDAR,
        raw=raw, factors=[factor(DAYS[0])])
    raw[-1] = replace(raw[-1], raw_close="1")
    after = build_pit_adjusted_close(
        security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
        reset_date=DAYS[0], source_snapshot_id=SNAPSHOT,
        factor_schema="effective_events", calendar=CALENDAR,
        raw=raw, factors=[factor(DAYS[0])])
    assert before == after


@pytest.mark.parametrize("rate,upper,close", [
    ("0.10", "11.00", "11.00"), ("0.20", "12.00", "12.00"),
    ("0.05", "10.50", "10.50")])
def test_is_close_limit_up_uses_raw_canonical_upper_for_any_regime(rate, upper, close):
    result = is_close_limit_up(limit_observation(DAYS[0], rate=rate, upper=upper, close=close),
                               decision_at=cutoff(DAYS[0]))
    assert result.ready and result.value is True
    below = is_close_limit_up(limit_observation(DAYS[0], rate=rate, upper=upper, close="10.00"),
                              decision_at=cutoff(DAYS[0]))
    assert below.ready and below.value is False


def test_is_close_limit_up_invalid_constraint_and_off_tick_price():
    assert is_close_limit_up(limit_observation(DAYS[0], valid=False),
                             decision_at=cutoff(DAYS[0])).status == "INVALID_INPUT"
    assert is_close_limit_up(limit_observation(DAYS[0], close="10.999"),
                             decision_at=cutoff(DAYS[0])).status == "INVALID_INPUT"
    assert is_close_limit_up(replace(limit_observation(DAYS[0]), security_type="ETF"),
                             decision_at=cutoff(DAYS[0])).status == "INVALID_INPUT"


@pytest.mark.parametrize("limits,expected", [
    ([True] * 5, 5), ([True] * 4 + [False], 4),
    ([True] * 3 + [False] * 2, 3)])
def test_limit_up_count_golden_windows(limits, expected):
    rows = [limit_observation(DAYS[n], close="11.00" if limit else "10.50")
            for n, limit in enumerate(limits)]
    result = count(rows)
    assert result.ready and result.value == expected
    assert result.window_start == DAYS[0] and result.window_end == DAYS[4]
    assert result.qualified_observation_count == 5


def test_limit_count_requires_five_and_skips_only_explicit_suspension():
    four = [limit_observation(DAYS[n]) for n in range(4)]
    assert count(four).status == "NOT_ENOUGH_HISTORY"
    rows = four[:2] + [limit_observation(DAYS[2], suspended=True)] + [
        limit_observation(DAYS[n]) for n in (3, 4, 5)]
    result = count(rows)
    assert result.ready and result.value == 5 and result.window_start == DAYS[0]
    broken = rows[:]
    broken[1] = limit_observation(DAYS[1], valid=False)
    assert count(broken).status == "DEPENDENCY_INVALID"


@pytest.mark.parametrize("rate,upper", [("0.20", "12.00"), ("0.05", "10.50")])
def test_non_ten_percent_history_makes_window_invalid_even_when_t_is_ten(rate, upper):
    rows = [limit_observation(DAYS[0], rate=rate, upper=upper, close=upper)] + [
        limit_observation(DAYS[n]) for n in range(1, 5)]
    assert is_close_limit_up(rows[0], decision_at=cutoff(DAYS[0])).value is True
    result = count(rows)
    assert result.status == "DEPENDENCY_INVALID" and result.reason == "NON_10_PERCENT_WINDOW"
    assert result.value is None


def test_relisting_resets_rsi_ma_and_limit_history():
    reset = DAYS[4]
    adjusted = series([10, 11, 12, 13, 14], reset=reset)
    assert len(adjusted.observations) == 1
    assert rsi14(adjusted, decision_at=cutoff(DAYS[4])).status == "NOT_ENOUGH_HISTORY"
    assert ma5(adjusted, decision_at=cutoff(DAYS[4])).status == "NOT_ENOUGH_HISTORY"
    rows = [limit_observation(DAYS[n]) for n in range(5)]
    assert count(rows, reset=reset).status == "NOT_ENOUGH_HISTORY"


def test_dependency_graph_keeps_price_and_constraint_history_separate():
    empty = required_history(())
    assert empty.base_universe_minimum == 0
    requirement = required_history(("LIMIT_UP_COUNT_5_V1", "MA5_SMA_V1",
                                    "RSI14_TONGHUASHUN_V1"))
    assert (requirement.price_history_required, requirement.limit_constraint_history_required,
            requirement.base_universe_minimum) == (5, 5, 5)
    assert requirement.price_replay_scope == "ALL_VALID_OBSERVATIONS_SINCE_RESET"
    assert requirement.dependency_graph_version == GRAPH_VERSION
    assert required_history(("RSI14_TONGHUASHUN_V1",)).price_history_required == 2
    with pytest.raises(ValueError):
        required_history(("UNKNOWN",))
    assert PrimitiveRunContext(SNAPSHOT, "1").indicator_price_basis_version == "PIT_ADJUSTED_CLOSE_V1"


def test_daily_factor_schema_requires_each_price_date():
    raw = raw_prices([10, 11, 12])
    complete = build_pit_adjusted_close(
        security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
        reset_date=DAYS[0], source_snapshot_id=SNAPSHOT, factor_schema="daily",
        calendar=CALENDAR, raw=raw,
        factors=[factor(DAYS[n], str(n + 1)) for n in range(3)])
    assert complete.observations[0].price == Decimal(10) / Decimal(3)
    with pytest.raises(ValueError, match="factor coverage"):
        build_pit_adjusted_close(
            security_id=CODE, as_of_date=DAYS[2], decision_at=cutoff(DAYS[2]),
            reset_date=DAYS[0], source_snapshot_id=SNAPSHOT, factor_schema="daily",
            calendar=CALENDAR, raw=raw,
            factors=[factor(DAYS[0]), factor(DAYS[2])])


def test_delayed_t_factor_delays_derived_indicator_availability():
    adjusted = series([10, 11, 12, 13, 14], factors=[
        factor(DAYS[0]), factor(DAYS[4], "2", available=f"{DAYS[4]}T15:05:00+08:00")])
    result = ma5(adjusted, decision_at=cutoff(DAYS[4]))
    assert result.ready and result.available_at == datetime.fromisoformat(f"{DAYS[4]}T15:05:00+08:00")


def test_golden_artifact_records_hand_calculated_rsi_evidence():
    path = Path(__file__).resolve().parents[1] / "artifacts/stage1_strategy_primitives/golden_validation_cases.json"
    golden = json.loads(path.read_bytes())
    rows = golden["rsi14_legacy_first_delta_seed"]["rows"]
    assert rows[1]["rsi"] == "100"
    assert rows[2]["avg_gain"] == "13/14"
    assert rows[3]["avg_gain"] == "197/196"
    assert rows[3]["avg_change"] == "15/14"
    assert rows[3]["rsi"] == "100*197/210"


def test_primitive_publication_manifest_integrity():
    assert verify_manifest() == 13
