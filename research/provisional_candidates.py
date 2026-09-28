"""High-recall search leads, never canonical limit-up or Observation events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256


DETECTOR_VERSION = "LIMIT_UP_LIKE_DISCOVERY_V1"
CANDIDATE_VERSION = "PROVISIONAL_OBSERVATION_CANDIDATE_V1"
BROAD_RETURN_FLOOR = Decimal("0.05")
V2_DETECTOR_VERSION = "RAW_RETURN_GE_5PCT_OR_SPECIAL_REFERENCE_V2"
V2_CANDIDATE_VERSION = "PROVISIONAL_OBSERVATION_CANDIDATE_V2"


def _positive(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def limit_up_like(*, close: object, vendor_upper: object,
                  vendor_preclose: object, previous_raw_close: object) -> tuple[bool, tuple[str, ...]]:
    """Union of broad raw-price and vendor search hints; false positives are expected.

    A 5% floor deliberately includes old 5% risk-warning hits. Neither supplier
    reference nor supplier upper price can qualify a real limit-up event.
    """
    raw = _positive(close)
    if raw is None:
        return False, ()
    reasons = []
    upper = _positive(vendor_upper)
    if upper is not None and abs(raw - upper) < Decimal("0.005"):
        reasons.append("VENDOR_UPPER_MATCH")
    for value, label in ((vendor_preclose, "VENDOR_PRECLOSE_MOVE_GE_5PCT"),
                         (previous_raw_close, "PREVIOUS_RAW_CLOSE_MOVE_GE_5PCT")):
        reference = _positive(value)
        if reference is not None and raw / reference - 1 >= BROAD_RETURN_FLOOR:
            reasons.append(label)
    return bool(reasons), tuple(reasons)


def limit_up_like_v2(*, close: object, previous_raw_close: object,
                     reference_special: bool) -> tuple[bool, tuple[str, ...]]:
    """Discovery superset of ordinary V2 hits; special references are retained.

    Every evaluable V2 hit has raw return >=9.91%, hence passes this 5% floor.
    Vendor absolute prices and status rates never enter this detector.
    """
    raw, previous = _positive(close), _positive(previous_raw_close)
    reasons = []
    if raw is not None and previous is not None and raw / previous - 1 >= BROAD_RETURN_FLOOR:
        reasons.append("PREVIOUS_RAW_CLOSE_MOVE_GE_5PCT")
    if reference_special:
        reasons.append("SPECIAL_REFERENCE_REVIEW")
    return bool(reasons), tuple(reasons)


@dataclass
class RsiPrefix:
    """Exact first-valid-delta recurrence of RSI14_PROJECT_V1, on a common scale."""

    previous_price: Decimal | None = None
    average_gain: Decimal | None = None
    average_change: Decimal | None = None
    invalid: bool = False

    def update(self, raw_close: object, factor: object) -> Decimal | None:
        close, adjustment = _positive(raw_close), _positive(factor)
        if close is None or adjustment is None:
            self.invalid = True
            return None
        if self.invalid:
            return None
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            price = close * adjustment
            if self.previous_price is not None:
                delta = price - self.previous_price
                gain, change = max(delta, Decimal(0)), abs(delta)
                if self.average_gain is None:
                    self.average_gain, self.average_change = gain, change
                else:
                    self.average_gain = (gain + 13 * self.average_gain) / 14
                    self.average_change = (change + 13 * self.average_change) / 14
            self.previous_price = price
            if self.average_change is None or self.average_change == 0:
                return None
            return self.average_gain / self.average_change * 100


def provisional_window(hits: deque[bool] | list[bool], rsi: Decimal | None) -> bool:
    return len(hits) == 5 and sum(hits) >= 4 and rsi is not None and rsi > 70


def candidate_id(security_id: str, trigger_date: str) -> str:
    return sha256(f"{security_id}|{trigger_date}|{CANDIDATE_VERSION}".encode()).hexdigest()


def candidate_id_v2(security_id: str, trigger_date: str) -> str:
    return sha256(f"{security_id}|{trigger_date}|{V2_CANDIDATE_VERSION}".encode()).hexdigest()
