"""Synthetic EOD cases for ObservationPool and the two frozen entry contracts."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from research.entry_signals import ENTRY_A, ENTRY_B, EntryPriceEvidence
from research.foundation import RunContext, TradingCalendar, UniverseRecord, execution_policy
from research.observation_pool import process_eod, resolve_execution
from research.primitives import PrimitiveValue
from scripts.verify_stage1_observation_entry_manifest import verify_manifest


CODE = "600000.SH"
SNAPSHOT = "synthetic-observation-snapshot"
# 11 October is an exchange holiday in this synthetic frozen calendar.
DAYS = tuple(date(2024, 10, n) for n in (7, 8, 9, 10, 14, 15, 16, 17, 18))
CALENDAR = TradingCalendar(DAYS, "synthetic-observation-calendar/1")


def at(day, clock="15:10:00"):
    return datetime.fromisoformat(f"{day.isoformat()}T{clock}+08:00")


def universe(day, **changes):
    values = dict(
        security_id=CODE, trade_date=day, security_type="A_SHARE_COMMON_STOCK",
        exchange="SSE", board="MAIN", lifecycle_state="LISTED", trading_status="TRADING",
        daily_constraint_valid=True, limit_applicable=True,
        limit_regime="QUALIFIED_10_PERCENT", market_bar_valid=True,
        history_observation_count=5, minimum_history_required=5,
        history_sufficient=True, eligible=True, exclusion_reasons=(),
        data_snapshot_id=SNAPSHOT, universe_contract_version="1")
    values.update(changes)
    return UniverseRecord(**values)


def primitive(day, name, value, status="READY", available=None):
    return PrimitiveValue(name, status, day, value if status == "READY" else None,
                          available if available is not None else at(day, "15:00:00"), SNAPSHOT)


def prices(day, *, low="11", close="11", indicator=None, factor="2", available=None):
    return EntryPriceEvidence(
        CODE, day, SNAPSHOT, Decimal(low), Decimal(close),
        Decimal(indicator if indicator is not None else close), Decimal(factor),
        Decimal(factor), available or at(day, "15:00:00"),
        at(day, "15:00:00"), at(day, "09:00:00"), "a" * 64, True)


def day(index, previous=None, *, count=4, rsi="75", ma="10", low="11", close="11",
        indicator=None, board=False, universe_changes=None, price_evidence=None,
        rsi_status="READY", ma_status="READY", limit_status="READY"):
    current = DAYS[index]
    return process_eod(
        previous=previous, calendar=CALENDAR,
        universe=universe(current, **(universe_changes or {})),
        limit_count=primitive(current, "LIMIT_UP_COUNT_5_V1", count, limit_status),
        rsi=primitive(current, "RSI14_PROJECT_V1", Decimal(rsi), rsi_status),
        ma=primitive(current, "MA5_SMA_V1", Decimal(ma), ma_status),
        close_limit=primitive(current, "IS_CLOSE_LIMIT_UP_V1", board),
        prices=price_evidence if price_evidence is not None else prices(
            current, low=low, close=close, indicator=indicator),
        decision_at=at(current))


def execute(pool, *, opening="10.50", payload=True):
    following = CALENDAR.next_after(pool.pending_signal_date)
    next_day = None if not payload else {
        "security_id": CODE, "trade_date": following.isoformat(), "trading_status": "TRADING",
        "raw_open": opening, "daily_constraint": {
            "security_id": CODE, "trade_date": following.isoformat(),
            "available_at": f"{following}T09:00:00+08:00", "validation_status": "VALID",
            "trading_allowed": True, "suspended": False, "limit_applicable": True,
            "limit_up_price": "11.00", "price_tick": "0.01",
            "source_provenance": "synthetic-qualified-gate-b"}}
    result = execution_policy("NEXT_SESSION_V1").execute(
        context=RunContext(SNAPSHOT, "gate-b-retry3-daily-contract/1", "1",
                           "NEXT_SESSION_V1", "1", CALENDAR.version), calendar=CALENDAR,
        security_id=CODE, signal_date=pool.pending_signal_date,
        signal_at=at(pool.pending_signal_date),
        latest_input_at=at(pool.pending_signal_date, "15:00:00"),
        side="BUY", next_day=next_day)
    return result, resolve_execution(pool, result, CALENDAR)


@pytest.mark.parametrize("count,rsi,admitted", [
    (5, "71", True), (4, "70.0001", True), (3, "75", False),
    (4, "70", False), (4, "69", False)])
def test_admission_strict_count_and_rsi(count, rsi, admitted):
    result = day(0, count=count, rsi=rsi)
    assert (result.pool is not None) is admitted
    assert result.status == ("ACTIVE" if admitted else "NOT_ADMITTED")
    if admitted:
        assert result.pool.pool_session_index == 1


@pytest.mark.parametrize("status", ["NOT_ENOUGH_HISTORY", "DEPENDENCY_INVALID"])
def test_admission_dependency_not_ready_is_not_false(status):
    assert day(0, limit_status=status).event == "NO_ADMISSION_EVALUATION"
    assert day(0, rsi_status=status).event == "NO_ADMISSION_EVALUATION"


def test_admission_requires_eligible_ten_percent_universe():
    assert day(0, universe_changes={"eligible": False,
                                    "exclusion_reasons": ("NON_10_PERCENT_REGIME",),
                                    "limit_regime": "OTHER"}).status == "NOT_ADMITTED"
    assert day(0, universe_changes={"eligible": False,
                                    "exclusion_reasons": ("INVALID_DAILY_CONSTRAINT",),
                                    "daily_constraint_valid": False}).status == "NOT_ADMITTED"


def test_admission_day_can_signal_a_and_it_is_not_a_fill():
    result = day(0, low="9.9", close="10.5")
    assert result.status == "SIGNALLED" and result.pool.pool_session_index == 1
    assert result.signal.signal_date == DAYS[0]
    assert result.signal.signal_reasons == (ENTRY_A,)
    assert result.signal.execution_policy_id == "NEXT_SESSION_V1"
    assert not hasattr(result.signal, "execution_price")


def test_seven_exchange_sessions_and_suspension_age_without_entry():
    pool = day(0).pool
    for index in range(1, 7):
        changes = None
        if index == 2:
            changes = {"trading_status": "SUSPENDED", "eligible": False,
                       "market_bar_valid": False,
                       "exclusion_reasons": ("SUSPENDED", "MISSING_BAR")}
        result = day(index, pool, universe_changes=changes)
        pool = result.pool
        assert pool.pool_session_index == index + 1
        if index == 2:
            assert result.event == "SUSPENDED_NO_ENTRY_EVALUATION"
            assert result.signal is None and pool.status == "ACTIVE"
    assert DAYS[3].weekday() == 3 and DAYS[4].weekday() == 0
    assert DAYS[4] - DAYS[3] == __import__("datetime").timedelta(days=4)
    assert pool.status == "EXPIRED" and pool.pool_session_index == 7
    assert day(7, pool).pool.admission_date == DAYS[7]


def test_day_seven_signal_can_execute_on_eighth_exchange_session():
    pool = day(0).pool
    for index in range(1, 6):
        pool = day(index, pool).pool
    seventh = day(6, pool, low="9.9", close="10.5")
    assert seventh.status == "SIGNALLED" and seventh.pool.pool_session_index == 7
    result, after = execute(seventh.pool)
    assert result.execution_date == DAYS[7] and after.status == "TERMINATED_AFTER_FILL"
    no_fill, expired = execute(seventh.pool, opening="11.00")
    assert no_fill.status == "NO_FILL" and expired.status == "EXPIRED"


@pytest.mark.parametrize("changes", [
    {"lifecycle_state": "DELISTED", "eligible": False, "exclusion_reasons": ("DELISTED",)},
    {"security_type": "ETF", "eligible": False, "exclusion_reasons": ("UNSUPPORTED_SECURITY_TYPE",)},
    {"daily_constraint_valid": False, "eligible": False, "exclusion_reasons": ("INVALID_DAILY_CONSTRAINT",)},
    {"limit_regime": "OTHER", "eligible": False, "exclusion_reasons": ("NON_10_PERCENT_REGIME",)},
    {"eligible": False, "exclusion_reasons": ("DATA_CONFLICT",)},
])
def test_hard_invalidation(changes):
    assert day(1, day(0).pool, universe_changes=changes).status == "INVALIDATED"


def test_count_is_admission_only_and_rsi_can_fall_below_70():
    pool = day(0).pool
    later = day(1, pool, count=0, rsi="65")
    assert later.status == "ACTIVE" and later.pool.has_seen_rsi_below_70


@pytest.mark.parametrize("low,close,rsi,trigger", [
    ("10", "10", "75", True), ("9.9", "10.5", "75", True),
    ("10.01", "11", "75", False), ("9.9", "9.99", "75", False),
    ("9.9", "10.5", "70", False), ("9.9", "10.5", "69", False)])
def test_entry_a_touch_hold_and_strict_rsi(low, close, rsi, trigger):
    result = day(0, low=low, close=close, rsi=rsi)
    assert (result.signal is not None) is trigger
    if trigger:
        assert result.signal.signal_reasons == (ENTRY_A,)


def test_entry_a_not_ready_is_distinct_from_false_and_active_exact_70_has_no_signal():
    pool = day(0).pool
    not_ready = day(1, pool, ma_status="NOT_ENOUGH_HISTORY", low="9.9", close="10.5")
    assert not_ready.status == "ACTIVE" and not_ready.entry_status == "NOT_READY"
    assert not_ready.signal is None
    exact = day(2, not_ready.pool, rsi="70", low="9.9", close="10.5")
    assert exact.status == "ACTIVE" and exact.signal is None


def test_entry_a_uses_t_factor_scale_and_rejects_future_factor():
    # A split makes historical raw closes 10 but T-anchored adjusted MA5 5.
    safe = prices(DAYS[0], low="4.9", close="5.1", factor="2")
    assert safe.raw_equivalent(Decimal("5"), decision_at=at(DAYS[0])) == Decimal("5")
    assert day(0, ma="5", price_evidence=safe).signal.signal_reasons == (ENTRY_A,)
    future = replace(safe, factor_available_at=at(DAYS[1], "09:00:00"))
    assert day(0, ma="5", price_evidence=future).status == "INVALIDATED"
    wrong_scale = replace(safe, anchor_factor_t=Decimal("1"))
    assert day(0, ma="5", price_evidence=wrong_scale).status == "INVALIDATED"


def test_a_never_reclassifies_a_pool_after_below_70():
    pool = day(1, day(0).pool, rsi="65", low="10", close="10").pool
    result = day(2, pool, rsi="75", low="9.9", close="10.5", ma="10")
    assert result.signal is not None and result.signal.signal_reasons == (ENTRY_B,)


@pytest.mark.parametrize("previous,current,close,expected", [
    ("69", "71", "12", True), ("70", "71", "12", False),
    ("69", "70", "12", False), ("69", "71", "11", False),
    ("69", "71", "10", False)])
def test_entry_b_true_cross_and_positive_indicator_price(previous, current, close, expected):
    pool = day(0).pool
    pool = day(1, pool, rsi=previous).pool
    result = day(2, pool, rsi=current, close=close, ma_status="NOT_ENOUGH_HISTORY")
    assert (result.signal is not None) is expected
    if expected:
        assert result.signal.signal_reasons == (ENTRY_B,)


def test_entry_b_needs_prior_below_and_survives_suspension_gap():
    assert day(1, day(0).pool, rsi="75", close="12").signal is None
    below = day(1, day(0).pool, rsi="65").pool
    suspended = day(2, below, universe_changes={
        "trading_status": "SUSPENDED", "eligible": False,
        "market_bar_valid": False, "exclusion_reasons": ("SUSPENDED", "MISSING_BAR")})
    result = day(3, suspended.pool, rsi="75", close="12", ma_status="NOT_ENOUGH_HISTORY")
    assert result.signal.signal_reasons == (ENTRY_B,)


def test_entry_b_does_not_require_ma5_input():
    pool = day(1, day(0).pool, rsi="65").pool
    current = DAYS[2]
    result = process_eod(
        previous=pool, calendar=CALENDAR, universe=universe(current),
        limit_count=None, rsi=primitive(current, "RSI14_PROJECT_V1", Decimal("75")),
        ma=None, close_limit=primitive(current, "IS_CLOSE_LIMIT_UP_V1", False),
        prices=prices(current, low="12", close="12"), decision_at=at(current))
    assert result.signal.signal_reasons == (ENTRY_B,)


def test_board_intent_for_a_and_b_has_fidelity_gap():
    a = day(0, low="9.9", close="10.5", board=True).signal
    pool = day(1, day(0).pool, rsi="65").pool
    b = day(2, pool, rsi="75", close="12", board=True).signal
    for signal in (a, b):
        assert signal.execution_intent_type == "LIMIT_UP_CLOSE_BOARD_INTENT"
        assert signal.execution_fidelity_gap is True
        assert signal.execution_policy_id == "NEXT_SESSION_V1"
    assert a.signal_reasons == (ENTRY_A,) and b.signal_reasons == (ENTRY_B,)


def test_execution_results_are_separate_and_no_fill_allows_later_retry():
    signal = day(0, low="9.9", close="10.5")
    filled, terminal = execute(signal.pool)
    no_fill, active = execute(signal.pool, opening="11.00")
    unresolved, pending = execute(signal.pool, payload=False)
    assert filled.status == "MODELLED_FILL" and terminal.status == "TERMINATED_AFTER_FILL"
    assert no_fill.status == "NO_FILL" and active.status == "ACTIVE"
    assert unresolved.status == "EXECUTION_UNRESOLVED" and pending.status == "SIGNALLED"
    assert day(1, pending).event == "EXECUTION_PENDING"
    retried = day(1, active, low="9.9", close="10.5")
    assert retried.status == "SIGNALLED" and retried.signal.signal_date == DAYS[1]


def test_unresolved_pending_signal_ages_through_suspension_and_expires():
    pool = day(0, low="9.9", close="10.5").pool
    _, pool = execute(pool, payload=False)
    for index in range(1, 7):
        changes = None
        if index == 1:
            changes = {"trading_status": "SUSPENDED", "eligible": False,
                       "market_bar_valid": False,
                       "exclusion_reasons": ("SUSPENDED", "MISSING_BAR")}
        result = day(index, pool, universe_changes=changes)
        pool = result.pool
        assert pool.pool_session_index == index + 1
        assert result.signal is None
    assert pool.status == "EXPIRED"


def test_instance_id_re_admission_and_no_overlapping_pools():
    first = day(0).pool
    still_first = day(1, first, count=5).pool
    assert still_first.observation_instance_id == first.observation_instance_id
    invalid = day(2, still_first, universe_changes={
        "limit_regime": "OTHER", "eligible": False,
        "exclusion_reasons": ("NON_10_PERCENT_REGIME",)}).pool
    renewed = day(3, invalid, count=5).pool
    assert renewed.admission_date == DAYS[3]
    assert renewed.observation_instance_id != first.observation_instance_id


def test_future_or_cross_snapshot_inputs_cannot_create_signal():
    current = DAYS[0]
    late_price = replace(prices(current, low="9.9", close="10.5"),
                         indicator_available_at=at(DAYS[1], "09:00:00"))
    assert day(0, price_evidence=late_price).status == "INVALIDATED"
    cross_snapshot = replace(prices(current, low="9.9", close="10.5"),
                             source_snapshot_id="other")
    assert day(0, price_evidence=cross_snapshot).status == "INVALIDATED"
    late_rsi = primitive(current, "RSI14_PROJECT_V1", Decimal("75"),
                         available=at(DAYS[1], "09:00:00"))
    no_admission = process_eod(
        previous=None, calendar=CALENDAR, universe=universe(current),
        limit_count=primitive(current, "LIMIT_UP_COUNT_5_V1", 4), rsi=late_rsi,
        ma=primitive(current, "MA5_SMA_V1", Decimal("10")),
        close_limit=primitive(current, "IS_CLOSE_LIMIT_UP_V1", False),
        prices=prices(current), decision_at=at(current))
    assert no_admission.event == "NO_ADMISSION_EVALUATION"


def test_preclose_t_values_do_not_masquerade_as_finalized_eod():
    current = DAYS[0]
    early = primitive(current, "RSI14_PROJECT_V1", Decimal("75"),
                      available=at(current, "14:59:00"))
    result = process_eod(
        previous=None, calendar=CALENDAR, universe=universe(current),
        limit_count=primitive(current, "LIMIT_UP_COUNT_5_V1", 4), rsi=early,
        ma=primitive(current, "MA5_SMA_V1", Decimal("10")),
        close_limit=primitive(current, "IS_CLOSE_LIMIT_UP_V1", False),
        prices=prices(current), decision_at=at(current))
    assert result.event == "NO_ADMISSION_EVALUATION"
    early_price = replace(prices(current, low="9.9", close="10.5"),
                          indicator_available_at=at(current, "14:59:00"))
    assert day(0, price_evidence=early_price).status == "INVALIDATED"


def test_observation_entry_manifest_integrity():
    assert verify_manifest() == 11
