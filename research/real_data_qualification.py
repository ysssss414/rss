"""Fail-closed evidence checks for a bounded real Stage 1 research snapshot.

This module does not generate Entry signals or alter the event-study engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Mapping, Sequence


@dataclass(frozen=True)
class EffectiveIdentity:
    security_id: str
    security_type: str
    exchange: str
    effective_from: date
    effective_to: date | None
    latest_listing_or_relisting: date


def identity_at(records: Sequence[EffectiveIdentity], security_id: str, day: date) -> EffectiveIdentity | None:
    """Future delisting cannot alter eligibility on an earlier effective date."""
    matches = [row for row in records if row.security_id == security_id
               and row.effective_from <= day
               and (row.effective_to is None or day < row.effective_to)]
    if len(matches) > 1:
        raise ValueError("Overlapping historical security identity")
    return matches[0] if matches else None


def trading_state(*, identities: Sequence[EffectiveIdentity], security_id: str,
                  day: date, status: str | None, bar_present: bool) -> str:
    """A missing bar by itself is UNKNOWN, never evidence of suspension."""
    identity = identity_at(identities, security_id, day)
    if identity is None:
        known = [row for row in identities if row.security_id == security_id]
        if known and day < min(row.effective_from for row in known):
            return "NOT_LISTED"
        if any(row.effective_to is not None and row.effective_to <= day for row in known):
            return "LIFECYCLE_ENDED"
        return "UNKNOWN"
    if status == "SUSPENDED" and not bar_present:
        return "SUSPENDED"
    if status == "SUSPENDED" and bar_present:
        return "CONFLICT"
    if status == "TRADING" and bar_present:
        return "TRADED"
    return "UNKNOWN"


def a_share_common_stock_at(records: Sequence[EffectiveIdentity], security_id: str, day: date) -> bool:
    identity = identity_at(records, security_id, day)
    return identity is not None and identity.security_type == "A_SHARE_COMMON_STOCK"


def factor_ratio_interval(pre: Decimal, post: Decimal, precision: Decimal) -> tuple[Decimal, Decimal]:
    """Bounds induced by two factors rounded to the declared display precision."""
    half = precision / 2
    if (not all(isinstance(x, Decimal) and x.is_finite() for x in (pre, post, precision))
            or precision <= 0 or pre <= half or post <= half):
        raise ValueError("Positive finite rounded factors and precision required")
    return (pre - half) / (post + half), (pre + half) / (post - half)


def ratio_invariant(before: tuple[Decimal, Decimal], after: tuple[Decimal, Decimal],
                    precision: Decimal) -> bool:
    """Compare the *pre/post ratio* across genuine retrieval snapshots, not raw levels."""
    left = factor_ratio_interval(*before, precision)
    right = factor_ratio_interval(*after, precision)
    return max(left[0], right[0]) <= min(left[1], right[1])


def reconstructed_ex_reference(*, previous_close: Decimal, cash_per_share: Decimal,
                               bonus_per_share: Decimal = Decimal(0),
                               rights_per_share: Decimal = Decimal(0),
                               rights_price: Decimal = Decimal(0),
                               tick: Decimal = Decimal("0.01")) -> Decimal:
    """Price-scale ex-reference only; cash dividend is not added as income."""
    values = (previous_close, cash_per_share, bonus_per_share, rights_per_share, rights_price, tick)
    if (not all(isinstance(x, Decimal) and x.is_finite() for x in values)
            or previous_close <= 0 or tick <= 0
            or any(x < 0 for x in values[1:5])):
        raise ValueError("Invalid corporate-action terms")
    raw = (previous_close - cash_per_share + rights_per_share * rights_price) / (
        Decimal(1) + bonus_per_share + rights_per_share)
    if raw <= 0:
        raise ValueError("Nonpositive reconstructed ex-reference")
    return (raw / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick


def official_ratio_in_vendor_bounds(*, previous_close: Decimal, reference: Decimal,
                                    factor_pre: Decimal, factor_post: Decimal,
                                    factor_precision: Decimal) -> bool:
    if previous_close <= 0 or reference <= 0:
        raise ValueError("Positive raw prices required")
    lower, upper = factor_ratio_interval(factor_pre, factor_post, factor_precision)
    return lower <= reference / previous_close <= upper


def reconcile_calendar(vendor_sessions: Sequence[date], start: date, end: date,
                       official_closures: Sequence[tuple[date, date]]) -> dict[str, object]:
    """Exact weekday-minus-official-holiday check; preserve every mismatch."""
    if start > end or tuple(sorted(set(vendor_sessions))) != tuple(vendor_sessions):
        raise ValueError("Invalid vendor calendar window")
    closed = set()
    for first, last in official_closures:
        if first > last:
            raise ValueError("Invalid official closure interval")
        day = first
        while day <= last:
            closed.add(day)
            day += timedelta(days=1)
    expected = set()
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in closed:
            expected.add(day)
        day += timedelta(days=1)
    actual = {day for day in vendor_sessions if start <= day <= end}
    return {"start": start.isoformat(), "end": end.isoformat(),
            "official_session_count": len(expected), "vendor_session_count": len(actual),
            "missing_vendor_dates": sorted(x.isoformat() for x in expected - actual),
            "extra_vendor_dates": sorted(x.isoformat() for x in actual - expected),
            "exact_match": expected == actual}


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    start: date | None
    end: date | None
    qualified: bool
    gap_dates: tuple[date, ...] = ()


def maximal_contiguous_intersection(sources: Sequence[SourceCoverage]) -> tuple[date, date] | None:
    if not sources or any(not x.qualified or x.start is None or x.end is None for x in sources):
        return None
    start, end = max(x.start for x in sources), min(x.end for x in sources)
    if start > end:
        return None
    gaps = sorted({day for x in sources for day in x.gap_dates if start <= day <= end})
    segments = []
    cursor = start
    for gap in gaps:
        if cursor < gap:
            segments.append((cursor, gap - timedelta(days=1)))
        cursor = gap + timedelta(days=1)
    if cursor <= end:
        segments.append((cursor, end))
    return max(segments, key=lambda span: ((span[1] - span[0]).days,
                                           -span[0].toordinal())) if segments else None


def snapshot_receipt(*, provider: str, version: str, retrieved_at: str,
                     request: Mapping[str, object], raw_bytes: bytes,
                     normalized_bytes: bytes, row_count: int, security_count: int,
                     coverage_start: date, coverage_end: date) -> dict[str, object]:
    if (not provider or not version or not retrieved_at or not request
            or row_count < 0 or security_count < 0 or coverage_start > coverage_end):
        raise ValueError("Incomplete real-research snapshot receipt")
    return {"schema": "REAL_RESEARCH_SNAPSHOT_V1", "provider": provider,
            "provider_version": version, "retrieved_at": retrieved_at,
            "request": dict(request), "raw_sha256": sha256(raw_bytes).hexdigest(),
            "normalized_sha256": sha256(normalized_bytes).hexdigest(),
            "row_count": row_count, "security_count": security_count,
            "coverage_start": coverage_start.isoformat(), "coverage_end": coverage_end.isoformat()}
