"""Tick-space operational 10% close-limit event, separate from V1 and V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


VERSION = "CLOSE_LIMIT_UP_10PCT_V3"
ROUNDING_VERSION = "PRICE_LIMIT_TICK_ROUNDING_V1"
REFERENCE_VERSION = "REFERENCE_PRICE_DAY_CLASSIFICATION_V1"
TARGET_BOARDS = {"SSE Main", "SZSE Main"}
TICK = Decimal("0.01")


@dataclass(frozen=True)
class ReferenceDay:
    classification: str
    reason: str
    previous_close_cents: int | None = None


@dataclass(frozen=True)
class CloseLimitUpV3:
    status: str  # TRUE, FALSE, EXCLUDED, UNRESOLVED
    reason: str
    reference_day: ReferenceDay
    canonical_limit_up_cents: int | None = None
    mechanical_hit: bool = False


def _positive_decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def price_cents(value: object) -> int | None:
    """Accept only an exact positive 0.01-CNY raw trading price."""
    number = _positive_decimal(value)
    if number is None or number % TICK:
        return None
    return int(number / TICK)


def upper_limit_cents(reference_cents: int, rate_basis_points: int = 1000) -> int:
    """Official half-up tick rounding, plus the one-tick minimum.

    1000 basis points is 10%; positive integer arithmetic has no binary-float
    or bankers-rounding path. The 0.01-CNY tick is this contract's scope.
    """
    if type(reference_cents) is not int or reference_cents <= 0 or rate_basis_points not in {500, 1000}:
        raise ValueError("Unsupported reference or limit rate")
    numerator = reference_cents * (10_000 + rate_basis_points)
    return max((numerator + 5_000) // 10_000, reference_cents + 1)


def classify_reference_day(*, board: str, listing_phase: str,
                           previous_raw_close: object,
                           factor: object, previous_factor: object,
                           is_xr_sec: bool | None, is_wd_sec: bool | None = None) -> ReferenceDay:
    if board not in TARGET_BOARDS:
        return ReferenceDay("OTHER_SPECIAL", "NON_TARGET_BOARD")
    if listing_phase.startswith("IPO_DAY_"):
        return ReferenceDay("IPO_NO_LIMIT", "IPO_FIRST_FIVE_SESSIONS")
    if listing_phase in {"RELISTING_DAY_1", "DELISTING_DAY_1"}:
        return ReferenceDay("RELISTING_OR_OTHER_NO_LIMIT", "NO_LIMIT_FIRST_DAY")
    if listing_phase != "NORMAL_LISTED":
        return ReferenceDay("UNRESOLVED", "LISTING_PHASE_UNQUALIFIED")
    previous_cents = price_cents(previous_raw_close)
    current_factor = _positive_decimal(factor)
    prior_factor = _positive_decimal(previous_factor)
    if previous_cents is None or current_factor is None or prior_factor is None:
        return ReferenceDay("UNRESOLVED", "MISSING_PREVIOUS_OR_FACTOR")
    if is_xr_sec is None:
        return ReferenceDay("UNRESOLVED", "MISSING_CORPORATE_ACTION_SCREEN")
    if is_wd_sec is None:
        return ReferenceDay("UNRESOLVED", "MISSING_DELISTING_SCREEN")
    if current_factor != prior_factor or is_xr_sec:
        return ReferenceDay("CORPORATE_ACTION_SPECIAL", "SPECIAL_REFERENCE_PRICE_REQUIRED")
    if is_wd_sec is True:
        return ReferenceDay("OTHER_SPECIAL", "VENDOR_DELISTING_SCREEN_REVIEW")
    return ReferenceDay("ORDINARY", "PREVIOUS_VALID_RAW_CLOSE_REFERENCE", previous_cents)


def evaluate_close_limit_up_v3(*, board: str, listing_phase: str,
                               close: object, high: object,
                               previous_raw_close: object,
                               factor: object, previous_factor: object,
                               is_xr_sec: bool | None,
                               is_wd_sec: bool | None = None,
                               special_exception: str = "UNKNOWN",
                               price_tick: object = "0.01") -> CloseLimitUpV3:
    """T-EOD event; independent official day evidence must clear exceptions."""
    if special_exception not in {"UNKNOWN", "CLEARED", "CONFIRMED"}:
        raise ValueError("Invalid special-exception state")
    reference = classify_reference_day(
        board=board, listing_phase=listing_phase,
        previous_raw_close=previous_raw_close, factor=factor,
        previous_factor=previous_factor, is_xr_sec=is_xr_sec,
        is_wd_sec=is_wd_sec,
    )
    if reference.classification in {"IPO_NO_LIMIT", "RELISTING_OR_OTHER_NO_LIMIT"}:
        return CloseLimitUpV3("EXCLUDED", reference.reason, reference)
    if reference.reason == "NON_TARGET_BOARD":
        return CloseLimitUpV3("EXCLUDED", reference.reason, reference)
    if reference.classification != "ORDINARY":
        return CloseLimitUpV3("UNRESOLVED", reference.reason, reference)
    if special_exception == "CONFIRMED":
        return CloseLimitUpV3("EXCLUDED", "NO_LIMIT_SPECIAL_EXCEPTION", reference)
    if _positive_decimal(price_tick) != TICK:
        return CloseLimitUpV3("UNRESOLVED", "UNSUPPORTED_OR_CHANGED_PRICE_TICK", reference)
    close_cents, high_cents = price_cents(close), price_cents(high)
    if close_cents is None or high_cents is None or close_cents > high_cents:
        return CloseLimitUpV3("UNRESOLVED", "INVALID_RAW_PRICE", reference)
    upper_cents = upper_limit_cents(reference.previous_close_cents)
    if close_cents != upper_cents or high_cents != close_cents:
        reason = "CLOSE_NOT_AT_CALCULATED_LIMIT" if close_cents != upper_cents else "CLOSE_BELOW_HIGH"
        return CloseLimitUpV3("FALSE", reason, reference, upper_cents)
    if special_exception == "UNKNOWN":
        return CloseLimitUpV3("UNRESOLVED", "SPECIAL_EXCEPTION_NOT_EXCLUDED", reference,
                              upper_cents, True)
    return CloseLimitUpV3("TRUE", "TICK_ROUNDED_10PCT_CLOSE_AT_HIGH", reference,
                          upper_cents, True)


def price_path_excludes_5pct_v2(*, event: CloseLimitUpV3, board: str,
                                day: date) -> bool:
    """Only a separated 10%/5% price ceiling proves old 5% impossible."""
    previous = event.reference_day.previous_close_cents
    return (event.status == "TRUE" and board in TARGET_BOARDS
            and day < date(2026, 7, 6) and previous is not None
            and upper_limit_cents(previous, 1000) > upper_limit_cents(previous, 500))
