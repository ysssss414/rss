"""Observation V2 episode and unchanged Entry A/B predicates for research smoke."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from hashlib import sha256

from .entry_signals import ENTRY_A, ENTRY_B
from .observation_contract_v2 import VERSION as OBSERVATION_VERSION


@dataclass(frozen=True)
class EpisodeV2:
    security_id: str
    admission_date: date
    observation_instance_id: str
    status: str
    admission_session_index: int
    last_session_index: int
    seen_rsi_below_70: bool = False
    previous_valid_rsi: Decimal | None = None
    previous_valid_indicator_close: Decimal | None = None


@dataclass(frozen=True)
class EpisodeStepV2:
    episode: EpisodeV2 | None
    event: str
    entry_reasons: tuple[str, ...] = ()
    execution_intent: str | None = None


def advance_episode_v2(*, previous: EpisodeV2 | None, security_id: str,
                       day: date, session_index: int, trigger_qualified: bool,
                       suspended: bool, quote_valid: bool,
                       rsi: Decimal | None, ma5_raw: Decimal | None,
                       raw_low: Decimal | None, raw_close: Decimal | None,
                       indicator_close: Decimal | None,
                       entry_v3_true: bool, hard_invalid: bool = False) -> EpisodeStepV2:
    """No post-admission ST/regime input exists. No execution fill is inferred."""
    if previous is not None and previous.security_id != security_id:
        raise ValueError("Episode security mismatch")
    if previous is not None and previous.status in {"ACTIVE", "SIGNALLED"}:
        if session_index <= previous.last_session_index:
            raise ValueError("Non-forward episode evaluation")
        index = session_index - previous.admission_session_index + 1
        if index > 7:
            return EpisodeStepV2(replace(previous, status="EXPIRED", last_session_index=session_index),
                                 "PENDING_SIGNAL_HORIZON_EXPIRED" if previous.status == "SIGNALLED"
                                 else "POOL_EXPIRED")
        episode = replace(previous, last_session_index=session_index)
        if hard_invalid:
            return EpisodeStepV2(replace(episode, status="INVALIDATED"), "HARD_INVALIDATION")
        if not suspended and not quote_valid:
            return EpisodeStepV2(replace(episode, status="INVALIDATED"), "INVALID_QUOTE")
        if previous.status == "SIGNALLED":
            if index >= 7:
                return EpisodeStepV2(replace(episode, status="EXPIRED"),
                                     "PENDING_SIGNAL_HORIZON_EXPIRED")
            return EpisodeStepV2(episode, "EXECUTION_PENDING")
        if suspended:
            if index == 7:
                episode = replace(episode, status="EXPIRED")
            return EpisodeStepV2(episode, "SUSPENDED_NO_ENTRY_EVALUATION")
    elif trigger_qualified and not suspended and quote_valid and not hard_invalid:
        instance_id = sha256(f"{security_id}|{day.isoformat()}|{OBSERVATION_VERSION}".encode()).hexdigest()
        episode = EpisodeV2(security_id, day, instance_id, "ACTIVE", session_index, session_index)
        index = 1
    else:
        return EpisodeStepV2(previous, "NOT_ADMITTED")

    if (rsi is None or not rsi.is_finite() or not 0 <= rsi <= 100
            or raw_low is None or raw_close is None or indicator_close is None):
        return EpisodeStepV2(replace(episode, status="INVALIDATED"), "INVALID_PIT_ENTRY_DEPENDENCY")
    reasons = []
    if rsi > 70 and not episode.seen_rsi_below_70 and ma5_raw is not None:
        if raw_low <= ma5_raw <= raw_close:
            reasons.append(ENTRY_A)
    if (episode.seen_rsi_below_70 and episode.previous_valid_rsi is not None
            and episode.previous_valid_rsi < 70 and rsi > 70
            and episode.previous_valid_indicator_close is not None
            and indicator_close > episode.previous_valid_indicator_close):
        reasons.append(ENTRY_B)
    episode = replace(episode, seen_rsi_below_70=episode.seen_rsi_below_70 or rsi < 70,
                      previous_valid_rsi=rsi, previous_valid_indicator_close=indicator_close)
    if reasons:
        return EpisodeStepV2(replace(episode, status="SIGNALLED"), "ENTRY_SIGNAL",
                             tuple(reasons), "LIMIT_UP_CLOSE_BOARD_INTENT" if entry_v3_true
                             else "NORMAL_CLOSE_INTENT")
    if index == 7:
        return EpisodeStepV2(replace(episode, status="EXPIRED"), "POOL_EXPIRED")
    return EpisodeStepV2(episode, "POOL_ADMISSION" if index == 1 else "POOL_OBSERVATION")
