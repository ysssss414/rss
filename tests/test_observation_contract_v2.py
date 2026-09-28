"""V2 changes eligibility timing without changing frozen V1 contracts."""

from decimal import Decimal

import pytest

from research.close_limit_up_v3 import CloseLimitUpV3, ReferenceDay
from research.observation_contract_v2 import (
    VERSION, episode_eligibility_at_start, evaluate_observation_trigger_v2,
)


def v3(status: str) -> CloseLimitUpV3:
    return CloseLimitUpV3(status, status, ReferenceDay("ORDINARY", "TEST"),
                          110 if status == "TRUE" else None,
                          status == "TRUE")


def evaluate(states, **changes):
    inputs = dict(board="SSE Main", lookback=[v3(state) for state in states],
                  trigger_regime="QUALIFIED_10PCT", rsi14_status="READY",
                  rsi14=Decimal("70.0001"))
    inputs.update(changes)
    return evaluate_observation_trigger_v2(**inputs)


@pytest.mark.parametrize("states,status,count", [
    (["TRUE"] * 4 + ["FALSE"], "QUALIFIED", 4),
    (["TRUE"] * 5, "QUALIFIED", 5),
    (["TRUE"] * 3 + ["FALSE"] * 2, "REJECTED", 3),
])
def test_strict_v3_count(states, status, count):
    decision = evaluate(states)
    assert decision.status == status and decision.definite_limit_up_count == count


def test_non_trigger_lookback_regime_is_not_a_parameter_or_prerequisite():
    assert VERSION == "OBSERVATION_POOL_CONTRACT_V2"
    # Four qualified V3 hits suffice even if the fifth observation is a
    # special-reference UNRESOLVED day; it is never counted as a hit.
    decision = evaluate(["TRUE", "TRUE", "UNRESOLVED", "TRUE", "TRUE"])
    assert decision.status == "QUALIFIED"
    assert (decision.definite_limit_up_count, decision.possible_limit_up_count) == (4, 5)
    assert episode_eligibility_at_start(decision)


def test_unresolved_v3_cannot_supply_fourth_hit():
    decision = evaluate(["TRUE", "TRUE", "UNRESOLVED", "FALSE", "TRUE"])
    assert decision.status == "UNRESOLVED"
    assert decision.reason == "V3_LOOKBACK_HITS_NOT_QUALIFIED"


@pytest.mark.parametrize("regime,status", [
    ("UNRESOLVED", "UNRESOLVED"),
    ("REJECTED_NON_10PCT", "REJECTED"),
    ("QUALIFIED_10PCT", "QUALIFIED"),
])
def test_only_trigger_date_regime_controls_admission(regime, status):
    decision = evaluate(["TRUE"] * 5, trigger_regime=regime)
    assert decision.status == status
    assert episode_eligibility_at_start(decision) is (status == "QUALIFIED")


def test_rsi_is_strict_and_must_be_formally_ready():
    states = ["TRUE"] * 4 + ["FALSE"]
    assert evaluate(states, rsi14=Decimal("70")).status == "REJECTED"
    assert evaluate(states, rsi14_status="READY_PROVISIONAL").status == "UNRESOLVED"
    assert evaluate(states, rsi14_status="UNVERIFIED_WARMUP").status == "UNRESOLVED"


def test_wrong_board_and_window_fail_closed():
    assert evaluate(["TRUE"] * 5, board="STAR").status == "REJECTED"
    with pytest.raises(ValueError):
        evaluate(["TRUE"] * 4)
    with pytest.raises(ValueError):
        evaluate_observation_trigger_v2(
            board="SSE Main",
            lookback=[CloseLimitUpV3("TRUE", "FAKE", ReferenceDay("ORDINARY", "TEST"))] * 5,
            trigger_regime="QUALIFIED_10PCT", rsi14_status="READY", rsi14=Decimal("71"))
