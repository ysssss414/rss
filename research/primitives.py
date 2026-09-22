"""Causal, strategy-independent Stage 1 technical primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Mapping, Sequence

from .foundation import TradingCalendar
from .indicator_prices import IndicatorPriceSeries, PRICE_BASIS


PRECISION = Context(prec=28, rounding=ROUND_HALF_EVEN)
SHANGHAI = timezone(timedelta(hours=8))
READINESS = {"READY", "NOT_ENOUGH_HISTORY", "INVALID_INPUT", "DEPENDENCY_INVALID"}


@dataclass(frozen=True)
class PrimitiveValue:
    primitive_id: str
    status: str
    as_of_date: date
    value: Decimal | int | bool | None
    available_at: datetime | None
    source_snapshot_id: str
    reason: str | None = None
    qualified_observation_count: int = 0
    window_start: date | None = None
    window_end: date | None = None

    @property
    def ready(self) -> bool:
        return self.status == "READY"


@dataclass(frozen=True)
class LimitObservation:
    security_id: str
    security_type: str
    trade_date: date
    raw_close: str | None
    trading_status: str
    valid_bar: bool
    available_at: str | None
    daily_constraint: Mapping[str, object] | None
    source_snapshot_id: str


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp requires an offset")
    return parsed


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Canonical price/rate must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal price/rate") from exc
    if not parsed.is_finite():
        raise ValueError("Non-finite price/rate")
    return parsed


def _result(primitive: str, status: str, day: date, snapshot: str, *,
            value: Decimal | int | bool | None = None, at: datetime | None = None,
            reason: str | None = None, count: int = 0,
            start: date | None = None) -> PrimitiveValue:
    return PrimitiveValue(primitive, status, day, value, at, snapshot, reason,
                          count, start, day if start else None)


def _indicator_records(series: IndicatorPriceSeries, decision_at: datetime):
    if (series.price_basis != PRICE_BASIS or not series.source_snapshot_id
            or decision_at.utcoffset() is None
            or decision_at.astimezone(SHANGHAI).date() != series.as_of_date):
        return None
    records = [row for row in series.observations
               if series.reset_date <= row.trade_date <= series.as_of_date]
    if len({row.trade_date for row in records}) != len(records):
        return None
    if any(row.security_id != series.security_id or row.source_snapshot_id != series.source_snapshot_id
           or row.price_basis != PRICE_BASIS or row.available_at and row.available_at > decision_at
           for row in records):
        return None
    return sorted(records, key=lambda row: row.trade_date)


def ma5(series: IndicatorPriceSeries, *, decision_at: datetime) -> PrimitiveValue:
    primitive = "MA5_SMA_V1"
    records = _indicator_records(series, decision_at)
    if records is None:
        return _result(primitive, "INVALID_INPUT", series.as_of_date, series.source_snapshot_id,
                       reason="PRICE_SERIES_INVALID_OR_LATE")
    trading = [row for row in records if row.state != "SUSPENDED"]
    window = trading[-5:]
    if not window or window[-1].trade_date != series.as_of_date:
        return _result(primitive, "INVALID_INPUT", series.as_of_date, series.source_snapshot_id,
                       reason="NO_VALID_T_OBSERVATION")
    if len(window) < 5:
        return _result(primitive, "NOT_ENOUGH_HISTORY", series.as_of_date, series.source_snapshot_id,
                       count=len(window))
    if any(not row.valid or row.price is None or row.available_at is None for row in window):
        return _result(primitive, "DEPENDENCY_INVALID", series.as_of_date, series.source_snapshot_id,
                       reason="INVALID_PRICE_IN_WINDOW", count=len(window), start=window[0].trade_date)
    with localcontext(PRECISION):
        value = sum((row.price for row in window), Decimal("0")) / Decimal(5)
    return _result(primitive, "READY", series.as_of_date, series.source_snapshot_id,
                   value=value, at=max(row.available_at for row in window),
                   count=5, start=window[0].trade_date)


def rsi14(series: IndicatorPriceSeries, *, decision_at: datetime) -> PrimitiveValue:
    """Legacy Tonghuashun SMA(GAIN,14,1)/SMA(ABS(delta),14,1), first-delta seed."""
    primitive = "RSI14_PROJECT_V1"
    records = _indicator_records(series, decision_at)
    if records is None:
        return _result(primitive, "INVALID_INPUT", series.as_of_date, series.source_snapshot_id,
                       reason="PRICE_SERIES_INVALID_OR_LATE")
    trading = [row for row in records if row.state != "SUSPENDED"]
    if not trading or trading[-1].trade_date != series.as_of_date:
        return _result(primitive, "INVALID_INPUT", series.as_of_date, series.source_snapshot_id,
                       reason="NO_VALID_T_OBSERVATION")
    if len(trading) < 2:
        return _result(primitive, "NOT_ENOUGH_HISTORY", series.as_of_date, series.source_snapshot_id,
                       count=len(trading))
    if any(not row.valid or row.price is None or row.available_at is None for row in trading):
        return _result(primitive, "DEPENDENCY_INVALID", series.as_of_date, series.source_snapshot_id,
                       reason="INVALID_PRICE_SINCE_RESET", count=len(trading))
    average_gain = average_change = None
    with localcontext(PRECISION):
        for previous, current in zip(trading, trading[1:]):
            delta = current.price - previous.price
            gain = max(delta, Decimal("0"))
            change = abs(delta)
            if average_gain is None:
                average_gain, average_change = gain, change
            else:
                average_gain = (gain + Decimal(13) * average_gain) / Decimal(14)
                average_change = (change + Decimal(13) * average_change) / Decimal(14)
        if average_change == 0:
            return _result(primitive, "DEPENDENCY_INVALID", series.as_of_date,
                           series.source_snapshot_id, reason="ZERO_ABSOLUTE_CHANGE_UNDEFINED",
                           count=len(trading), start=trading[0].trade_date)
        value = average_gain / average_change * Decimal(100)
    return _result(primitive, "READY", series.as_of_date, series.source_snapshot_id,
                   value=value, at=max(row.available_at for row in trading),
                   count=len(trading), start=trading[0].trade_date)


def is_close_limit_up(observation: LimitObservation, *, decision_at: datetime) -> PrimitiveValue:
    """Raw close versus canonical raw upper limit; never an adjusted-price comparison."""
    primitive = "IS_CLOSE_LIMIT_UP_V1"
    day = observation.trade_date
    snapshot = observation.source_snapshot_id
    constraint = observation.daily_constraint or {}
    try:
        close_at = _time(observation.available_at)
        constraint_at = _time(constraint["available_at"])
        if (not snapshot or decision_at.utcoffset() is None
                or decision_at.astimezone(SHANGHAI).date() < day
                or close_at > decision_at or constraint_at > decision_at
                or close_at.astimezone(SHANGHAI).time() < time(15, 0)
                or observation.security_type != "A_SHARE_COMMON_STOCK"
                or observation.trading_status != "TRADING" or observation.valid_bar is not True
                or constraint.get("security_id") != observation.security_id
                or constraint.get("trade_date") != day.isoformat()
                or constraint.get("validation_status") != "VALID"
                or constraint.get("limit_applicable") is not True
                or constraint.get("trading_allowed") is not True
                or constraint.get("suspended") is not False
                or not constraint.get("source_provenance")):
            raise ValueError("Invalid PIT bar or DailyTradingConstraint")
        close = _decimal(observation.raw_close)
        upper = _decimal(constraint["limit_up_price"])
        tick = _decimal(constraint["price_tick"])
        with localcontext(PRECISION):
            if min(close, upper, tick) <= 0 or close % tick or upper % tick or close > upper:
                raise ValueError("Off-tick or impossible raw close/upper limit")
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return _result(primitive, "INVALID_INPUT", day, snapshot, reason="INVALID_RAW_CLOSE_OR_CONSTRAINT")
    return _result(primitive, "READY", day, snapshot, value=close == upper,
                   at=max(close_at, constraint_at), count=1, start=day)


def limit_up_count5(*, security_id: str, as_of_date: date, reset_date: date,
                    source_snapshot_id: str, decision_at: datetime, calendar: TradingCalendar,
                    observations: Sequence[LimitObservation]) -> PrimitiveValue:
    primitive = "LIMIT_UP_COUNT_5_V1"
    if (not security_id or not source_snapshot_id or reset_date > as_of_date
            or decision_at.utcoffset() is None
            or decision_at.astimezone(SHANGHAI).date() != as_of_date
            or reset_date < calendar.sessions[0] or as_of_date not in calendar.sessions):
        return _result(primitive, "INVALID_INPUT", as_of_date, source_snapshot_id,
                       reason="INVALID_WINDOW_CONTEXT")
    # Future rows are outside the T prefix. Suspensions are not zero-valued events.
    prefix = sorted((row for row in observations if reset_date <= row.trade_date <= as_of_date),
                    key=lambda row: row.trade_date)
    expected_days = {day for day in calendar.sessions if reset_date <= day <= as_of_date}
    if (len({row.trade_date for row in prefix}) != len(prefix)
            or {row.trade_date for row in prefix} != expected_days
            or any(row.security_id != security_id or row.source_snapshot_id != source_snapshot_id
                   for row in prefix)):
        return _result(primitive, "INVALID_INPUT", as_of_date, source_snapshot_id,
                       reason="CONFLICTING_WINDOW_SOURCE")
    trading = [row for row in prefix if row.trading_status != "SUSPENDED"]
    window = trading[-5:]
    if not window or window[-1].trade_date != as_of_date:
        return _result(primitive, "INVALID_INPUT", as_of_date, source_snapshot_id,
                       reason="NO_T_OBSERVATION")
    if len(window) < 5:
        return _result(primitive, "NOT_ENOUGH_HISTORY", as_of_date, source_snapshot_id,
                       count=len(window))
    values = [is_close_limit_up(row, decision_at=decision_at) for row in window]
    if any(not result.ready for result in values):
        return _result(primitive, "DEPENDENCY_INVALID", as_of_date, source_snapshot_id,
                       reason="INVALID_LIMIT_EVENT_IN_WINDOW", count=5, start=window[0].trade_date)
    try:
        regime_valid = all(
            row.daily_constraint.get("limit_applicable") is True
            and _decimal(row.daily_constraint["limit_up_rate"]) == Decimal("0.10")
            and _decimal(row.daily_constraint["limit_down_rate"]) == Decimal("0.10")
            for row in window
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        regime_valid = False
    if not regime_valid:
        return _result(primitive, "DEPENDENCY_INVALID", as_of_date, source_snapshot_id,
                       reason="NON_10_PERCENT_WINDOW", count=5, start=window[0].trade_date)
    return _result(primitive, "READY", as_of_date, source_snapshot_id,
                   value=sum(result.value for result in values),
                   at=max(result.available_at for result in values), count=5,
                   start=window[0].trade_date)
