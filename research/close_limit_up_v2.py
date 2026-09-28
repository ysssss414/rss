"""Operational 10% close-at-high event; not an official absolute-limit test."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext


VERSION = "CLOSE_LIMIT_UP_10PCT_V2"
TARGET_BOARDS = {"SSE Main", "SZSE Main"}
LOWER = Decimal("9.91")
UPPER = Decimal("10.09")
TICK = Decimal("0.01")


@dataclass(frozen=True)
class CloseLimitUpV2:
    status: str  # TRUE, FALSE, EXCLUDED, UNRESOLVED
    reason: str
    pct_change: Decimal | None = None
    mechanical_hit: bool = False


def _positive(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _price(value: object) -> Decimal | None:
    number = _positive(value)
    return number if number is not None and number % TICK == 0 else None


def evaluate_close_limit_up_v2(*, board: str, listing_phase: str,
                               close: object, high: object,
                               previous_raw_close: object,
                               factor: object, previous_factor: object,
                               is_xr_sec: bool | None,
                               special_exception: str = "UNKNOWN") -> CloseLimitUpV2:
    """Evaluate T-EOD raw prices; UNKNOWN exception evidence fails closed.

    Factor change and supplier ex-right flag are *screens*, not a reconstructed
    exchange reference. Only independent day evidence may set CLEARED.
    """
    if board not in TARGET_BOARDS:
        return CloseLimitUpV2("EXCLUDED", "NON_TARGET_BOARD")
    if listing_phase.startswith("IPO_DAY_") or listing_phase in {
        "RELISTING_DAY_1", "DELISTING_DAY_1"
    }:
        return CloseLimitUpV2("EXCLUDED", "NO_LIMIT_SPECIAL_PHASE")
    if listing_phase != "NORMAL_LISTED":
        return CloseLimitUpV2("UNRESOLVED", "LISTING_PHASE_UNQUALIFIED")
    if special_exception == "CONFIRMED":
        return CloseLimitUpV2("EXCLUDED", "NO_LIMIT_SPECIAL_EXCEPTION")
    if special_exception not in {"UNKNOWN", "CLEARED"}:
        raise ValueError("Invalid special-exception evidence state")
    raw_close, raw_high = _price(close), _price(high)
    previous = _price(previous_raw_close)
    current_factor, prior_factor = _positive(factor), _positive(previous_factor)
    if raw_close is None or raw_high is None or raw_close > raw_high:
        return CloseLimitUpV2("UNRESOLVED", "INVALID_RAW_PRICE")
    if previous is None or current_factor is None or prior_factor is None:
        return CloseLimitUpV2("UNRESOLVED", "MISSING_PREVIOUS_OR_FACTOR")
    if is_xr_sec is None:
        return CloseLimitUpV2("UNRESOLVED", "MISSING_CORPORATE_ACTION_SCREEN")
    if current_factor != prior_factor or is_xr_sec is True:
        return CloseLimitUpV2("UNRESOLVED", "SPECIAL_REFERENCE_PRICE_REQUIRED")
    with localcontext() as context:
        context.prec = 28
        pct = (raw_close / previous - 1) * 100
    hit = LOWER <= pct <= UPPER and raw_close == raw_high
    if not hit:
        reason = "OUTSIDE_PCT_RANGE" if not LOWER <= pct <= UPPER else "CLOSE_BELOW_HIGH"
        return CloseLimitUpV2("FALSE", reason, pct)
    if special_exception == "UNKNOWN":
        return CloseLimitUpV2("UNRESOLVED", "SPECIAL_EXCEPTION_NOT_EXCLUDED", pct, True)
    return CloseLimitUpV2("TRUE", "OPERATIONAL_10PCT_CLOSE_AT_HIGH", pct, True)


def price_path_excludes_5pct(*, event: CloseLimitUpV2, board: str,
                             day: date) -> bool:
    """Qualified ordinary old-rule hit cannot be a normal 5% risk day."""
    return (event.status == "TRUE" and board in TARGET_BOARDS
            and day < date(2026, 7, 6))
