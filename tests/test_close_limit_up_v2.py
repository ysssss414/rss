"""Operational V2 must remain separate from frozen official-price V1."""

import json
from datetime import date
from pathlib import Path

import pytest

from research.close_limit_up_v2 import evaluate_close_limit_up_v2, price_path_excludes_5pct
from research.provisional_candidates import limit_up_like_v2


def event(**changes):
    row = dict(board="SSE Main", listing_phase="NORMAL_LISTED",
               close="110.00", high="110.00", previous_raw_close="100.00",
               factor="1.000000", previous_factor="1.000000",
               is_xr_sec=False, special_exception="CLEARED")
    row.update(changes)
    return evaluate_close_limit_up_v2(**row)


@pytest.mark.parametrize("close,expected", [
    ("109.90", "FALSE"), ("109.91", "TRUE"), ("109.99", "TRUE"),
    ("110.00", "TRUE"), ("110.09", "TRUE"), ("110.10", "FALSE"),
])
def test_inclusive_percent_boundaries(close, expected):
    assert event(close=close, high=close).status == expected


def test_exact_raw_close_high_tick_equality():
    assert event(close="110.00", high="110.01").reason == "CLOSE_BELOW_HIGH"
    assert event(close=110.0, high=110.0).status == "TRUE"
    assert event(close="110.001", high="110.001").reason == "INVALID_RAW_PRICE"


@pytest.mark.parametrize("board", ["ChiNext", "STAR", "BSE"])
def test_non_target_boards_cannot_be_v2_events(board):
    assert event(board=board).status == "EXCLUDED"


def test_special_phase_and_reference_price_fail_closed():
    assert event(listing_phase="IPO_DAY_3").status == "EXCLUDED"
    assert event(listing_phase="RELISTING_DAY_1").status == "EXCLUDED"
    assert event(listing_phase="DELISTING_DAY_1").status == "EXCLUDED"
    assert event().status == "TRUE"  # ordinary post-special session
    assert event(factor="1.000001").reason == "SPECIAL_REFERENCE_PRICE_REQUIRED"
    assert event(is_xr_sec=True).reason == "SPECIAL_REFERENCE_PRICE_REQUIRED"
    assert event(is_xr_sec=None).reason == "MISSING_CORPORATE_ACTION_SCREEN"
    assert event(special_exception="UNKNOWN").reason == "SPECIAL_EXCEPTION_NOT_EXCLUDED"
    assert event(special_exception="CONFIRMED").status == "EXCLUDED"


def test_price_path_inference_requires_qualified_ordinary_old_rule_hit():
    hit = event()
    assert price_path_excludes_5pct(event=hit, board="SZSE Main", day=date(2024, 7, 4))
    assert not price_path_excludes_5pct(event=event(close="105.00", high="105.00"),
                                        board="SSE Main", day=date(2024, 7, 4))
    assert not price_path_excludes_5pct(event=event(special_exception="UNKNOWN"),
                                        board="SSE Main", day=date(2024, 7, 4))
    assert not price_path_excludes_5pct(event=hit, board="SSE Main", day=date(2026, 7, 6))


def test_previous_valid_observation_is_required():
    assert event(previous_raw_close=None).status == "UNRESOLVED"


def test_broad_detector_covers_ordinary_v2_boundary_and_special_reference():
    for close in ("109.91", "109.99", "110.00", "110.09"):
        assert limit_up_like_v2(close=close, previous_raw_close="100.00",
                                reference_special=False)[0]
    assert limit_up_like_v2(close="90.00", previous_raw_close="100.00",
                            reference_special=True)[0]


def test_known_gate_b_ordinary_10pct_rounding_false_negative_is_explicit():
    cases = json.loads((Path(__file__).resolve().parents[1] /
                        "artifacts/stage1_gate_b_retry3/historical_validation_cases.json")
                       .read_text(encoding="utf-8"))["cases"]
    known = next(row for row in cases if row["case"] == "sse_risk_removal_2024_07_04")
    assert known["regime"] == "NORMAL"
    assert known["close_limit_up"] is True
    result = event(close="2.12", high="2.12", previous_raw_close="1.93")
    assert result.status == "FALSE"
    assert result.pct_change < 9.91
