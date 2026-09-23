"""Stage 1 strategy-independent historical universe and daily execution model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from scripts.validate_stage1_gate_c_contract import classify_security_day


SHANGHAI = timezone(timedelta(hours=8))
POLICY_STATUS = {
    "NEXT_SESSION_V1": "CONTRACT_QUALIFIED",
    "NEXT_SESSION_SELL_V1": "CONTRACT_QUALIFIED",
    "CLOSING_AUCTION_V1": "NOT_QUALIFIED",
    "PRE_CLOSE_SNAPSHOT_V1": "NOT_QUALIFIED",
}


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("PIT timestamp needs an explicit offset")
    return result


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Canonical prices and rates must be decimal strings")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Non-finite decimal")
    return result


@dataclass(frozen=True)
class RunContext:
    data_snapshot_id: str
    daily_constraint_contract_version: str
    universe_contract_version: str
    execution_policy_id: str
    execution_policy_version: str
    calendar_version: str

    def __post_init__(self) -> None:
        if not all(vars(self).values()):
            raise ValueError("All run-context provenance fields are required")


@dataclass(frozen=True)
class TradingCalendar:
    sessions: tuple[date, ...]
    version: str
    qualified: bool = True

    def __post_init__(self) -> None:
        if not self.version or not self.qualified or not self.sessions or tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("A qualified, sorted, unique frozen trading calendar is required")

    def next_after(self, day: date) -> date | None:
        return next((session for session in self.sessions if session > day), None)


@dataclass(frozen=True)
class UniverseRecord:
    security_id: str
    trade_date: date
    security_type: str | None
    exchange: str | None
    board: str | None
    lifecycle_state: str | None
    trading_status: str | None
    daily_constraint_valid: bool
    limit_applicable: bool
    limit_regime: str | None
    market_bar_valid: bool
    history_observation_count: int
    minimum_history_required: int
    history_sufficient: bool
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    data_snapshot_id: str
    universe_contract_version: str


class HistoricalUniverse:
    """Evaluate one frozen security-day without vendor calls or strategy features."""

    def __init__(self, context: RunContext, calendar: TradingCalendar, *, minimum_history_required: int):
        if type(minimum_history_required) is not int or minimum_history_required < 0:
            raise ValueError("minimum_history_required must be an explicit non-negative integer")
        if (context.calendar_version != calendar.version or context.universe_contract_version != "1"
                or context.daily_constraint_contract_version != "gate-b-retry3-daily-contract/1"):
            raise ValueError("Run context disagrees with frozen Gate C inputs")
        if context.execution_policy_id != "NEXT_SESSION_V1":
            raise ValueError("Only NEXT_SESSION_V1 has a qualified universe cutoff")
        self.context = context
        self.calendar = calendar
        self.minimum_history_required = minimum_history_required

    def evaluate(self, row: Mapping[str, object], observations: Sequence[Mapping[str, object]]) -> UniverseRecord:
        day = _date(row["trade_date"])
        cutoff = _time(row["decision_at"])
        if day not in self.calendar.sessions:
            raise ValueError("Candidate day is absent from the frozen exchange calendar")
        if row.get("data_snapshot_id") != self.context.data_snapshot_id:
            raise ValueError("Candidate snapshot differs from run context")
        if row.get("universe_contract_version") != self.context.universe_contract_version:
            raise ValueError("Candidate contract version differs from run context")
        if row.get("execution_policy_id") != self.context.execution_policy_id:
            raise ValueError("Candidate policy differs from run context")

        events = row.get("lifecycle_events") or []
        lifecycle_conflict = False
        try:
            effective = [event for event in events if _date(event["effective_date"]) <= day
                         and _time(event["available_at"]) <= cutoff]
        except (KeyError, TypeError, ValueError):
            effective = []
            lifecycle_conflict = True
        effective.sort(key=lambda event: (event["effective_date"], event["available_at"]))
        reset = max((_date(event["effective_date"]) for event in effective
                     if event.get("kind") in {"LISTED", "RELISTED"}), default=None)
        count, conflict = self._count_history(observations, row["security_id"], reset, day, cutoff)
        normalized = dict(row, valid_history_sessions=count)
        primary = classify_security_day(normalized, minimum_history_required=self.minimum_history_required)
        reasons = [primary["exclusion_reason"]] if primary["exclusion_reason"] else []
        constraint = row.get("daily_constraint") or {}
        if not constraint.get("provenance") and not constraint.get("source_provenance"):
            reasons.append("INVALID_DAILY_CONSTRAINT")
        if conflict or lifecycle_conflict:
            reasons.append("DATA_CONFLICT")
        if row.get("security_type") != "A_SHARE_COMMON_STOCK":
            reasons.append("UNSUPPORTED_SECURITY_TYPE")
        if not row.get("bar_present"):
            reasons.append("MISSING_BAR")
        if constraint.get("validation_status") != "VALID":
            reasons.append("INVALID_DAILY_CONSTRAINT")
        elif constraint.get("limit_applicable") is False:
            reasons.append("NO_PRICE_LIMIT")
        if count < self.minimum_history_required:
            reasons.append("INSUFFICIENT_HISTORY")
        reasons = tuple(dict.fromkeys(reasons))
        state = effective[-1]["kind"] if effective else None
        regime = None
        if constraint.get("validation_status") == "VALID" and constraint.get("limit_applicable"):
            try:
                up = _decimal(constraint["limit_up_rate"])
                down = _decimal(constraint["limit_down_rate"])
                regime = "QUALIFIED_10_PERCENT" if up == down == Decimal("0.10") else "OTHER"
            except (KeyError, InvalidOperation, ValueError):
                pass
        try:
            bar_valid = row.get("bar_present") is True and _time(row["bar_available_at"]) <= cutoff
        except (KeyError, TypeError, ValueError):
            bar_valid = False
            if "MISSING_BAR" not in reasons:
                reasons = (*reasons, "MISSING_BAR")
        return UniverseRecord(
            security_id=row["security_id"], trade_date=day, security_type=row.get("security_type"),
            exchange=row.get("exchange"), board=row.get("board"), lifecycle_state=state,
            trading_status=row.get("trading_status"),
            daily_constraint_valid=constraint.get("validation_status") == "VALID",
            limit_applicable=constraint.get("limit_applicable") is True, limit_regime=regime,
            market_bar_valid=bar_valid,
            history_observation_count=count, minimum_history_required=self.minimum_history_required,
            history_sufficient=count >= self.minimum_history_required,
            eligible=not reasons, exclusion_reasons=reasons,
            data_snapshot_id=self.context.data_snapshot_id,
            universe_contract_version=self.context.universe_contract_version,
        )

    def _count_history(self, observations: Sequence[Mapping[str, object]], security_id: str,
                       reset: date | None, day: date, cutoff: datetime) -> tuple[int, bool]:
        seen: set[date] = set()
        conflict = False
        for bar in observations:
            try:
                bar_day = _date(bar["trade_date"])
                source_hash = bar.get("source_hash")
                if (bar.get("security_id") != security_id
                        or bar.get("data_snapshot_id") != self.context.data_snapshot_id
                        or not isinstance(source_hash, str) or len(source_hash) != 64
                        or any(character not in "0123456789abcdef" for character in source_hash)
                        or bar_day not in self.calendar.sessions):
                    conflict = True
                    continue
                if reset is None or not reset <= bar_day <= day or _time(bar["available_at"]) > cutoff:
                    continue
                if bar.get("trading_status") != "TRADING" or bar.get("valid_bar") is not True:
                    continue
                if bar_day in seen:
                    conflict = True
                seen.add(bar_day)
            except (KeyError, TypeError, ValueError):
                conflict = True
        return len(seen), conflict


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    execution_date: date | None
    order_at: datetime | None
    execution_at: datetime | None
    execution_price: Decimal | None
    reason: str | None
    policy_id: str
    slippage_bps: Decimal


class ExecutionPolicy:
    policy_id: str
    version: str

    def execute(self, *, context: RunContext, calendar: TradingCalendar, security_id: str,
                signal_date: date, signal_at: datetime, latest_input_at: datetime,
                side: str, next_day: Mapping[str, object] | None) -> ExecutionResult:
        raise NotImplementedError


class NextSessionV1(ExecutionPolicy):
    policy_id = "NEXT_SESSION_V1"
    version = "1"
    signal_time_rule = "T_EOD_FINALIZED"
    order_time_rule = "NEXT_EXCHANGE_SESSION_09_15"
    execution_time_rule = "OPENING_AUCTION_09_25"
    price_rule = "OFFICIAL_RAW_OPEN"
    fill_rule = "STRICTLY_BELOW_VALIDATED_UPPER_LIMIT"
    limit_rule = "AT_UPPER_LIMIT_NO_FILL"
    slippage_rule = "ZERO_BPS_IDEALIZED"

    def execute(self, *, context: RunContext, calendar: TradingCalendar, security_id: str,
                signal_date: date, signal_at: datetime, latest_input_at: datetime,
                side: str, next_day: Mapping[str, object] | None) -> ExecutionResult:
        if (context.execution_policy_id != self.policy_id or context.execution_policy_version != self.version
                or context.calendar_version != calendar.version
                or context.daily_constraint_contract_version != "gate-b-retry3-daily-contract/1"):
            raise ValueError("Execution policy or calendar differs from frozen run context")
        if (signal_at.utcoffset() is None or latest_input_at.utcoffset() is None
                or signal_at.astimezone(SHANGHAI).date() != signal_date
                or latest_input_at.astimezone(SHANGHAI).date() != signal_date
                or latest_input_at.astimezone(SHANGHAI).time() < time(15, 0)
                or latest_input_at > signal_at or side != "BUY"):
            raise ValueError("Invalid causal T EOD signal or unsupported side")
        if signal_date not in calendar.sessions:
            return self._result("EXECUTION_UNRESOLVED", None, None, None, "SIGNAL_NOT_IN_CALENDAR")
        target = calendar.next_after(signal_date)
        if target is None:
            return self._result("EXECUTION_UNRESOLVED", None, None, None, "NO_FOLLOWING_SESSION")
        order_at = datetime.combine(target, time(9, 15), SHANGHAI)
        execution_at = datetime.combine(target, time(9, 25), SHANGHAI)
        if not signal_at < order_at:
            raise ValueError("Signal cannot be ordered in the past")

        def unresolved(reason: str) -> ExecutionResult:
            return self._result("EXECUTION_UNRESOLVED", target, order_at, execution_at, reason)

        if next_day is None or next_day.get("security_id") != security_id or next_day.get("trade_date") != target.isoformat():
            return unresolved("MISSING_NEXT_SESSION_DATA")
        if next_day.get("trading_status") != "TRADING":
            return unresolved("NON_TRADING_OR_SUSPENDED")
        constraint = next_day.get("daily_constraint") or {}
        try:
            if (constraint.get("security_id") != security_id or constraint.get("trade_date") != target.isoformat()
                    or _time(constraint["available_at"]) > order_at
                    or constraint.get("validation_status") != "VALID"
                    or constraint.get("trading_allowed") is not True
                    or constraint.get("suspended") is not False
                    or constraint.get("limit_applicable") is not True
                    or not constraint.get("source_provenance")):
                return unresolved("INVALID_DAILY_CONSTRAINT")
            opening = _decimal(next_day["raw_open"])
            upper = _decimal(constraint["limit_up_price"])
            tick = _decimal(constraint["price_tick"])
            if min(opening, upper, tick) <= 0 or opening % tick or upper % tick or opening > upper:
                return unresolved("INVALID_OPEN_OR_LIMIT")
        except (KeyError, TypeError, InvalidOperation, ValueError):
            return unresolved("MISSING_OR_INVALID_OPEN_OR_CONSTRAINT")
        if opening == upper:
            return self._result("NO_FILL", target, order_at, execution_at, "OPEN_AT_LIMIT_UP")
        return self._result("MODELLED_FILL", target, order_at, execution_at, None, opening)

    def _result(self, status: str, day: date | None, order: datetime | None,
                execution: datetime | None, reason: str | None,
                price: Decimal | None = None) -> ExecutionResult:
        return ExecutionResult(status, day, order, execution, price, reason,
                               self.policy_id, Decimal("0"))


class NextSessionSellV1(ExecutionPolicy):
    """Conservative next-session sell model using the validated raw lower limit."""

    policy_id = "NEXT_SESSION_SELL_V1"
    version = "1"
    signal_time_rule = "T_EOD_FINALIZED"
    order_time_rule = "NEXT_EXCHANGE_SESSION_09_15"
    execution_time_rule = "OPENING_AUCTION_09_25"
    price_rule = "OFFICIAL_RAW_OPEN"
    fill_rule = "STRICTLY_ABOVE_VALIDATED_LOWER_LIMIT"
    limit_rule = "AT_LOWER_LIMIT_NO_FILL"
    slippage_rule = "ZERO_BPS_IDEALIZED"

    def execute(self, *, context: RunContext, calendar: TradingCalendar, security_id: str,
                signal_date: date, signal_at: datetime, latest_input_at: datetime,
                side: str, next_day: Mapping[str, object] | None) -> ExecutionResult:
        if (context.execution_policy_id != self.policy_id
                or context.execution_policy_version != self.version
                or context.calendar_version != calendar.version
                or context.daily_constraint_contract_version != "gate-b-retry3-daily-contract/1"):
            raise ValueError("Execution policy or calendar differs from frozen run context")
        if (signal_at.utcoffset() is None or latest_input_at.utcoffset() is None
                or signal_at.astimezone(SHANGHAI).date() != signal_date
                or latest_input_at.astimezone(SHANGHAI).date() != signal_date
                or latest_input_at.astimezone(SHANGHAI).time() < time(15, 0)
                or latest_input_at > signal_at or side != "SELL"):
            raise ValueError("Invalid causal T EOD signal or unsupported side")
        if signal_date not in calendar.sessions:
            return self._result("EXECUTION_UNRESOLVED", None, None, None,
                                "SIGNAL_NOT_IN_CALENDAR")
        target = calendar.next_after(signal_date)
        if target is None:
            return self._result("EXECUTION_UNRESOLVED", None, None, None,
                                "NO_FOLLOWING_SESSION")
        order_at = datetime.combine(target, time(9, 15), SHANGHAI)
        execution_at = datetime.combine(target, time(9, 25), SHANGHAI)
        if not signal_at < order_at:
            raise ValueError("Signal cannot be ordered in the past")

        def unresolved(reason: str) -> ExecutionResult:
            return self._result("EXECUTION_UNRESOLVED", target, order_at,
                                execution_at, reason)

        if (next_day is None or next_day.get("security_id") != security_id
                or next_day.get("trade_date") != target.isoformat()):
            return unresolved("MISSING_NEXT_SESSION_DATA")
        if next_day.get("trading_status") != "TRADING":
            return unresolved("NON_TRADING_OR_SUSPENDED")
        constraint = next_day.get("daily_constraint") or {}
        try:
            if (constraint.get("security_id") != security_id
                    or constraint.get("trade_date") != target.isoformat()
                    or _time(constraint["available_at"]) > order_at
                    or constraint.get("validation_status") != "VALID"
                    or constraint.get("trading_allowed") is not True
                    or constraint.get("suspended") is not False
                    or constraint.get("limit_applicable") is not True
                    or not constraint.get("source_provenance")):
                return unresolved("INVALID_DAILY_CONSTRAINT")
            opening = _decimal(next_day["raw_open"])
            lower = _decimal(constraint["limit_down_price"])
            upper = _decimal(constraint["limit_up_price"])
            tick = _decimal(constraint["price_tick"])
            if (min(opening, lower, upper, tick) <= 0
                    or opening % tick or lower % tick or upper % tick
                    or lower > upper or not lower <= opening <= upper):
                return unresolved("INVALID_OPEN_OR_LIMIT")
        except (KeyError, TypeError, InvalidOperation, ValueError):
            return unresolved("MISSING_OR_INVALID_OPEN_OR_CONSTRAINT")
        if opening == lower:
            return self._result("NO_FILL", target, order_at, execution_at,
                                "OPEN_AT_LIMIT_DOWN")
        return self._result("MODELLED_FILL", target, order_at, execution_at,
                            None, opening)

    def _result(self, status: str, day: date | None, order: datetime | None,
                execution: datetime | None, reason: str | None,
                price: Decimal | None = None) -> ExecutionResult:
        return ExecutionResult(status, day, order, execution, price, reason,
                               self.policy_id, Decimal("0"))


def execution_policy(policy_id: str) -> ExecutionPolicy:
    if POLICY_STATUS.get(policy_id) != "CONTRACT_QUALIFIED":
        raise ValueError(f"Execution policy {policy_id} is NOT_QUALIFIED or unknown")
    if policy_id == "NEXT_SESSION_V1":
        return NextSessionV1()
    if policy_id == "NEXT_SESSION_SELL_V1":
        return NextSessionSellV1()
    raise ValueError(f"Execution policy {policy_id} has no implementation")
