"""Point-in-time indicator prices; raw trading prices remain a separate lineage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Sequence

from .foundation import TradingCalendar


PRICE_BASIS = "PIT_ADJUSTED_CLOSE_V1"
PRECISION = Context(prec=28, rounding=ROUND_HALF_EVEN)
SHANGHAI = timezone(timedelta(hours=8))


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Availability time must have an offset")
    return parsed


def _positive(value: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Price/factor must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal price/factor") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("Price/factor must be finite and positive")
    return parsed


@dataclass(frozen=True)
class RawCloseObservation:
    security_id: str
    trade_date: date
    raw_close: str | None
    trading_status: str
    valid_bar: bool
    available_at: str | None
    source_snapshot_id: str


@dataclass(frozen=True)
class AdjustmentFactor:
    security_id: str
    effective_date: date
    factor: str
    available_at: str
    source_snapshot_id: str
    source_hash: str
    pit_verified: bool


@dataclass(frozen=True)
class IndicatorPrice:
    security_id: str
    trade_date: date
    price: Decimal | None
    price_basis: str
    available_at: datetime | None
    source_snapshot_id: str
    valid: bool
    state: str


@dataclass(frozen=True)
class IndicatorPriceSeries:
    security_id: str
    as_of_date: date
    reset_date: date
    price_basis: str
    source_snapshot_id: str
    calendar_version: str
    factor_schema: str
    observations: tuple[IndicatorPrice, ...]


def build_pit_adjusted_close(
    *, security_id: str, as_of_date: date, decision_at: datetime, reset_date: date,
    source_snapshot_id: str, factor_schema: str, calendar: TradingCalendar,
    raw: Sequence[RawCloseObservation], factors: Sequence[AdjustmentFactor],
) -> IndicatorPriceSeries:
    """Reuse Stage 0's qfq factor(date)/factor(T) semantics with an explicit T cutoff."""
    if (not security_id or not source_snapshot_id or reset_date > as_of_date
            or factor_schema not in {"daily", "effective_events"}
            or decision_at.utcoffset() is None
            or decision_at.astimezone(SHANGHAI).date() != as_of_date
            or reset_date < calendar.sessions[0] or as_of_date not in calendar.sessions):
        raise ValueError("Invalid frozen indicator-price context")

    # Filter by effective date first: future rows and revisions cannot alter the T prefix.
    bars = sorted((bar for bar in raw if reset_date <= bar.trade_date <= as_of_date),
                  key=lambda bar: bar.trade_date)
    admitted = sorted((factor for factor in factors if factor.effective_date <= as_of_date),
                      key=lambda factor: factor.effective_date)
    if not bars or not admitted or len({bar.trade_date for bar in bars}) != len(bars):
        raise ValueError("Missing or conflicting PIT price history")
    expected_days = {day for day in calendar.sessions if reset_date <= day <= as_of_date}
    if {bar.trade_date for bar in bars} != expected_days:
        raise ValueError("Missing security-day status or bar in frozen calendar")
    if len({factor.effective_date for factor in admitted}) != len(admitted):
        raise ValueError("Conflicting factor versions; select one frozen as-of source")
    for factor in admitted:
        if (factor.security_id != security_id or factor.source_snapshot_id != source_snapshot_id
                or not factor.pit_verified or len(factor.source_hash) != 64
                or any(char not in "0123456789abcdef" for char in factor.source_hash)
                or _time(factor.available_at) > decision_at):
            raise ValueError("Unqualified or unavailable factor")
        _positive(factor.factor)

    anchor = _factor_for(as_of_date, admitted, factor_schema)
    anchor_value = _positive(anchor.factor)
    anchor_available = _time(anchor.available_at)
    observations = []
    with localcontext(PRECISION):
        for bar in bars:
            if bar.security_id != security_id or bar.source_snapshot_id != source_snapshot_id:
                raise ValueError("Raw bar disagrees with frozen security/snapshot")
            if bar.trading_status == "SUSPENDED":
                observations.append(IndicatorPrice(security_id, bar.trade_date, None, PRICE_BASIS,
                                                   None, source_snapshot_id, False, "SUSPENDED"))
                continue
            if bar.trading_status != "TRADING":
                raise ValueError("Unknown historical trading state")
            if not bar.available_at or _time(bar.available_at) > decision_at:
                raise ValueError("Raw bar not available at T cutoff")
            if (_time(bar.available_at).astimezone(SHANGHAI).date() != bar.trade_date
                    or _time(bar.available_at).astimezone(SHANGHAI).time() < time(15, 0)):
                raise ValueError("Raw EOD bar was available before finalization")
            if not bar.valid_bar or bar.raw_close is None:
                observations.append(IndicatorPrice(security_id, bar.trade_date, None, PRICE_BASIS,
                                                   _time(bar.available_at), source_snapshot_id,
                                                   False, "INVALID_BAR"))
                continue
            factor = _factor_for(bar.trade_date, admitted, factor_schema)
            price = _positive(bar.raw_close) * _positive(factor.factor) / anchor_value
            available = max(_time(bar.available_at), _time(factor.available_at), anchor_available)
            observations.append(IndicatorPrice(security_id, bar.trade_date, price, PRICE_BASIS,
                                               available, source_snapshot_id, True, "TRADING"))
    return IndicatorPriceSeries(security_id, as_of_date, reset_date, PRICE_BASIS,
                                source_snapshot_id, calendar.version, factor_schema,
                                tuple(observations))


def _factor_for(day: date, factors: Sequence[AdjustmentFactor], schema: str) -> AdjustmentFactor:
    if schema == "daily":
        matching = [factor for factor in factors if factor.effective_date == day]
    else:
        matching = [factor for factor in factors if factor.effective_date <= day]
    if not matching:
        raise ValueError("PIT factor coverage missing")
    return matching[-1]
