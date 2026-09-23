"""Strategy-agnostic Stage 1 position lifecycle; no entry/exit rule or PnL."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256

from .entry_signals import EntrySignal
from .foundation import ExecutionResult, SHANGHAI
from .indicator_prices import PRICE_BASIS


LIFECYCLE_VERSION = "TRADE_LIFECYCLE_V1"
LIFECYCLE_STATES = {
    "NO_POSITION",
    "ENTRY_PENDING",
    "OPEN",
    "EXIT_PENDING",
    "CLOSED",
    "ENTRY_FAILED",
    "EXIT_UNRESOLVED",
}
EXECUTION_STATUSES = {"MODELLED_FILL", "NO_FILL", "EXECUTION_UNRESOLVED"}
POSITION_EVENT_TYPES = {
    "SPLIT",
    "DIVIDEND",
    "RIGHTS",
    "SECURITY_CODE_CHANGE",
    "DELISTING",
    "RELISTING",
    "LONG_SUSPENSION",
    "TRADING_LIFECYCLE_TERMINATION",
}


@dataclass(frozen=True)
class ExitSignal:
    """Externally supplied exit intent; this module never creates one."""

    position_id: str
    security_id: str
    signal_date: date
    signal_at: datetime
    latest_input_at: datetime
    signal_reasons: tuple[str, ...]
    data_snapshot_id: str
    strategy_contract_versions: tuple[str, ...]
    execution_policy_id: str = "NEXT_SESSION_SELL_V1"

    def __post_init__(self) -> None:
        if (not self.position_id or not self.security_id or not self.data_snapshot_id
                or not self.signal_reasons or not self.strategy_contract_versions
                or self.execution_policy_id != "NEXT_SESSION_SELL_V1"
                or self.signal_at.utcoffset() is None
                or self.latest_input_at.utcoffset() is None
                or self.signal_at.astimezone(SHANGHAI).date() != self.signal_date
                or self.latest_input_at.astimezone(SHANGHAI).date() != self.signal_date
                or self.latest_input_at.astimezone(SHANGHAI).time() < time(15)
                or self.latest_input_at > self.signal_at):
            raise ValueError("Invalid causal T EOD exit signal")


@dataclass(frozen=True)
class Position:
    position_id: str
    security_id: str
    entry_signal_date: date
    entry_execution_date: date
    entry_execution_price: Decimal
    entry_signal_reasons: tuple[str, ...]
    observation_instance_id: str
    execution_policy_id: str
    data_snapshot_id: str
    strategy_contract_versions: tuple[str, ...]
    indicator_price_basis: str
    factor_source_hash: str
    quantity_semantics: str = "ONE_BASELINE_POSITION_NO_QUANTITY_OR_LOTS"
    exit_attempt_count: int = 0


@dataclass(frozen=True)
class ClosedTrade:
    trade_id: str
    position_id: str
    security_id: str
    entry_signal_date: date
    entry_execution_date: date
    entry_execution_price: Decimal
    exit_signal_date: date
    exit_execution_date: date
    exit_execution_price: Decimal
    entry_signal_reasons: tuple[str, ...]
    exit_signal_reasons: tuple[str, ...]
    entry_execution_policy_id: str
    exit_execution_policy_id: str
    entry_data_snapshot_id: str
    exit_data_snapshot_id: str
    observation_instance_id: str
    strategy_contract_versions: tuple[str, ...]
    raw_execution_price_lineage: str = "OFFICIAL_RAW_OPEN"
    indicator_price_lineage: str = PRICE_BASIS
    quantity_semantics: str = "ONE_BASELINE_POSITION_NO_QUANTITY_OR_LOTS"


@dataclass(frozen=True)
class LifecycleIssue:
    code: str
    policy_id: str | None
    event_date: date | None
    detail: str | None
    data_snapshot_id: str | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class LifecycleEvent:
    event_type: str
    event_at: datetime
    reason: str | None
    reference_id: str | None


@dataclass(frozen=True)
class PositionEvent:
    security_id: str
    event_type: str
    effective_date: date
    available_at: datetime
    data_snapshot_id: str
    source_reference: str


@dataclass(frozen=True)
class TradeLifecycle:
    security_id: str
    state: str = "NO_POSITION"
    position: Position | None = None
    pending_entry_signal: EntrySignal | None = None
    pending_exit_signal: ExitSignal | None = None
    closed_trades: tuple[ClosedTrade, ...] = ()
    position_events: tuple[PositionEvent, ...] = ()
    issue: LifecycleIssue | None = None
    events: tuple[LifecycleEvent, ...] = ()
    contract_version: str = LIFECYCLE_VERSION

    def __post_init__(self) -> None:
        if (not self.security_id or self.state not in LIFECYCLE_STATES
                or self.contract_version != LIFECYCLE_VERSION):
            raise ValueError("Invalid trade lifecycle identity")


def new_lifecycle(security_id: str) -> TradeLifecycle:
    return TradeLifecycle(security_id)


def submit_entry(lifecycle: TradeLifecycle, signal: EntrySignal) -> TradeLifecycle:
    _validate_entry_signal(signal)
    if lifecycle.position is not None:
        return _with_event(
            lifecycle, "IGNORED_WHILE_POSITION_OPEN", signal.signal_at,
            "ONE_BASELINE_POSITION_PER_SECURITY", signal.observation_instance_id,
        )
    if lifecycle.state == "ENTRY_PENDING":
        raise ValueError("An entry execution is already pending")
    if lifecycle.state not in {"NO_POSITION", "ENTRY_FAILED", "CLOSED"}:
        raise ValueError("Lifecycle cannot accept an entry signal")
    return _with_event(
        replace(lifecycle, state="ENTRY_PENDING", pending_entry_signal=signal,
                pending_exit_signal=None, issue=None),
        "ENTRY_SIGNAL_ACCEPTED", signal.signal_at, None, signal.observation_instance_id,
    )


def resolve_entry(lifecycle: TradeLifecycle, result: ExecutionResult) -> TradeLifecycle:
    signal = lifecycle.pending_entry_signal
    if lifecycle.state != "ENTRY_PENDING" or signal is None:
        raise ValueError("No entry execution is pending")
    _validate_execution(result, signal.execution_policy_id, signal.signal_date)
    event_at = result.execution_at or signal.signal_at
    if result.status != "MODELLED_FILL":
        issue = LifecycleIssue(
            result.status, result.policy_id, result.execution_date, result.reason,
        )
        return _with_event(
            replace(lifecycle, state="ENTRY_FAILED", pending_entry_signal=None,
                    issue=issue),
            result.status, event_at, result.reason, signal.observation_instance_id,
        )
    versions = tuple(dict.fromkeys((
        signal.observation_contract_version,
        *signal.entry_contract_versions,
        *signal.primitive_versions,
        LIFECYCLE_VERSION,
    )))
    identity = "|".join((
        lifecycle.security_id,
        signal.observation_instance_id,
        signal.signal_date.isoformat(),
        result.execution_date.isoformat(),
        str(result.execution_price),
        result.policy_id,
    ))
    position = Position(
        sha256(identity.encode()).hexdigest(), lifecycle.security_id,
        signal.signal_date, result.execution_date, result.execution_price,
        signal.signal_reasons, signal.observation_instance_id, result.policy_id,
        signal.data_snapshot_id, versions, PRICE_BASIS, signal.factor_source_hash,
    )
    return _with_event(
        replace(lifecycle, state="OPEN", position=position,
                pending_entry_signal=None, issue=None),
        "POSITION_OPENED", event_at, None, position.position_id,
    )


def submit_exit(lifecycle: TradeLifecycle, signal: ExitSignal) -> TradeLifecycle:
    position = lifecycle.position
    if lifecycle.state != "OPEN" or position is None:
        raise ValueError("An OPEN position is required for an exit signal")
    if (signal.security_id != lifecycle.security_id
            or signal.position_id != position.position_id
            or signal.signal_date < position.entry_execution_date):
        raise ValueError("Exit signal disagrees with the open position")
    position = replace(position, exit_attempt_count=position.exit_attempt_count + 1)
    return _with_event(
        replace(lifecycle, state="EXIT_PENDING", position=position,
                pending_exit_signal=signal, issue=None),
        "EXIT_SIGNAL_ACCEPTED", signal.signal_at, None, position.position_id,
    )


def resolve_exit(lifecycle: TradeLifecycle, result: ExecutionResult) -> TradeLifecycle:
    signal = lifecycle.pending_exit_signal
    position = lifecycle.position
    if (lifecycle.state not in {"EXIT_PENDING", "EXIT_UNRESOLVED"}
            or signal is None or position is None):
        raise ValueError("No exit execution is pending")
    _validate_execution(result, signal.execution_policy_id, signal.signal_date)
    event_at = result.execution_at or signal.signal_at
    if result.status == "NO_FILL":
        return _with_event(
            replace(lifecycle, state="OPEN", pending_exit_signal=None, issue=None),
            "EXIT_NO_FILL_POSITION_REMAINS_OPEN", event_at, result.reason,
            position.position_id,
        )
    if result.status == "EXECUTION_UNRESOLVED":
        issue = LifecycleIssue(
            result.status, result.policy_id, result.execution_date, result.reason,
        )
        return _with_event(
            replace(lifecycle, state="EXIT_UNRESOLVED", issue=issue),
            "EXIT_EXECUTION_UNRESOLVED", event_at, result.reason,
            position.position_id,
        )
    versions = tuple(dict.fromkeys((
        *position.strategy_contract_versions,
        *signal.strategy_contract_versions,
        LIFECYCLE_VERSION,
    )))
    identity = "|".join((
        position.position_id,
        signal.signal_date.isoformat(),
        result.execution_date.isoformat(),
        str(result.execution_price),
        result.policy_id,
    ))
    closed = ClosedTrade(
        sha256(identity.encode()).hexdigest(), position.position_id,
        position.security_id, position.entry_signal_date,
        position.entry_execution_date, position.entry_execution_price,
        signal.signal_date, result.execution_date, result.execution_price,
        position.entry_signal_reasons, signal.signal_reasons,
        position.execution_policy_id, result.policy_id,
        position.data_snapshot_id, signal.data_snapshot_id,
        position.observation_instance_id, versions,
    )
    return _with_event(
        replace(lifecycle, state="CLOSED", position=None,
                pending_exit_signal=None, closed_trades=(*lifecycle.closed_trades, closed),
                issue=None),
        "TRADE_STRUCTURALLY_CLOSED", event_at, None, closed.trade_id,
    )


def record_position_event(
    lifecycle: TradeLifecycle,
    event: PositionEvent,
    *,
    as_of_at: datetime,
) -> TradeLifecycle:
    """Fail closed on position-affecting corporate/trading lifecycle evidence."""
    position = lifecycle.position
    if lifecycle.state not in {"OPEN", "EXIT_PENDING", "EXIT_UNRESOLVED"} or position is None:
        raise ValueError("A live economic position is required")
    if (event.security_id != position.security_id
            or event.event_type not in POSITION_EVENT_TYPES
            or not event.data_snapshot_id or not event.source_reference
            or event.available_at.utcoffset() is None or as_of_at.utcoffset() is None
            or event.available_at > as_of_at
            or event.effective_date > as_of_at.astimezone(SHANGHAI).date()):
        raise ValueError("Invalid or future position event")
    corporate = {"SPLIT", "DIVIDEND", "RIGHTS", "SECURITY_CODE_CHANGE"}
    code = ("POSITION_CORPORATE_ACTION_UNRESOLVED"
            if event.event_type in corporate
            else "POSITION_TRADING_LIFECYCLE_UNRESOLVED")
    issue = LifecycleIssue(
        code, None, event.effective_date, event.event_type,
        event.data_snapshot_id, event.source_reference,
    )
    return _with_event(
        replace(lifecycle, state="EXIT_UNRESOLVED", issue=issue,
                position_events=(*lifecycle.position_events, event)),
        code, event.available_at, event.event_type, position.position_id,
    )


def _validate_entry_signal(signal: EntrySignal) -> None:
    if (not signal.observation_instance_id or not signal.data_snapshot_id
            or not signal.signal_reasons or signal.execution_policy_id != "NEXT_SESSION_V1"
            or signal.signal_at.utcoffset() is None
            or signal.signal_at.astimezone(SHANGHAI).date() != signal.signal_date
            or signal.signal_at.astimezone(SHANGHAI).time() < time(15)
            or len(signal.factor_source_hash) != 64
            or any(char not in "0123456789abcdef" for char in signal.factor_source_hash)):
        raise ValueError("Invalid causal entry signal")


def _validate_execution(
    result: ExecutionResult,
    expected_policy: str,
    signal_date: date,
) -> None:
    if result.status not in EXECUTION_STATUSES or result.policy_id != expected_policy:
        raise ValueError("Execution result disagrees with the pending signal")
    if result.status == "MODELLED_FILL":
        if (result.execution_date is None or result.order_at is None
                or result.execution_at is None or result.execution_date <= signal_date
                or result.execution_price is None
                or not result.execution_price.is_finite()
                or result.execution_price <= 0):
            raise ValueError("Invalid modelled fill")
    elif result.execution_price is not None:
        raise ValueError("Non-fill execution cannot carry a price")


def _with_event(
    lifecycle: TradeLifecycle,
    event_type: str,
    event_at: datetime,
    reason: str | None,
    reference_id: str | None,
) -> TradeLifecycle:
    if event_at.utcoffset() is None:
        raise ValueError("Lifecycle event time needs an offset")
    event = LifecycleEvent(event_type, event_at, reason, reference_id)
    return replace(lifecycle, events=(*lifecycle.events, event))
