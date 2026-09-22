"""Offline Gate C contract checks. No provider calls or strategy calculations."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_gate_c"


def load(name: str) -> dict:
    return json.loads((ART / name).read_bytes())


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include an offset")
    return parsed


def _available(value: str | None, cutoff: datetime) -> bool:
    try:
        return value is not None and _time(value) <= cutoff
    except (TypeError, ValueError):
        return False


def classify_security_day(row: dict, *, minimum_history_required: int) -> dict:
    """Classify one normalized, frozen security-day; no market data are fetched."""
    def exclude(reason: str) -> dict:
        return {"eligible": False, "exclusion_reason": reason}

    if type(minimum_history_required) is not int or minimum_history_required < 0:
        return exclude("DATA_CONFLICT")
    try:
        day = date.fromisoformat(row["trade_date"])
        cutoff = _time(row["decision_at"])
    except (KeyError, TypeError, ValueError):
        return exclude("DATA_CONFLICT")
    if cutoff.date() != day:
        return exclude("DATA_CONFLICT")
    if (not row.get("security_id") or not row.get("data_snapshot_id")
            or row.get("execution_policy_id") != "NEXT_SESSION_V1"
            or row.get("universe_contract_version") != "1"):
        return exclude("DATA_CONFLICT")
    if not _available(row.get("security_type_available_at"), cutoff):
        return exclude("MISSING_PIT_EVIDENCE")
    if row.get("security_type") != "A_SHARE_COMMON_STOCK":
        return exclude("UNSUPPORTED_SECURITY_TYPE")

    if not row.get("lifecycle_coverage_verified") or not _available(
        row.get("lifecycle_coverage_available_at"), cutoff
    ):
        return exclude("UNKNOWN_LISTING_LIFECYCLE")
    try:
        events = [
            event for event in row.get("lifecycle_events", [])
            if date.fromisoformat(event["effective_date"]) <= day
            and _available(event.get("available_at"), cutoff)
        ]
    except (KeyError, TypeError, ValueError):
        return exclude("DATA_CONFLICT")
    if not events:
        return exclude("NOT_LISTED")
    events.sort(key=lambda event: (event["effective_date"], event["available_at"]))
    if any(
        left["effective_date"] == right["effective_date"] and left["kind"] != right["kind"]
        for left, right in zip(events, events[1:])
    ):
        return exclude("DATA_CONFLICT")
    state = events[-1]["kind"]
    if state == "DELISTED":
        return exclude("DELISTED")
    if state not in {"LISTED", "RELISTED"}:
        return exclude("UNKNOWN_LISTING_LIFECYCLE")

    if not _available(row.get("trading_status_available_at"), cutoff):
        return exclude("MISSING_PIT_EVIDENCE")
    if row.get("trading_status") == "SUSPENDED":
        return exclude("SUSPENDED")
    if row.get("trading_status") == "NON_TRADING":
        return exclude("NON_TRADING")
    if row.get("trading_status") != "TRADING":
        return exclude("UNKNOWN_TRADING_STATUS")

    constraint = row.get("daily_constraint") or {}
    if not _available(constraint.get("available_at"), cutoff):
        return exclude("MISSING_PIT_EVIDENCE")
    if constraint.get("validation_status") != "VALID":
        return exclude("INVALID_DAILY_CONSTRAINT")
    if not constraint.get("limit_applicable"):
        return exclude("NO_PRICE_LIMIT")
    try:
        up_rate = Decimal(str(constraint["limit_up_rate"]))
        down_rate = Decimal(str(constraint["limit_down_rate"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return exclude("INVALID_DAILY_CONSTRAINT")
    if not up_rate.is_finite() or not down_rate.is_finite() or up_rate <= 0 or down_rate <= 0:
        return exclude("INVALID_DAILY_CONSTRAINT")
    if up_rate != Decimal("0.10") or down_rate != Decimal("0.10"):
        return exclude("NON_10_PERCENT_REGIME")

    if not row.get("bar_present") or not _available(row.get("bar_available_at"), cutoff):
        return exclude("MISSING_BAR")
    if not _available(row.get("history_available_at"), cutoff):
        return exclude("MISSING_PIT_EVIDENCE")
    if row.get("history_basis") != "SINCE_ACTIVE_LISTING":
        return exclude("DATA_CONFLICT")
    if type(row.get("valid_history_sessions")) is not int or row["valid_history_sessions"] < 0:
        return exclude("DATA_CONFLICT")
    if row["valid_history_sessions"] < minimum_history_required:
        return exclude("INSUFFICIENT_HISTORY")
    return {"eligible": True, "exclusion_reason": None}


def validate_next_session_timeline(*, trade_date: str, next_session_date: str,
                                   verified_next_session_date: str,
                                   signal_at: str, order_at: str, execution_at: str,
                                   input_available_at: str) -> None:
    """Reject a final-T-bar signal executed at the same T close."""
    signal, order, execution = map(_time, (signal_at, order_at, execution_at))
    if not (date.fromisoformat(trade_date) < date.fromisoformat(next_session_date)):
        raise ValueError("Next session must follow the signal day")
    if next_session_date != verified_next_session_date:
        raise ValueError("Next session disagrees with the frozen trading calendar")
    if signal.date() != date.fromisoformat(trade_date) or not _available(input_available_at, signal):
        raise ValueError("Signal precedes final input availability")
    if order.date() != date.fromisoformat(next_session_date) or execution.date() != order.date():
        raise ValueError("Order and execution must be in the next session")
    if order.strftime("%H:%M:%S") != "09:15:00" or execution.strftime("%H:%M:%S") != "09:25:00":
        raise ValueError("NEXT_SESSION_V1 uses the next opening auction")
    if not signal < order < execution:
        raise ValueError("Invalid signal/order/execution sequence")


def classify_daily_open_fill(*, trading_allowed: bool, constraint_valid: bool,
                             limit_applicable: bool, opening_price: str | None,
                             limit_up_price: str | None) -> dict:
    """Conservative daily-data benchmark; this is not a broker fill replay."""
    if not trading_allowed:
        return {"status": "EXECUTION_UNRESOLVED", "price": None}
    if not constraint_valid or not limit_applicable or opening_price is None or limit_up_price is None:
        return {"status": "EXECUTION_UNRESOLVED", "price": None}
    try:
        opening, upper = Decimal(opening_price), Decimal(limit_up_price)
    except (TypeError, ValueError, InvalidOperation):
        return {"status": "EXECUTION_UNRESOLVED", "price": None}
    if not opening.is_finite() or not upper.is_finite() or opening <= 0 or upper <= 0 or opening > upper:
        return {"status": "EXECUTION_UNRESOLVED", "price": None}
    if opening == upper:
        return {"status": "NO_FILL", "price": None}
    return {"status": "MODELLED_FILL", "price": opening_price}


def verify_manifest() -> int:
    hashes = load("qualification_manifest.json")["evidence_hashes"]
    for relative, expected in hashes.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"Manifest mismatch: {relative}")
    return len(hashes)


def validate() -> dict:
    policy = load("execution_policy_contract.json")
    universe = load("historical_universe_contract.json")
    cases = load("universe_validation_cases.json")["cases"]
    if policy["recommended_baseline"] != "NEXT_SESSION_V1":
        raise AssertionError("No causal daily-data baseline")
    if any(policy["policies"][name]["qualification"] != "NOT_QUALIFIED"
           for name in ("CLOSING_AUCTION_V1", "PRE_CLOSE_SNAPSHOT_V1")):
        raise AssertionError("Unverified intraday policy admitted")
    if universe["history_requirement"] != "DEPENDENCY_DRIVEN" or universe["daily_regime_source"] != "Gate B DailyTradingConstraint":
        raise AssertionError("Universe contract baseline changed")
    minimum = load("universe_validation_cases.json")["minimum_history_required"]
    if not cases or any(classify_security_day(case, minimum_history_required=minimum) != case["expected"]
                        for case in cases):
        raise AssertionError("Universe fixture mismatch")
    return {"C1": "CLOSED", "C2": "CLOSED", "gate_c": "GATE_C_CONTRACT_PASS",
            "cases": len(cases), "manifest_hashes": verify_manifest()}


if __name__ == "__main__":
    print(json.dumps(validate(), sort_keys=True))
