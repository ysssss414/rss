"""Sequential, outcome-only long exit simulation for frozen Entry signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Mapping, Sequence


TICK = Decimal("0.01")


def money(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Price or adjustment must be finite and positive")
    return result


def on_tick(value: Decimal) -> bool:
    return value == value.quantize(TICK)


def upper_tick(value: Decimal) -> Decimal:
    return (value / TICK).to_integral_value(rounding=ROUND_CEILING) * TICK


def lower_tick(value: Decimal) -> Decimal:
    return (value / TICK).to_integral_value(rounding=ROUND_FLOOR) * TICK


@dataclass(frozen=True)
class Session:
    day: str
    status: str  # TRADING, SUSPENDED, UNKNOWN, TERMINAL
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    lower_limit: Decimal | None = None
    d8_single_factor: Decimal = Decimal(1)
    d8_exclusion_reason: str | None = None
    d8_event_kind: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"TRADING", "SUSPENDED", "UNKNOWN", "TERMINAL"}:
            raise ValueError("Unsupported session status")
        if self.status == "TRADING":
            if (any(value is None or not value.is_finite() or value <= 0
                    for value in (self.open, self.high, self.low, self.close))
                    or not self.low <= self.open <= self.high
                    or not self.low <= self.close <= self.high
                    or any(not on_tick(value) for value in (self.open, self.high, self.low, self.close))):
                raise ValueError("Invalid raw trading OHLC or tick")
        if not self.d8_single_factor.is_finite() or self.d8_single_factor <= 0:
            raise ValueError("Invalid D8 factor")


def buy_fill(signal_close: Decimal, *, fixed_bps: Decimal = Decimal(0),
             fixed_ticks: int = 0) -> Decimal:
    """Adverse buy-side slippage interface; baseline uses zero and equals close."""
    if fixed_bps < 0 or fixed_ticks < 0 or not on_tick(signal_close):
        raise ValueError("Invalid buy fill assumption")
    return upper_tick(signal_close * (1 + fixed_bps / 10000) + TICK * fixed_ticks)


def _one_price_limit_down(session: Session) -> bool:
    return (session.status == "TRADING" and session.lower_limit is not None
            and session.lower_limit.is_finite() and session.lower_limit > 0
            and session.open == session.high == session.low == session.close == session.lower_limit)


def _trace(session: Session, index: int, scale: Decimal, decision: str,
           *, target: Decimal | None = None, stop: Decimal | None = None) -> dict[str, object]:
    return {"session_index": index, "date": session.day, "status": session.status,
            "raw_open": float(session.open) if session.open is not None else None,
            "raw_high": float(session.high) if session.high is not None else None,
            "raw_low": float(session.low) if session.low is not None else None,
            "raw_close": float(session.close) if session.close is not None else None,
            "comparable_factor": float(scale),
            "raw_target_tick": float(target) if target is not None else None,
            "raw_stop_tick": float(stop) if stop is not None else None,
            "d8_event_kind": session.d8_event_kind,
            "d8_exclusion_reason": session.d8_exclusion_reason,
            "decision": decision}


def simulate(
    *, entry_date: str, fill_price: Decimal, sessions: Sequence[Session],
    contract: Mapping[str, object], costs: Mapping[str, object],
) -> dict[str, object]:
    """Advance one exchange session at a time; stop reading after the modeled exit."""
    if not on_tick(fill_price) or fill_price <= 0:
        raise ValueError("Fill price must be positive and on tick")
    if any(s.day <= entry_date for s in sessions) or tuple(s.day for s in sessions) != tuple(
            sorted({s.day for s in sessions})):
        raise ValueError("Sessions must be unique, ordered and strictly after Entry T")
    timeout = contract["timeout_exchange_sessions"]
    if type(timeout) is not int or timeout not in {1, 3, 5}:
        raise ValueError("Unregistered timeout")
    tp = Decimal(str(contract["take_profit"])) if contract["take_profit"] is not None else None
    sl = Decimal(str(contract["stop_loss"])) if contract["stop_loss"] is not None else None
    if tp is not None and not 0 < tp < 1 or sl is not None and not 0 < sl < 1:
        raise ValueError("Invalid pre-registered threshold")
    scale = Decimal(1)
    prefix_high = prefix_low = fill_price
    pending = None
    blocked_limit_down = suspended_days = timeout_blocked = 0
    trace: list[dict[str, object]] = []
    exit_price = exit_date = exit_reason = None
    exit_index = None
    same_bar = False
    gap = None
    excursion_basis = None
    status = "OPEN_AT_SNAPSHOT_CUTOFF"
    for index, session in enumerate(sessions, 1):
        if session.d8_exclusion_reason:
            trace.append(_trace(session, index, scale, "ADJUSTMENT_UNRESOLVED"))
            status = "ADJUSTMENT_UNRESOLVED"
            break
        scale *= session.d8_single_factor
        if session.status == "TERMINAL":
            trace.append(_trace(session, index, scale, "TERMINAL_NO_LIQUIDATION_VALUE"))
            status = "TERMINAL_UNRESOLVED"
            break
        if session.status == "UNKNOWN":
            trace.append(_trace(session, index, scale, "QUOTE_OR_STATUS_UNKNOWN"))
            status = "PATH_UNRESOLVED"
            break
        if session.status == "SUSPENDED":
            suspended_days += 1
            if index >= timeout and pending is None:
                pending = "TIMEOUT"
                timeout_blocked += 1
                decision = "TIME_EXIT_BLOCKED_SUSPENDED"
            else:
                decision = "SUSPENDED_CARRY"
            trace.append(_trace(session, index, scale, decision))
            continue
        raw_target = upper_tick(fill_price * (1 + tp) / scale) if tp is not None else None
        raw_stop = lower_tick(fill_price * (1 - sl) / scale) if sl is not None else None
        if _one_price_limit_down(session):
            if pending or raw_stop is not None and session.low <= raw_stop or index >= timeout:
                blocked_limit_down += 1
                if pending is None:
                    pending = "LIMIT_DOWN_STOP" if raw_stop is not None and session.low <= raw_stop else "TIMEOUT"
                if index >= timeout:
                    timeout_blocked += 1
                decision = "EXIT_BLOCKED_BY_LIMIT_DOWN"
            else:
                decision = "ONE_PRICE_LIMIT_DOWN_NO_EXIT_TRIGGER"
            prefix_high = max(prefix_high, session.high * scale)
            prefix_low = min(prefix_low, session.low * scale)
            trace.append(_trace(session, index, scale, decision,
                                target=raw_target, stop=raw_stop))
            continue
        if pending:
            exit_price, exit_reason = session.open, (
                "DEFERRED_LIMIT_DOWN_OPEN" if pending == "LIMIT_DOWN_STOP" else "DEFERRED_TIMEOUT_OPEN")
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_OPEN"
        elif index < timeout and tp is None and sl is None:
            trace.append(_trace(session, index, scale, "HOLD_UNTIL_TIME_EXIT",
                                target=raw_target, stop=raw_stop))
            prefix_high = max(prefix_high, session.high * scale)
            prefix_low = min(prefix_low, session.low * scale)
            continue
        elif raw_stop is not None and session.open <= raw_stop:
            exit_price, exit_reason, gap = session.open, "SL_GAP_OPEN", "GAP_DOWN_THROUGH_STOP"
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_OPEN"
        elif raw_target is not None and session.open >= raw_target:
            exit_price, exit_reason, gap = session.open, "TP_GAP_OPEN", "GAP_UP_THROUGH_TARGET"
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_OPEN"
        elif raw_stop is not None and session.low <= raw_stop and raw_target is not None and session.high >= raw_target:
            exit_price, exit_reason = raw_stop, "SAME_BAR_AMBIGUOUS_STOP_FIRST"
            same_bar = True
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_PRINT_BOUND"
        elif raw_stop is not None and session.low <= raw_stop:
            exit_price, exit_reason = raw_stop, "SL_INTRADAY"
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_PRINT_BOUND"
        elif raw_target is not None and session.high >= raw_target:
            exit_price, exit_reason = raw_target, "TP_INTRADAY"
            excursion_basis = "PRIOR_FULL_SESSIONS_PLUS_EXIT_PRINT_BOUND"
        elif index >= timeout:
            exit_price, exit_reason = session.close, "TIMEOUT_CLOSE"
            prefix_high = max(prefix_high, session.high * scale)
            prefix_low = min(prefix_low, session.low * scale)
            excursion_basis = "FULL_SESSIONS_THROUGH_EXIT_CLOSE"
        else:
            prefix_high = max(prefix_high, session.high * scale)
            prefix_low = min(prefix_low, session.low * scale)
            trace.append(_trace(session, index, scale, "NO_EXIT_TRIGGER",
                                target=raw_target, stop=raw_stop))
            continue
        comparable_exit = exit_price * scale
        prefix_high = max(prefix_high, comparable_exit)
        prefix_low = min(prefix_low, comparable_exit)
        exit_date, exit_index, status = session.day, index, "CLOSED"
        trace.append(_trace(session, index, scale, exit_reason,
                            target=raw_target, stop=raw_stop))
        break
    result: dict[str, object] = {
        "outcome_status": status, "exit_date": exit_date,
        "exit_price_raw": float(exit_price) if exit_price is not None else None,
        "exit_price_comparable": float(exit_price * scale) if exit_price is not None else None,
        "exit_reason": exit_reason, "holding_sessions": exit_index,
        "same_bar_ambiguity": same_bar, "gap_handling": gap,
        "suspension_handling": "DEFERRED_OR_CARRIED" if suspended_days else "NONE",
        "suspended_session_count": suspended_days,
        "limit_down_blocked_count": blocked_limit_down,
        "timeout_blocked_count": timeout_blocked,
        "pre_exit_excursion_basis": excursion_basis,
        "pre_exit_mfe": None, "pre_exit_mae": None,
        "mfe_capture_ratio": None, "gross_price_return": None,
        "transaction_cost": None, "net_price_return": None,
        "decision_trace": trace,
    }
    if status == "CLOSED":
        gross = exit_price * scale / fill_price - 1
        buy_bps = Decimal(str(costs["buy_commission_bps"])) + Decimal(str(costs["buy_transfer_bps"]))
        sell_bps = (Decimal(str(costs["sell_commission_bps"]))
                    + Decimal(str(costs["sell_transfer_bps"]))
                    + Decimal(str(costs["sell_stamp_duty_bps"])))
        fee = buy_bps / 10000 + (1 + gross) * sell_bps / 10000
        mfe = prefix_high / fill_price - 1
        mae = prefix_low / fill_price - 1
        result.update({"pre_exit_mfe": float(mfe), "pre_exit_mae": float(mae),
                       "mfe_capture_ratio": float(gross / mfe) if mfe > 0 else None,
                       "gross_price_return": float(gross), "transaction_cost": float(fee),
                       "net_price_return": float(gross - fee)})
    return result
