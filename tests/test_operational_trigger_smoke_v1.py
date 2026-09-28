"""Operational precedence and Observation V2 episode regressions."""

from datetime import date, timedelta
from decimal import Decimal

from research.close_limit_up_v3 import evaluate_close_limit_up_v3
from research.entry_signals import ENTRY_A, ENTRY_B
from research.observation_contract_v2 import evaluate_observation_trigger_v2
from research.observation_runtime_v2 import advance_episode_v2
from research.operational_trigger_qualification import qualify_operational_trigger


def v3(**changes):
    values = dict(board="SSE Main", listing_phase="NORMAL_LISTED",
                  close="2.12", high="2.12", previous_raw_close="1.93",
                  factor="48.081333", previous_factor="48.081333",
                  is_xr_sec=False, is_wd_sec=False, special_exception="CLEARED")
    values.update(changes)
    return evaluate_close_limit_up_v3(**values)


def qualify(event=None, **changes):
    values = dict(board="SSE Main", trigger_date=date(2024, 7, 4),
                  listing_phase="NORMAL_LISTED", v3=event or v3(),
                  vendor_is_st=False, vendor_limit_rate=0.1)
    values.update(changes)
    return qualify_operational_trigger(**values)


def step(previous=None, index=0, **changes):
    values = dict(previous=previous, security_id="600518.SH",
                  day=date(2024, 7, 4) + timedelta(days=index), session_index=index,
                  trigger_qualified=index == 0, suspended=False, quote_valid=True,
                  rsi=Decimal("75"), ma5_raw=Decimal("8"), raw_low=Decimal("9"),
                  raw_close=Decimal("10"), indicator_close=Decimal("10"),
                  entry_v3_true=False)
    values.update(changes)
    return advance_episode_v2(**values)


def test_600518_official_v3_overrides_stale_vendor_status():
    decision = qualify(vendor_is_st=True, vendor_limit_rate=0.1, official_rate="0.10")
    assert v3().status == "TRUE"
    assert decision.status == "QUALIFIED_BY_V3_PRICE_PATH"
    assert decision.vendor_conflict and decision.official_override


def test_vendor_normal_st_missing_and_official_conflict():
    false_event = v3(close="2.11", high="2.12")
    assert qualify(false_event).status == "QUALIFIED_BY_OPERATIONAL_STATUS"
    assert qualify(false_event, vendor_is_st=True, vendor_limit_rate=0.05).status == "REJECTED_BY_OPERATIONAL_STATUS"
    assert qualify(false_event, vendor_is_st=None).status == "REVIEW_REQUIRED"
    assert qualify(false_event, vendor_limit_rate=None).status == "REVIEW_REQUIRED"
    assert qualify(false_event, vendor_is_st=False, vendor_limit_rate=0.05).status == "REVIEW_REQUIRED"
    assert qualify(false_event, official_rate="0.05").status == "REVIEW_REQUIRED"
    assert qualify(false_event, board_conflict=True).reason == "BOARD_CLASSIFICATION_CONFLICT"
    assert qualify(false_event, vendor_is_st=True, official_rate="0.10").official_override
    assert qualify(vendor_is_st=True).status == "QUALIFIED_BY_V3_PRICE_PATH"


def test_post_2026_600187_rule_not_overridden_by_old_vendor_rate():
    event = v3(previous_raw_close="1.47", close="1.62", high="1.62")
    assert event.status == "TRUE"
    assert qualify(event, trigger_date=date(2026, 7, 6), vendor_is_st=True,
                   vendor_limit_rate=0.05).status == "QUALIFIED_BY_V3_PRICE_PATH"
    assert qualify(v3(close="2.11", high="2.12"), trigger_date=date(2026, 7, 6),
                   vendor_limit_rate=0.05).status == "QUALIFIED_BY_OPERATIONAL_STATUS"


def test_observation_v2_counts_only_v3_hits_and_trigger_status():
    true, false = v3(), v3(close="2.11", high="2.12")
    for hits in (4, 5):
        window = [true] * hits + [false] * (5 - hits)
        decision = evaluate_observation_trigger_v2(
            board="SSE Main", lookback=window, trigger_regime="QUALIFIED_10PCT",
            rsi14_status="READY", rsi14=Decimal("71"))
        assert decision.status == "QUALIFIED"
    assert evaluate_observation_trigger_v2(
        board="SSE Main", lookback=[true] * 5, trigger_regime="REJECTED_NON_10PCT",
        rsi14_status="READY", rsi14=Decimal("71")).status == "REJECTED"
    assert evaluate_observation_trigger_v2(
        board="SSE Main", lookback=[true] * 5, trigger_regime="UNRESOLVED",
        rsi14_status="READY", rsi14=Decimal("71")).status == "UNRESOLVED"


def test_episode_no_daily_st_input_suspension_expiry_and_reentry():
    admission = step()
    assert admission.event == "POOL_ADMISSION"
    suspended = step(admission.episode, 1, suspended=True, rsi=None, raw_low=None,
                     raw_close=None, indicator_close=None)
    assert suspended.event == "SUSPENDED_NO_ENTRY_EVALUATION"
    current = suspended
    for index in range(2, 7):
        current = step(current.episode, index)
    assert current.event == "POOL_EXPIRED"
    next_episode = step(current.episode, 7, trigger_qualified=True)
    assert next_episode.event == "POOL_ADMISSION"
    assert next_episode.episode.observation_instance_id != admission.episode.observation_instance_id


def test_entry_a_b_intents_and_no_fill_claim():
    a = step(ma5_raw=Decimal("9.5"), entry_v3_true=True)
    assert a.entry_reasons == (ENTRY_A,)
    assert a.execution_intent == "LIMIT_UP_CLOSE_BOARD_INTENT"
    assert a.episode.status == "SIGNALLED"
    pending = step(a.episode, 1, trigger_qualified=True, ma5_raw=Decimal("9.5"))
    assert pending.event == "EXECUTION_PENDING" and not pending.entry_reasons
    start = step()
    below = step(start.episode, 1, rsi=Decimal("65"))
    b = step(below.episode, 2, rsi=Decimal("72"), raw_close=Decimal("11"),
             indicator_close=Decimal("11"))
    assert b.entry_reasons == (ENTRY_B,)
    assert b.execution_intent == "NORMAL_CLOSE_INTENT"


def test_future_mutation_does_not_change_prior_episode():
    first = step()
    a = step(first.episode, 1, rsi=Decimal("65"))
    b = step(first.episode, 1, rsi=Decimal("5"), hard_invalid=True)
    assert first == step()
    assert a.episode.admission_date == b.episode.admission_date == first.episode.admission_date
