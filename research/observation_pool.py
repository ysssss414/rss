"""Seven-exchange-session observation state; no position or execution simulation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256

from .entry_signals import EntryEvaluation, EntryPriceEvidence, EntrySignal, evaluate_entries
from .foundation import ExecutionResult, SHANGHAI, TradingCalendar, UniverseRecord
from .primitives import PrimitiveValue


POOL_VERSION = "OBSERVATION_POOL_V1"
POOL_STATES = {"NOT_ADMITTED", "ACTIVE", "SIGNALLED", "EXPIRED",
               "INVALIDATED", "TERMINATED_AFTER_FILL"}
TERMINAL = {"EXPIRED", "INVALIDATED", "TERMINATED_AFTER_FILL"}


@dataclass(frozen=True)
class ObservationPool:
    security_id: str
    observation_instance_id: str
    admission_date: date
    data_snapshot_id: str
    status: str
    last_evaluated_date: date
    pool_session_index: int
    has_seen_rsi_below_70: bool = False
    previous_valid_rsi: Decimal | None = None
    previous_valid_indicator_close: Decimal | None = None
    pending_signal_date: date | None = None
    contract_version: str = POOL_VERSION


@dataclass(frozen=True)
class PoolDecision:
    pool: ObservationPool | None
    status: str
    event: str
    signal: EntrySignal | None = None
    entry_status: str | None = None


def _valid_primitive(value: PrimitiveValue, expected: str, universe: UniverseRecord,
                     decision_at: datetime) -> bool:
    return (value.primitive_id == expected and value.as_of_date == universe.trade_date
            and value.source_snapshot_id == universe.data_snapshot_id
            and (not value.ready or value.available_at is not None
                 and value.available_at.utcoffset() is not None
                 and value.available_at <= decision_at
                 and value.available_at.astimezone(SHANGHAI).date() == universe.trade_date
                 and value.available_at.astimezone(SHANGHAI).time() >= time(15)))


def _hard_invalid(universe: UniverseRecord) -> bool:
    if (universe.security_type != "A_SHARE_COMMON_STOCK"
            or universe.lifecycle_state not in {"LISTED", "RELISTED"}
            or not universe.daily_constraint_valid or not universe.limit_applicable
            or universe.limit_regime != "QUALIFIED_10_PERCENT"
            or universe.trading_status not in {"TRADING", "SUSPENDED"}):
        return True
    allowed = {"SUSPENDED", "MISSING_BAR"} if universe.trading_status == "SUSPENDED" else set()
    return bool(set(universe.exclusion_reasons) - allowed) or (
        universe.trading_status == "TRADING" and not universe.eligible)


def _instance_id(security_id: str, day: date) -> str:
    return sha256(f"{security_id}|{day.isoformat()}|{POOL_VERSION}".encode()).hexdigest()


def process_eod(*, previous: ObservationPool | None, calendar: TradingCalendar,
                universe: UniverseRecord, limit_count: PrimitiveValue | None,
                rsi: PrimitiveValue | None, ma: PrimitiveValue | None,
                close_limit: PrimitiveValue | None, prices: EntryPriceEvidence | None,
                decision_at: datetime) -> PoolDecision:
    day = universe.trade_date
    if (day not in calendar.sessions or decision_at.utcoffset() is None
            or decision_at.astimezone(SHANGHAI).date() != day
            or decision_at.astimezone(SHANGHAI).time() < time(15)
            or not universe.security_id or not universe.data_snapshot_id):
        raise ValueError("Invalid T EOD pool context")
    if previous is not None:
        if (previous.security_id != universe.security_id or previous.contract_version != POOL_VERSION
                or previous.status not in POOL_STATES - {"NOT_ADMITTED"}
                or previous.admission_date not in calendar.sessions
                or day <= previous.last_evaluated_date):
            raise ValueError("Conflicting pool instance or non-forward evaluation")
        if previous.status == "SIGNALLED":
            index = calendar.sessions.index(day) - calendar.sessions.index(previous.admission_date) + 1
            pending = replace(previous, last_evaluated_date=day, pool_session_index=index)
            if previous.data_snapshot_id != universe.data_snapshot_id or _hard_invalid(universe):
                invalid = replace(pending, status="INVALIDATED")
                return PoolDecision(invalid, invalid.status, "HARD_INVALIDATION")
            if index >= 7:
                expired = replace(pending, status="EXPIRED")
                return PoolDecision(expired, expired.status, "PENDING_SIGNAL_HORIZON_EXPIRED")
            return PoolDecision(pending, pending.status, "EXECUTION_PENDING")
        if previous.status not in TERMINAL:
            index = calendar.sessions.index(day) - calendar.sessions.index(previous.admission_date) + 1
            if index > 7:
                expired = replace(previous, status="EXPIRED", last_evaluated_date=day,
                                  pool_session_index=index)
                return PoolDecision(expired, expired.status, "POOL_EXPIRED")
            if previous.data_snapshot_id != universe.data_snapshot_id or _hard_invalid(universe):
                invalid = replace(previous, status="INVALIDATED", last_evaluated_date=day,
                                  pool_session_index=index)
                return PoolDecision(invalid, invalid.status, "HARD_INVALIDATION")
            pool = replace(previous, last_evaluated_date=day, pool_session_index=index)
            if universe.trading_status == "SUSPENDED":
                if index == 7:
                    pool = replace(pool, status="EXPIRED")
                return PoolDecision(pool, pool.status, "SUSPENDED_NO_ENTRY_EVALUATION")
        else:
            pool = None  # A terminal instance permits a future, non-overlapping admission.
    else:
        pool = None

    if pool is None:
        if not universe.eligible or _hard_invalid(universe):
            return PoolDecision(None, "NOT_ADMITTED", "NO_ADMISSION_UNIVERSE")
        if limit_count is None or rsi is None:
            return PoolDecision(None, "NOT_ADMITTED", "NO_ADMISSION_EVALUATION")
        if (not _valid_primitive(limit_count, "LIMIT_UP_COUNT_5_V1", universe, decision_at)
                or not _valid_primitive(rsi, "RSI14_PROJECT_V1", universe, decision_at)):
            return PoolDecision(None, "NOT_ADMITTED", "NO_ADMISSION_EVALUATION")
        if not limit_count.ready or not rsi.ready:
            return PoolDecision(None, "NOT_ADMITTED", "NO_ADMISSION_EVALUATION")
        if (type(limit_count.value) is not int or limit_count.value not in {4, 5}
                or not isinstance(rsi.value, Decimal) or not rsi.value.is_finite()
                or not Decimal(0) <= rsi.value <= Decimal(100)):
            return PoolDecision(None, "NOT_ADMITTED", "NO_ADMISSION_EVALUATION")
        if rsi.value <= 70:
            return PoolDecision(None, "NOT_ADMITTED", "ADMISSION_CONDITION_FALSE")
        pool = ObservationPool(universe.security_id, _instance_id(universe.security_id, day),
                               day, universe.data_snapshot_id, "ACTIVE", day, 1)
        admitted = True
    else:
        admitted = False

    if rsi is None or close_limit is None or prices is None:
        pool = replace(pool, status="INVALIDATED")
        return PoolDecision(pool, pool.status, "MISSING_PIT_ENTRY_DEPENDENCY",
                            entry_status="INVALID")
    evaluation = evaluate_entries(pool=pool, universe=universe, rsi=rsi, ma=ma,
                                  close_limit=close_limit, prices=prices,
                                  decision_at=decision_at)
    if evaluation.status == "INVALID":
        pool = replace(pool, status="INVALIDATED")
        return PoolDecision(pool, pool.status, evaluation.reason or "PIT_DATA_CONFLICT",
                            entry_status="INVALID")
    if evaluation.status == "READY" or evaluation.reason in {"MA5_UNAVAILABLE", "MA5_NOT_READY"}:
        pool = replace(pool, has_seen_rsi_below_70=pool.has_seen_rsi_below_70 or rsi.value < 70,
                       previous_valid_rsi=rsi.value,
                       previous_valid_indicator_close=prices.indicator_close)
    if evaluation.signal is not None:
        pool = replace(pool, status="SIGNALLED", pending_signal_date=day)
        return PoolDecision(pool, pool.status, "ENTRY_SIGNAL", evaluation.signal, "READY")
    if pool.pool_session_index == 7:
        pool = replace(pool, status="EXPIRED")
    return PoolDecision(pool, pool.status,
                        "POOL_ADMISSION" if admitted else "POOL_OBSERVATION",
                        entry_status=evaluation.status)


def resolve_execution(pool: ObservationPool, result: ExecutionResult,
                      calendar: TradingCalendar) -> ObservationPool:
    """Consume a separate NEXT_SESSION_V1 outcome, without creating a position."""
    if (pool.status != "SIGNALLED" or pool.pending_signal_date is None
            or result.policy_id != "NEXT_SESSION_V1"
            or result.status not in {"MODELLED_FILL", "NO_FILL", "EXECUTION_UNRESOLVED"}):
        raise ValueError("Execution outcome disagrees with a pending pool signal")
    target = calendar.next_after(pool.pending_signal_date)
    if result.execution_date is not None and result.execution_date != target:
        raise ValueError("Execution result is not for the next exchange session")
    if result.status == "EXECUTION_UNRESOLVED":
        return pool
    if result.status == "MODELLED_FILL":
        return replace(pool, status="TERMINATED_AFTER_FILL", pending_signal_date=None)
    return replace(pool, status="EXPIRED" if pool.pool_session_index == 7 else "ACTIVE",
                   pending_signal_date=None)
