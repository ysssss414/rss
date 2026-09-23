"""D5 event-decomposition checks for a frozen backward-factor snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Mapping, Sequence


FACTOR_RATIO_CONTRACT = "HISTORICAL_FACTOR_RATIO_V1"
FACTOR_SNAPSHOT_CONTRACT = "PIT_FACTOR_RATIO_SNAPSHOT_V1"
BACKWARD_QUANTUM = Decimal("0.000001")  # Observed output, not SDK-documented precision.


@dataclass(frozen=True)
class CorporateAction:
    event_id: str
    kind: str
    effective_date: date
    known_date: date
    single_factor: Decimal
    state: str  # PROPOSAL or IMPLEMENTED
    version: int
    revision_resolved: bool
    revision_evidence: str | None = None
    vendor_changed: bool = False


def implemented_events(actions: Sequence[CorporateAction], cutoff: date) -> tuple[CorporateAction, ...]:
    """Fail closed on ambiguous, late, or retroactively changed action terms."""
    selected = []
    for event_id in sorted({action.event_id for action in actions}):
        versions = sorted((action for action in actions if action.event_id == event_id),
                          key=lambda action: action.version)
        final = versions[-1]
        if final.effective_date > cutoff:
            continue
        if len({action.version for action in versions}) != len(versions):
            raise ValueError("Conflicting corporate-action versions")
        if final.state != "IMPLEMENTED":
            continue
        # Date-only announcements need the preceding day; same-day needs verified time.
        if (not final.revision_resolved or final.known_date >= final.effective_date
                or final.single_factor <= 0 or not final.single_factor.is_finite()
                or (len(versions) > 1 or final.vendor_changed) and not final.revision_evidence):
            raise ValueError("Unresolved or late corporate-action terms")
        selected.append(final)
    if len({action.effective_date for action in selected}) != len(selected):
        raise ValueError("Multiple actions on one date need an explicitly combined factor")
    return tuple(sorted(selected, key=lambda action: action.effective_date))


def reconstructed_ratio(actions: Sequence[CorporateAction], d: date, t: date) -> Decimal:
    """B(d)/B(T) = 1 / product(A(e), d < e <= T); later events are excluded."""
    if d > t:
        raise ValueError("History date must be no later than as-of date")
    events = implemented_events(actions, t)
    with localcontext() as context:
        context.prec = 40
        product = Decimal(1)
        for event in events:
            if d < event.effective_date <= t:
                product *= event.single_factor
        return Decimal(1) / product


def backward_ratio_bounds(before: Decimal, after: Decimal) -> tuple[Decimal, Decimal]:
    """Conservative ratio interval for observed six-decimal backward-factor rounding."""
    half = BACKWARD_QUANTUM / 2
    if before <= half or after <= half:
        raise ValueError("Invalid backward factor")
    with localcontext() as context:
        context.prec = 40
        return (before - half) / (after + half), (before + half) / (after - half)


def ratio_matches(reconstructed: Decimal, before: Decimal, after: Decimal) -> bool:
    low, high = backward_ratio_bounds(before, after)
    return low <= reconstructed <= high


def common_normalization_preserves_ratio(
    original: Mapping[date, Decimal], revised: Mapping[date, Decimal], d: date, t: date,
) -> bool:
    if min(original[d], original[t], revised[d], revised[t]) <= 0:
        raise ValueError("Invalid normalization")
    return original[d] * revised[t] == revised[d] * original[t]


@dataclass(frozen=True)
class FactorRatioSnapshot:
    """Small security/date factor cache; never stores adjusted-close history."""

    security_id: str
    source_snapshot_id: str
    source_hash: str
    raw_factors: Mapping[date, Decimal]
    actions: tuple[CorporateAction, ...]

    def ratio(self, d: date, t: date) -> Decimal:
        if (not self.security_id or not self.source_snapshot_id or len(self.source_hash) != 64
                or any(char not in "0123456789abcdef" for char in self.source_hash)
                or d not in self.raw_factors or t not in self.raw_factors):
            raise ValueError("Incomplete factor-ratio snapshot")
        expected = reconstructed_ratio(self.actions, d, t)
        before, after = self.raw_factors[d], self.raw_factors[t]
        if not ratio_matches(expected, before, after):
            raise ValueError("Provider ratio disagrees with effective-event decomposition")
        with localcontext() as context:
            context.prec = 40
            return before / after
