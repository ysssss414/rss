"""Versioned trigger-date-only Observation eligibility; V1 remains frozen."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from .close_limit_up_v3 import CloseLimitUpV3


VERSION = "OBSERVATION_POOL_CONTRACT_V2"
EPISODE_VERSION = "EPISODE_START_ELIGIBILITY_V1"
TARGET_BOARDS = {"SSE Main", "SZSE Main"}


@dataclass(frozen=True)
class TriggerDecisionV2:
    status: str  # QUALIFIED, REJECTED, UNRESOLVED
    reason: str
    definite_limit_up_count: int
    possible_limit_up_count: int


def evaluate_observation_trigger_v2(*, board: str,
                                    lookback: Sequence[CloseLimitUpV3],
                                    trigger_regime: str,
                                    rsi14_status: str,
                                    rsi14: Decimal | None) -> TriggerDecisionV2:
    """Count only qualified V3 TRUE; no lookback-day regime input exists."""
    if len(lookback) != 5:
        raise ValueError("Exactly five contract-defined valid observations required")
    if trigger_regime not in {"QUALIFIED_10PCT", "REJECTED_NON_10PCT", "UNRESOLVED"}:
        raise ValueError("Invalid trigger-date regime decision")
    if any(item.status not in {"TRUE", "FALSE", "EXCLUDED", "UNRESOLVED"}
           for item in lookback):
        raise ValueError("Invalid V3 event state")
    if any(item.status == "TRUE" and (
            item.reference_day.classification != "ORDINARY"
            or not item.mechanical_hit or item.canonical_limit_up_cents is None)
           for item in lookback):
        raise ValueError("Unqualified object cannot represent a V3 TRUE event")
    definite = sum(item.status == "TRUE" for item in lookback)
    possible = definite + sum(item.status == "UNRESOLVED" for item in lookback)

    def result(status: str, reason: str) -> TriggerDecisionV2:
        return TriggerDecisionV2(status, reason, definite, possible)

    if board not in TARGET_BOARDS:
        return result("REJECTED", "NON_TARGET_BOARD")
    if possible < 4:
        return result("REJECTED", "FEWER_THAN_FOUR_POSSIBLE_V3_HITS")
    if trigger_regime == "REJECTED_NON_10PCT":
        return result("REJECTED", "TRIGGER_REGIME_REJECTED")
    if trigger_regime == "UNRESOLVED":
        return result("UNRESOLVED", "TRIGGER_REGIME_UNRESOLVED")
    if rsi14_status != "READY" or rsi14 is None:
        return result("UNRESOLVED", "RSI14_NOT_FORMALLY_READY")
    if not isinstance(rsi14, Decimal) or not rsi14.is_finite() or not 0 <= rsi14 <= 100:
        return result("UNRESOLVED", "RSI14_INVALID")
    if rsi14 <= 70:
        return result("REJECTED", "RSI14_NOT_ABOVE_70")
    if definite < 4:
        return result("UNRESOLVED", "V3_LOOKBACK_HITS_NOT_QUALIFIED")
    return result("QUALIFIED", "TRIGGER_DATE_ONLY_REGIME_QUALIFIED")


def episode_eligibility_at_start(trigger: TriggerDecisionV2) -> bool:
    """Later sessions carry admission, subject to explicit lifecycle events."""
    return trigger.status == "QUALIFIED"
