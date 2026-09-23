"""Fail-closed building blocks for a frozen real-data Stage 1 snapshot.

These functions validate supplied records; they never fetch or infer missing
exchange data. In particular, a valid vendor price is not a qualified daily
trading constraint.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class IdentityDay:
    security_id: str
    trade_date: date
    security_type: str | None
    exchange: str | None
    listed: bool | None
    listing_date: date | None
    latest_listing_or_relisting_date: date | None
    lifecycle_active: bool | None
    delisted: bool | None
    lifecycle_end_date: date | None
    identity_status: str
    source: str
    snapshot_id: str


@dataclass(frozen=True)
class IdentityInterval:
    effective_from: date
    effective_to: date  # inclusive exchange session
    identity: IdentityDay


def max_full_horizon_signal_day(sessions: Sequence[date], data_end: date, horizon: int = 20) -> date:
    """T+1 is D0; the Hth session after T is the last outcome day."""
    ordered = tuple(day for day in sessions if day <= data_end)
    if (type(horizon) is not int or horizon < 1 or
            ordered != tuple(sorted(set(ordered))) or len(ordered) <= horizon):
        raise ValueError("Incomplete or unordered frozen calendar")
    return ordered[-horizon - 1]


def compress_identity_days(days: Sequence[IdentityDay], sessions: Sequence[date]) -> tuple[IdentityInterval, ...]:
    """Compress only a complete security x session grid; never bridge gaps."""
    calendar = tuple(sessions)
    if not calendar or calendar != tuple(sorted(set(calendar))):
        raise ValueError("Invalid frozen calendar")
    by_security: dict[str, dict[date, IdentityDay]] = {}
    for row in days:
        if not row.security_id or not row.source or not row.snapshot_id or row.trade_date not in calendar:
            raise ValueError("Incomplete identity provenance")
        partition = by_security.setdefault(row.security_id, {})
        if row.trade_date in partition:
            raise ValueError("Duplicate identity security-day")
        partition[row.trade_date] = row
    intervals = []
    for security_id, rows in sorted(by_security.items()):
        if set(rows) != set(calendar):
            raise ValueError(f"Missing identity security-day: {security_id}")
        first = last = calendar[0]
        template = rows[first]
        for day in calendar[1:]:
            row = rows[day]
            if replace(row, trade_date=first) != template:
                intervals.append(IdentityInterval(first, last, template))
                first, template = day, row
            last = day
        intervals.append(IdentityInterval(first, last, template))
    return tuple(intervals)


def expand_identity_intervals(intervals: Sequence[IdentityInterval], sessions: Sequence[date]) -> tuple[IdentityDay, ...]:
    calendar = tuple(sessions)
    rows = []
    for interval in intervals:
        if interval.effective_from > interval.effective_to:
            raise ValueError("Reversed identity interval")
        rows.extend(replace(interval.identity, trade_date=day) for day in calendar
                    if interval.effective_from <= day <= interval.effective_to)
    result = tuple(sorted(rows, key=lambda row: (row.security_id, row.trade_date)))
    if compress_identity_days(result, calendar) != tuple(intervals):
        raise ValueError("Non-reversible identity intervals")
    return result


def bar_validation_status(row: Mapping[str, object]) -> str:
    """Return BAR_VALID or BAR_INVALID without dropping unusual vendor rows."""
    try:
        values = {key: Decimal(str(row[key])) for key in
                  ("open", "high", "low", "close", "volume", "amount")}
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return "BAR_INVALID"
    if (not all(value.is_finite() for value in values.values())
            or min(values[key] for key in ("open", "high", "low", "close")) <= 0
            or values["high"] < max(values["open"], values["close"])
            or values["low"] > min(values["open"], values["close"])
            or values["high"] < values["low"]
            or values["volume"] < 0 or values["amount"] < 0):
        return "BAR_INVALID"
    return "BAR_VALID"


def audit_bar_snapshot(rows: Sequence[Mapping[str, object]],
                       expected_keys: set[tuple[str, date]]) -> dict[str, int]:
    """Account for every expected key; duplicates are conflicts, not first wins."""
    grouped: dict[tuple[str, date], list[Mapping[str, object]]] = {}
    for row in rows:
        key = (row["security_id"], row["trade_date"])
        grouped.setdefault(key, []).append(row)
    observed = set(grouped)
    unique = {key: values[0] for key, values in grouped.items()
              if key in expected_keys and len(values) == 1}
    return {
        "expected": len(expected_keys),
        "valid": sum(bar_validation_status(row) == "BAR_VALID" for row in unique.values()),
        "invalid": sum(bar_validation_status(row) == "BAR_INVALID" for row in unique.values()),
        "missing": len(expected_keys - observed),
        "conflict": sum(len(grouped[key]) > 1 for key in expected_keys & observed),
        "unexpected": len(observed - expected_keys),
    }


def audit_daily_constraints(rows: Sequence[Mapping[str, object]],
                            expected_keys: set[tuple[str, date]]) -> dict[str, int]:
    """Audit canonical Gate B outputs without deriving limits from vendor prices."""
    grouped: dict[tuple[str, date], list[Mapping[str, object]]] = {}
    for row in rows:
        grouped.setdefault((row["security_id"], row["trade_date"]), []).append(row)
    counts = {key: 0 for key in ("VALID", "INVALID", "MISSING", "CONFLICT",
                                  "10_PERCENT", "5_PERCENT", "20_PERCENT", "NO_LIMIT", "OTHER")}
    for key in expected_keys:
        values = grouped.get(key, ())
        if not values:
            counts["MISSING"] += 1
            continue
        if len(values) != 1:
            counts["CONFLICT"] += 1
            continue
        row = values[0]
        status = row.get("validation_status")
        if (status not in {"VALID", "INVALID", "MISSING", "CONFLICT"}
                or status == "VALID" and (row.get("price_basis") != "RAW"
                                             or not row.get("source_path"))):
            status = "CONFLICT"
        counts[status] += 1
        if status == "VALID":
            regime = row.get("limit_regime")
            counts[regime if regime in {"10_PERCENT", "5_PERCENT", "20_PERCENT", "NO_LIMIT"}
                   else "OTHER"] += 1
    counts["unexpected"] = len(set(grouped) - expected_keys)
    return counts


def factor_prefix_admission(*, factor_present: bool, events_complete: bool,
                            revision_dates_unresolved: Sequence[date],
                            as_of: date, source_frozen: bool) -> str:
    """A qualified D5 source does not automatically admit each security prefix."""
    if not source_frozen:
        return "SOURCE_NOT_FROZEN"
    if not factor_present:
        return "FACTOR_MISSING"
    if not events_complete:
        return "EVENT_CHRONOLOGY_INCOMPLETE"
    if any(day <= as_of for day in revision_dates_unresolved):
        return "UNRESOLVED_REVISION"
    return "QUALIFIED"


def freeze_private_blob(path: Path, payload: bytes, *, private_root: Path) -> str:
    """Exclusive creation prevents a later download from replacing a used ID."""
    if not private_root.is_dir() or not path.parent.is_dir() or not path.resolve().is_relative_to(
            private_root.resolve()) or path == private_root:
        raise ValueError("Target must be inside an existing private snapshot directory")
    with path.open("xb") as output:
        output.write(payload)
    return sha256(payload).hexdigest()
