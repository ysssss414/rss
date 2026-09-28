"""The V3 limit is an exact tick calculation, never a percentage band."""

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

from research.close_limit_up_v3 import (
    evaluate_close_limit_up_v3, price_cents, price_path_excludes_5pct_v2,
    upper_limit_cents,
)


def event(**changes):
    row = dict(board="SSE Main", listing_phase="NORMAL_LISTED",
               close="2.12", high="2.12", previous_raw_close="1.93",
               factor="48.081333", previous_factor="48.081333",
               is_xr_sec=False, is_wd_sec=False, special_exception="CLEARED")
    row.update(changes)
    return evaluate_close_limit_up_v3(**row)


@pytest.mark.parametrize("previous,expected", [
    ("0.50", 55), ("0.51", 56), ("0.99", 109), ("1.00", 110),
    ("1.01", 111), ("1.50", 165), ("1.93", 212), ("2.00", 220),
    ("3.33", 366), ("5.00", 550), ("10.00", 1100),
    ("20.00", 2200), ("50.00", 5500), ("100.00", 11000),
])
def test_requested_low_price_matrix_uses_exact_integer_cents(previous, expected):
    cents = price_cents(previous)
    assert upper_limit_cents(cents) == expected
    oracle = (Decimal(cents) * Decimal("1.10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    assert expected == max(int(oracle), cents + 1)


def test_half_cent_and_one_tick_boundary_are_not_binary_round():
    assert upper_limit_cents(105) == 116  # 1.05 * 1.10 = 1.155 -> 1.16
    assert upper_limit_cents(115) == 127  # 1.15 * 1.10 = 1.265 -> 1.27
    assert upper_limit_cents(1) == 2      # one-tick minimum
    assert price_cents("2.1201") is None


def test_fixed_v2_band_misses_natural_low_price_limits():
    for cents in (51, 99, 101, 193):
        implied_pct = (Decimal(upper_limit_cents(cents)) / cents - 1) * 100
        assert implied_pct < Decimal("9.91") or implied_pct > Decimal("10.09")


def test_600518_official_gate_b_regression():
    cases = json.loads((Path(__file__).resolve().parents[1] /
                        "artifacts/stage1_gate_b_retry3/historical_validation_cases.json")
                       .read_text(encoding="utf-8"))["cases"]
    known = next(row for row in cases if row["case"] == "sse_risk_removal_2024_07_04")
    assert known["regime"] == "NORMAL" and known["close_limit_up"] is True
    result = event()
    assert result.reference_day.classification == "ORDINARY"
    assert result.canonical_limit_up_cents == 212
    assert result.status == "TRUE"


def test_exact_close_and_high_predicate():
    assert event(close="2.11", high="2.12").status == "FALSE"
    assert event(close="2.12", high="2.13").reason == "CLOSE_BELOW_HIGH"
    assert event(close="2.13", high="2.13").status == "FALSE"
    assert event(close="2.1201", high="2.1201").status == "UNRESOLVED"


@pytest.mark.parametrize("board", ["ChiNext", "STAR", "BSE"])
def test_non_target_boards_excluded(board):
    assert event(board=board).status == "EXCLUDED"


def test_special_reference_and_no_limit_fail_closed():
    assert event(listing_phase="IPO_DAY_1").status == "EXCLUDED"
    assert event(listing_phase="RELISTING_DAY_1").status == "EXCLUDED"
    assert event(listing_phase="DELISTING_DAY_1").status == "EXCLUDED"
    assert event(factor="48.081334").reason == "SPECIAL_REFERENCE_PRICE_REQUIRED"
    assert event(is_xr_sec=True).reason == "SPECIAL_REFERENCE_PRICE_REQUIRED"
    assert event(is_xr_sec=None).status == "UNRESOLVED"
    assert event(is_wd_sec=None).status == "UNRESOLVED"
    assert event(is_wd_sec=True).status == "UNRESOLVED"
    assert event(price_tick="0.005").status == "UNRESOLVED"
    assert event(special_exception="CONFIRMED").status == "EXCLUDED"
    assert event(special_exception="UNKNOWN").status == "UNRESOLVED"


def test_price_path_5pct_inference_requires_separated_tick_limits():
    assert price_path_excludes_5pct_v2(event=event(), board="SSE Main", day=date(2024, 7, 4))
    very_low = event(previous_raw_close="0.10", close="0.11", high="0.11")
    assert very_low.status == "TRUE"
    assert upper_limit_cents(10, 500) == upper_limit_cents(10, 1000) == 11
    assert not price_path_excludes_5pct_v2(event=very_low, board="SSE Main", day=date(2024, 7, 4))
    assert not price_path_excludes_5pct_v2(event=event(), board="SSE Main", day=date(2026, 7, 6))
    assert not price_path_excludes_5pct_v2(event=event(special_exception="UNKNOWN"),
                                            board="SSE Main", day=date(2024, 7, 4))


def test_post_2026_risk_warning_10pct_price_is_evaluable():
    assert event(previous_raw_close="1.47", close="1.62", high="1.62").status == "TRUE"


def test_broad_five_percent_floor_covers_all_positive_cent_references():
    for cents in range(1, 100_001):
        assert upper_limit_cents(cents) * 20 >= cents * 21  # return >= 5%
