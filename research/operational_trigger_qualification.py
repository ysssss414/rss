"""Trigger-day research screen; vendor history is not canonical regime evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .close_limit_up_v3 import CloseLimitUpV3, TARGET_BOARDS


VERSION = "OPERATIONAL_TRIGGER_QUALIFICATION_V1"
VENDOR_SOURCE = "REAL_RESEARCH_SNAPSHOT_V1/daily_status.is_st_sec"


@dataclass(frozen=True)
class OperationalTriggerDecision:
    status: str
    evidence_level: str
    reason: str
    vendor_conflict: bool
    official_override: bool
    v3_self_qualification: bool
    vendor_source: str = VENDOR_SOURCE


def qualify_operational_trigger(*, board: str, trigger_date: date,
                                listing_phase: str, v3: CloseLimitUpV3,
                                vendor_is_st: bool | None,
                                vendor_limit_rate: float | None,
                                official_rate: str | None = None,
                                board_conflict: bool = False) -> OperationalTriggerDecision:
    """Apply official effective-day evidence, then V3, then frozen T-day status.

    ``v3`` is evaluated under this version's bounded operational exception
    screen. Its clearance is not a newly discovered official historical fact.
    """
    def decision(status: str, level: str, reason: str, *, conflict: bool = False,
                 override: bool = False, self_qualified: bool = False) -> OperationalTriggerDecision:
        return OperationalTriggerDecision(status, level, reason, conflict, override,
                                          self_qualified)

    if board_conflict:
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "BOARD_CLASSIFICATION_CONFLICT")
    if board not in TARGET_BOARDS or listing_phase != "NORMAL_LISTED":
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "BOARD_OR_LISTING_PHASE")
    if v3.reference_day.classification != "ORDINARY" or v3.status in {"EXCLUDED", "UNRESOLVED"}:
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "REFERENCE_OR_V3_UNRESOLVED")
    if type(vendor_is_st) is not bool:
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "VENDOR_STATUS_MISSING_OR_INVALID")
    if official_rate not in {None, "0.05", "0.10"}:
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "OFFICIAL_EVIDENCE_INVALID")
    if official_rate == "0.05":
        return decision("REVIEW_REQUIRED", "CANONICAL_OFFICIAL",
                        "OFFICIAL_RISK_WARNING_DAY", conflict=v3.status == "TRUE" or not vendor_is_st,
                        override=True)
    if v3.status == "TRUE":
        return decision("QUALIFIED_BY_V3_PRICE_PATH",
                        "CANONICAL_OFFICIAL_PLUS_V3" if official_rate else "V3_OPERATIONAL_PRICE_PATH",
                        "TICK_SPACE_10PCT_CLOSE_AT_HIGH",
                        conflict=vendor_is_st, override=official_rate is not None,
                        self_qualified=True)
    if official_rate == "0.10":
        return decision("QUALIFIED_BY_OPERATIONAL_STATUS", "CANONICAL_OFFICIAL",
                        "OFFICIAL_NORMAL_DAY_OVERRIDES_VENDOR", conflict=vendor_is_st,
                        override=True)
    if vendor_limit_rate not in {0.05, 0.1}:
        return decision("REVIEW_REQUIRED", "UNRESOLVED", "VENDOR_LIMIT_RATE_MISSING_OR_INVALID")
    # Before the July 2026 main-board change, a status/rate disagreement is an
    # anomaly. After it, the historical vendor 5% field cannot defeat the rule.
    if (trigger_date < date(2026, 7, 6)
            and (vendor_limit_rate == 0.05) != vendor_is_st):
        return decision("REVIEW_REQUIRED", "OPERATIONAL_VENDOR_SCREEN",
                        "VENDOR_STATUS_RATE_CONFLICT")
    if vendor_is_st:
        return decision("REJECTED_BY_OPERATIONAL_STATUS", "OPERATIONAL_VENDOR_SCREEN",
                        "VENDOR_RISK_WARNING_TRUE")
    return decision("QUALIFIED_BY_OPERATIONAL_STATUS", "OPERATIONAL_VENDOR_SCREEN",
                    "VENDOR_NORMAL_TRUE")
