"""Frozen Entry signal outcomes. This module is never imported by signal code."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256


HORIZONS = (1, 3, 5, 10, 20)
MAX_HORIZON = 20


def event_id(security_id: str, entry_date: str, observation_id: str) -> str:
    return sha256(f"{security_id}|{entry_date}|{observation_id}".encode()).hexdigest()


def _positive(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (ValueError, TypeError):
        return None
    return number if number.is_finite() and number > 0 else None


def evaluate_event(
    *, security_id: str, entry_date: str, observation_id: str,
    anchor: object, sessions: tuple[str, ...],
    bars: dict[tuple[str, str], dict[str, object]],
    statuses: dict[tuple[str, str], dict[str, object]],
    actions: dict[str, dict[str, object]],
    exclusions: dict[str, str],
    delisting_date: str | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Hk is the kth exchange session strictly after T; never a modeled fill."""
    price = _positive(anchor)
    if price is None or entry_date not in sessions or entry_date > sessions[-1]:
        raise ValueError("Invalid Entry anchor or exchange-session date")
    if any(day <= entry_date for day in actions | exclusions):
        raise ValueError("Outcome actions must be strictly after Entry")
    identity = event_id(security_id, entry_date, observation_id)
    start = sessions.index(entry_date)
    scale = Decimal(1)
    unresolved_reason = None
    path: list[dict[str, object]] = []
    for h in range(1, MAX_HORIZON + 1):
        if start + h >= len(sessions):
            path.append({"entry_event_id": identity, "horizon": h,
                         "session_date": None, "path_status": "RIGHT_CENSORED",
                         "missing_reason": "SNAPSHOT_CUTOFF", "raw_close": None,
                         "raw_high": None, "raw_low": None,
                         "comparable_close": None, "comparable_high": None,
                         "comparable_low": None, "cumulative_d8_factor": None,
                         "d8_event_kind": None, "d8_source_event_hash": None})
            continue
        day = sessions[start + h]
        action = actions.get(day)
        if action:
            factor = _positive(action.get("single_factor"))
            known = action.get("known_date")
            if (factor is None or not known or str(known) >= day
                    or len(str(action.get("source_event_hash", ""))) != 64):
                unresolved_reason = "INVALID_D8_ACTION"
            else:
                scale *= factor
        if day in exclusions:
            unresolved_reason = f"D8_EXCLUDED:{exclusions[day]}"
        bar = bars.get((security_id, day))
        state = statuses.get((security_id, day))
        raw = {field: _positive(bar.get(field)) if bar else None
               for field in ("close", "high", "low")}
        valid_bar = (state is not None and state.get("is_susp_sec") == False
                     and all(raw.values()) and raw["low"] <= raw["close"] <= raw["high"])
        if unresolved_reason:
            status, missing = "ADJUSTMENT_UNRESOLVED", unresolved_reason
        elif delisting_date and day >= delisting_date:
            status, missing = "TERMINAL_NO_QUOTE", "DELISTED"
        elif state is None:
            status, missing = "OTHER_MISSING", "STATUS_MISSING"
        elif state.get("is_susp_sec") is True:
            status, missing = "NO_VALID_QUOTE", "SUSPENDED"
        elif not valid_bar:
            status, missing = "NO_VALID_QUOTE", (
                "WD_STATUS_NO_QUOTE" if state.get("is_wd_sec") is True else "MISSING_OR_INVALID_BAR")
        else:
            status, missing = "AVAILABLE", None
        result = {"entry_event_id": identity, "horizon": h, "session_date": day,
                  "path_status": status, "missing_reason": missing,
                  "raw_close": float(raw["close"]) if raw["close"] else None,
                  "raw_high": float(raw["high"]) if raw["high"] else None,
                  "raw_low": float(raw["low"]) if raw["low"] else None,
                  "comparable_close": None, "comparable_high": None,
                  "comparable_low": None,
                  "cumulative_d8_factor": float(scale) if not unresolved_reason else None,
                  "d8_event_kind": action.get("event_kind") if action else None,
                  "d8_source_event_hash": action.get("source_event_hash") if action else None,
                  "withdrawal_status": state.get("is_wd_sec") if state else None}
        if status == "AVAILABLE":
            for field in ("close", "high", "low"):
                result[f"comparable_{field}"] = float(raw[field] * scale)
        path.append(result)
    outcomes: dict[str, object] = {"entry_event_id": identity,
                                   "signal_anchor_close": float(price)}
    for h in HORIZONS:
        window = path[:h]
        endpoint = window[-1]
        censored = endpoint["path_status"] == "RIGHT_CENSORED"
        unresolved = any(row["path_status"] == "ADJUSTMENT_UNRESOLVED" for row in window)
        valid = [] if censored or unresolved else [row for row in window if row["path_status"] == "AVAILABLE"]
        top = max(valid, key=lambda row: row["comparable_high"]) if valid else None
        bottom = min(valid, key=lambda row: row["comparable_low"]) if valid else None
        prefix = f"h{h}_"
        outcomes.update({
            prefix + "session_date": endpoint["session_date"],
            prefix + "outcome_status": endpoint["path_status"],
            prefix + "missing_reason": endpoint["missing_reason"],
            prefix + "raw_future_close": endpoint["raw_close"],
            prefix + "comparable_close": endpoint["comparable_close"],
            prefix + "signal_forward_close_return":
                endpoint["comparable_close"] / float(price) - 1
                if endpoint["path_status"] == "AVAILABLE" else None,
            prefix + "signal_forward_mfe": top["comparable_high"] / float(price) - 1 if top else None,
            prefix + "signal_forward_mae": bottom["comparable_low"] / float(price) - 1 if bottom else None,
            prefix + "mfe_source_date": top["session_date"] if top else None,
            prefix + "mae_source_date": bottom["session_date"] if bottom else None,
            prefix + "mfe_source_session": top["horizon"] if top else None,
            prefix + "mae_source_session": bottom["horizon"] if bottom else None,
            prefix + "valid_session_count": len(valid),
        })
    return outcomes, path
