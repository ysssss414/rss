"""Synthetic execution/lifecycle cases; no strategy exit rule or PnL."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from research.entry_signals import EntrySignal
from research.foundation import RunContext, TradingCalendar, execution_policy
from research.trade_lifecycle import (
    ExitSignal,
    PositionEvent,
    new_lifecycle,
    record_position_event,
    resolve_entry,
    resolve_exit,
    submit_entry,
    submit_exit,
)


CODE = "600000.SH"
SNAPSHOT = "synthetic-lifecycle-snapshot"
DAYS = tuple(date(2024, 10, day) for day in (9, 10, 14, 16, 17))
CALENDAR = TradingCalendar(DAYS, "synthetic-lifecycle-calendar/1")


def at(day, clock="15:10:00"):
    return datetime.fromisoformat(f"{day.isoformat()}T{clock}+08:00")


def entry_signal(day=DAYS[0], instance="observation-1"):
    return EntrySignal(
        observation_instance_id=instance,
        observation_contract_version="OBSERVATION_POOL_V1",
        entry_contract_versions=("ENTRY_A_PULLBACK_TO_MA5_V1",),
        primitive_versions=("RSI14_PROJECT_V1", "PIT_ADJUSTED_CLOSE_V1"),
        data_snapshot_id=SNAPSHOT,
        signal_date=day,
        signal_at=at(day),
        signal_reasons=("ENTRY_A_PULLBACK_TO_MA5_V1",),
        execution_intent_type="NORMAL_CLOSE_INTENT",
        factor_source_hash="a" * 64,
    )


def next_day(day, opening="10.50", **changes):
    payload = {
        "security_id": CODE,
        "trade_date": day.isoformat(),
        "trading_status": "TRADING",
        "raw_open": opening,
        "daily_constraint": {
            "security_id": CODE,
            "trade_date": day.isoformat(),
            "available_at": f"{day}T09:00:00+08:00",
            "validation_status": "VALID",
            "trading_allowed": True,
            "suspended": False,
            "limit_applicable": True,
            "limit_up_price": "11.00",
            "limit_down_price": "9.00",
            "price_tick": "0.01",
            "source_provenance": "synthetic-qualified-gate-b",
        },
    }
    payload.update(changes)
    return payload


def execute(signal_date, policy_id, side, payload):
    return execution_policy(policy_id).execute(
        context=RunContext(
            SNAPSHOT, "gate-b-retry3-daily-contract/1", "1",
            policy_id, "1", CALENDAR.version,
        ),
        calendar=CALENDAR,
        security_id=CODE,
        signal_date=signal_date,
        signal_at=at(signal_date),
        latest_input_at=at(signal_date, "15:00:00"),
        side=side,
        next_day=payload,
    )


def open_lifecycle():
    signal = entry_signal()
    pending = submit_entry(new_lifecycle(CODE), signal)
    fill = execute(DAYS[0], "NEXT_SESSION_V1", "BUY", next_day(DAYS[1]))
    return resolve_entry(pending, fill)


def exit_signal(position, day=DAYS[1]):
    return ExitSignal(
        position.position_id, CODE, day, at(day), at(day, "15:00:00"),
        ("SYNTHETIC_EXIT_SIGNAL_TEST_ONLY",), "synthetic-exit-snapshot",
        ("SYNTHETIC_EXIT_CONTRACT_TEST_ONLY",),
    )


@pytest.mark.parametrize(("opening", "payload", "status", "state"), [
    ("10.50", True, "MODELLED_FILL", "OPEN"),
    ("11.00", True, "NO_FILL", "ENTRY_FAILED"),
    ("10.50", False, "EXECUTION_UNRESOLVED", "ENTRY_FAILED"),
])
def test_entry_signal_execution_opens_only_after_modelled_fill(
    opening, payload, status, state,
):
    signal = entry_signal()
    pending = submit_entry(new_lifecycle(CODE), signal)
    result = execute(
        DAYS[0], "NEXT_SESSION_V1", "BUY",
        next_day(DAYS[1], opening) if payload else None,
    )
    lifecycle = resolve_entry(pending, result)
    assert result.status == status and lifecycle.state == state
    assert (lifecycle.position is not None) is (status == "MODELLED_FILL")
    if lifecycle.position:
        position = lifecycle.position
        assert position.security_id == CODE
        assert position.entry_signal_date == DAYS[0]
        assert position.entry_execution_date == DAYS[1]
        assert position.entry_execution_price == Decimal("10.50")
        assert position.observation_instance_id == "observation-1"
        assert position.execution_policy_id == "NEXT_SESSION_V1"
        assert position.data_snapshot_id == SNAPSHOT
        assert position.strategy_contract_versions


def test_open_exit_fill_closes_structurally_without_pnl():
    lifecycle = open_lifecycle()
    signal = exit_signal(lifecycle.position)
    pending = submit_exit(lifecycle, signal)
    result = execute(
        DAYS[1], "NEXT_SESSION_SELL_V1", "SELL", next_day(DAYS[2], "9.50"),
    )
    closed = resolve_exit(pending, result)
    assert result.status == "MODELLED_FILL" and closed.state == "CLOSED"
    assert closed.position is None and len(closed.closed_trades) == 1
    trade = closed.closed_trades[0]
    assert trade.exit_signal_date == DAYS[1]
    assert trade.exit_execution_date == DAYS[2]
    assert trade.exit_execution_price == Decimal("9.50")
    for prohibited in ("return_value", "profit", "loss", "holding_return"):
        assert not hasattr(trade, prohibited)


def test_exit_limit_down_no_fill_keeps_position_open_for_external_resignal():
    lifecycle = open_lifecycle()
    pending = submit_exit(lifecycle, exit_signal(lifecycle.position))
    result = execute(
        DAYS[1], "NEXT_SESSION_SELL_V1", "SELL", next_day(DAYS[2], "9.00"),
    )
    after = resolve_exit(pending, result)
    assert result.status == "NO_FILL" and result.reason == "OPEN_AT_LIMIT_DOWN"
    assert after.state == "OPEN" and after.position is not None
    assert after.position.exit_attempt_count == 1
    assert after.pending_exit_signal is None and not after.closed_trades


@pytest.mark.parametrize("change", [
    "suspended", "missing_constraint", "invalid_constraint", "missing_open",
])
def test_exit_unresolved_preserves_open_economic_position_and_reason(change):
    lifecycle = open_lifecycle()
    pending = submit_exit(lifecycle, exit_signal(lifecycle.position))
    payload = next_day(DAYS[2], "9.50")
    if change == "suspended":
        payload["trading_status"] = "SUSPENDED"
    elif change == "missing_constraint":
        payload.pop("daily_constraint")
    elif change == "invalid_constraint":
        payload["daily_constraint"]["validation_status"] = "INVALID"
    else:
        payload.pop("raw_open")
    result = execute(DAYS[1], "NEXT_SESSION_SELL_V1", "SELL", payload)
    unresolved = resolve_exit(pending, result)
    assert result.status == "EXECUTION_UNRESOLVED"
    assert unresolved.state == "EXIT_UNRESOLVED"
    assert unresolved.position is not None
    assert unresolved.issue.code == "EXECUTION_UNRESOLVED"
    assert unresolved.issue.detail == result.reason
    assert not unresolved.closed_trades


def test_new_entry_while_open_is_ignored_and_does_not_average_position():
    lifecycle = open_lifecycle()
    original = lifecycle.position
    ignored = submit_entry(lifecycle, entry_signal(DAYS[1], "observation-2"))
    assert ignored.state == "OPEN" and ignored.position == original
    assert ignored.events[-1].event_type == "IGNORED_WHILE_POSITION_OPEN"


def test_sell_next_session_uses_weekend_and_frozen_holiday_calendar():
    lifecycle = open_lifecycle()
    pending = submit_exit(lifecycle, exit_signal(lifecycle.position))
    weekend = execute(
        DAYS[1], "NEXT_SESSION_SELL_V1", "SELL", next_day(DAYS[2], "9.50"),
    )
    assert weekend.execution_date == date(2024, 10, 14)
    assert (weekend.order_at.hour, weekend.order_at.minute) == (9, 15)
    assert (weekend.execution_at.hour, weekend.execution_at.minute) == (9, 25)
    still_open = resolve_exit(
        pending,
        replace(weekend, status="NO_FILL", execution_price=None,
                reason="SYNTHETIC_NO_FILL"),
    )
    second = submit_exit(still_open, exit_signal(still_open.position, DAYS[2]))
    holiday = execute(
        DAYS[2], "NEXT_SESSION_SELL_V1", "SELL", next_day(DAYS[3], "9.50"),
    )
    assert holiday.execution_date == date(2024, 10, 16)
    assert resolve_exit(second, holiday).state == "CLOSED"


@pytest.mark.parametrize(("event_type", "reason"), [
    ("SPLIT", "POSITION_CORPORATE_ACTION_UNRESOLVED"),
    ("DIVIDEND", "POSITION_CORPORATE_ACTION_UNRESOLVED"),
    ("RIGHTS", "POSITION_CORPORATE_ACTION_UNRESOLVED"),
    ("SECURITY_CODE_CHANGE", "POSITION_CORPORATE_ACTION_UNRESOLVED"),
    ("DELISTING", "POSITION_TRADING_LIFECYCLE_UNRESOLVED"),
    ("RELISTING", "POSITION_TRADING_LIFECYCLE_UNRESOLVED"),
    ("LONG_SUSPENSION", "POSITION_TRADING_LIFECYCLE_UNRESOLVED"),
    ("TRADING_LIFECYCLE_TERMINATION", "POSITION_TRADING_LIFECYCLE_UNRESOLVED"),
])
def test_position_events_fail_closed_without_fabricated_exit(event_type, reason):
    lifecycle = open_lifecycle()
    event = PositionEvent(
        CODE, event_type, DAYS[1], at(DAYS[1], "15:05:00"),
        "position-event-snapshot", "synthetic-source",
    )
    unresolved = record_position_event(lifecycle, event, as_of_at=at(DAYS[1]))
    assert unresolved.state == "EXIT_UNRESOLVED"
    assert unresolved.position == lifecycle.position
    assert unresolved.issue.code == reason
    assert unresolved.issue.data_snapshot_id == "position-event-snapshot"
    assert unresolved.issue.source_reference == "synthetic-source"
    assert unresolved.position_events == (event,)
    assert not unresolved.closed_trades


def test_future_position_event_is_rejected():
    lifecycle = open_lifecycle()
    future = PositionEvent(
        CODE, "DELISTING", DAYS[2], at(DAYS[2], "15:05:00"),
        "position-event-snapshot", "synthetic-source",
    )
    with pytest.raises(ValueError, match="future"):
        record_position_event(lifecycle, future, as_of_at=at(DAYS[1]))


def test_sell_policy_rejects_buy_side_and_missing_following_session():
    with pytest.raises(ValueError, match="unsupported side"):
        execute(DAYS[1], "NEXT_SESSION_SELL_V1", "BUY", next_day(DAYS[2]))
    result = execute(DAYS[-1], "NEXT_SESSION_SELL_V1", "SELL", None)
    assert result.status == "EXECUTION_UNRESOLVED"
    assert result.reason == "NO_FOLLOWING_SESSION"
