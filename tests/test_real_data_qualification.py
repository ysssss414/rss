"""Fail-closed Stage 1 real-source semantics; no market edge claims."""

from datetime import date, datetime, time
from decimal import Decimal as D

import pytest

from research.entry_signals import EntryPriceEvidence
from research.event_study import ForwardEventStudy, PriceBar, PricePath, StudyEvent
from research.foundation import SHANGHAI, TradingCalendar
from research.real_data_qualification import (
    EffectiveIdentity, SourceCoverage, a_share_common_stock_at, factor_ratio_interval,
    identity_at, maximal_contiguous_intersection, official_ratio_in_vendor_bounds,
    ratio_invariant, reconcile_calendar, reconstructed_ex_reference, snapshot_receipt,
    trading_state,
)


def d(value):
    return date.fromisoformat(value)


def listed(start, end=None, *, kind="A_SHARE_COMMON_STOCK", relisted=None):
    return EffectiveIdentity("S", kind, "SSE", d(start), d(end) if end else None,
                             d(relisted or start))


def test_effective_dated_identity_ignores_future_delisting_and_relisting():
    records = (listed("2020-01-01", "2024-02-01"),
               listed("2025-03-01", relisted="2025-03-01"))
    assert identity_at(records, "S", d("2024-01-31")).latest_listing_or_relisting == d("2020-01-01")
    assert trading_state(identities=records, security_id="S", day=d("2019-12-31"),
                         status=None, bar_present=False) == "NOT_LISTED"
    assert trading_state(identities=records, security_id="S", day=d("2024-02-01"),
                         status=None, bar_present=False) == "LIFECYCLE_ENDED"
    assert identity_at(records, "S", d("2025-03-01")).latest_listing_or_relisting == d("2025-03-01")
    with pytest.raises(ValueError, match="Overlapping"):
        identity_at((listed("2020-01-01"), listed("2021-01-01")), "S", d("2022-01-01"))


def test_security_type_and_trading_state_fail_closed():
    other = (listed("2020-01-01", kind="ETF"),)
    assert not a_share_common_stock_at(other, "S", d("2024-01-02"))
    assert a_share_common_stock_at((listed("2020-01-01"),), "S", d("2024-01-02"))
    cases = (("TRADING", True, "TRADED"), ("SUSPENDED", False, "SUSPENDED"),
             (None, False, "UNKNOWN"), ("TRADING", False, "UNKNOWN"),
             ("SUSPENDED", True, "UNKNOWN"))
    for status, bar, expected in cases:
        assert trading_state(identities=(listed("2020-01-01"),), security_id="S",
                             day=d("2024-01-02"), status=status, bar_present=bar) == expected


def test_factor_ratio_invariance_is_about_ratio_not_absolute_factor():
    precision = D("0.000001")
    assert ratio_invariant((D(2), D(4)), (D(20), D(40)), precision)
    assert not ratio_invariant((D(2), D(4)), (D(20), D(39)), precision)
    low, high = factor_ratio_interval(D("7.857710"), D("8.020492"), precision)
    assert low < D("7.857710") / D("8.020492") < high


@pytest.mark.parametrize("previous,cash,bonus,expected,fpre,fpost", [
    ("1521.50", "30.876", "0", "1490.62", "7.857710", "8.020492"),
    ("251.59", "4.374", "0.1", "224.74", "6.125138", "6.856917"),
])
def test_official_cash_and_bonus_reconstruction(previous, cash, bonus, expected, fpre, fpost):
    reference = reconstructed_ex_reference(previous_close=D(previous),
                                           cash_per_share=D(cash), bonus_per_share=D(bonus))
    assert reference == D(expected)
    assert official_ratio_in_vendor_bounds(
        previous_close=D(previous), reference=reference,
        factor_pre=D(fpre), factor_post=D(fpost), factor_precision=D("0.000001"))


def test_rights_formula_and_impossible_terms():
    assert reconstructed_ex_reference(previous_close=D(100), cash_per_share=D(0),
                                      rights_per_share=D("0.2"), rights_price=D(50)) == D("91.67")
    with pytest.raises(ValueError):
        reconstructed_ex_reference(previous_close=D(1), cash_per_share=D(2))


def test_calendar_exact_reconciliation_and_mismatch_visible():
    sessions = (d("2024-01-02"), d("2024-01-03"), d("2024-01-04"), d("2024-01-05"))
    closures = ((d("2024-01-01"), d("2024-01-01")),)
    report = reconcile_calendar(sessions, d("2024-01-01"), d("2024-01-07"), closures)
    assert report["exact_match"] and report["official_session_count"] == 4
    changed = reconcile_calendar(sessions[:-1], d("2024-01-01"), d("2024-01-07"), closures)
    assert changed["missing_vendor_dates"] == ["2024-01-05"] and not changed["exact_match"]


def test_maximal_contiguous_intersection_requires_every_source_qualified():
    sources = [SourceCoverage("master", d("2024-01-01"), d("2026-01-01"), True),
               SourceCoverage("factor", d("2024-06-01"), d("2025-12-31"), True)]
    assert maximal_contiguous_intersection(sources) == (d("2024-06-01"), d("2025-12-31"))
    assert maximal_contiguous_intersection([*sources, SourceCoverage("status", None, None, False)]) is None
    assert maximal_contiguous_intersection([
        *sources, SourceCoverage("status", d("2024-01-01"), d("2026-01-01"), True,
                                 (d("2025-05-01"),))]) == (d("2024-06-01"), d("2025-04-30"))
    assert maximal_contiguous_intersection([
        SourceCoverage("only", d("2024-01-01"), d("2024-01-01"), True,
                       (d("2024-01-01"),))]) is None


def test_snapshot_receipt_keeps_hashes_and_counts_without_raw_rows():
    receipt = snapshot_receipt(provider="AmazingData", version="1.1.6",
                               retrieved_at="2026-09-23T00:00:00+00:00",
                               request={"codes": ["S"], "start": "2024-01-01"},
                               raw_bytes=b"raw", normalized_bytes=b"normalized",
                               row_count=3, security_count=1,
                               coverage_start=d("2024-01-02"), coverage_end=d("2024-01-04"))
    assert receipt["schema"] == "REAL_RESEARCH_SNAPSHOT_V1"
    assert receipt["raw_sha256"] != receipt["normalized_sha256"]
    assert "raw_rows" not in receipt


def test_future_outcome_action_allowed_but_late_signal_factor_rejected():
    signal_day, entry_day, action_day = d("2024-06-17"), d("2024-06-18"), d("2024-06-19")
    decision = datetime.combine(signal_day, time(15, 10), SHANGHAI)
    prices = EntryPriceEvidence("S", signal_day, "snap", D(99), D(100), D(100),
                                D(1), D(1), datetime.combine(signal_day, time(15), SHANGHAI),
                                datetime.combine(signal_day, time(15), SHANGHAI),
                                datetime.combine(action_day, time(15), SHANGHAI),
                                "a" * 64, True)
    with pytest.raises(ValueError, match="Invalid or unavailable"):
        prices.raw_equivalent(D(100), decision_at=decision)
    calendar = TradingCalendar((signal_day, entry_day, action_day), "qualified-calendar/1")
    event = StudyEvent("E", "S", signal_day, entry_day, D(100), ("DIAGNOSTIC",),
                       "snap", ("ENTRY_COMPARABLE_FORWARD_PATH_V1",))
    bars = (PriceBar(entry_day, D(105), D(95), D(100), adjustment_factor=D(1),
                     factor_available_at=datetime.combine(entry_day, time(15), SHANGHAI)),
            PriceBar(action_day, D(55), D(48), D(50), adjustment_factor=D(2),
                     factor_available_at=datetime.combine(action_day, time(15), SHANGHAI)))
    path = PricePath("S", "snap", bars, (action_day,), (), True, "a" * 64, D(1),
                     datetime.combine(entry_day, time(15), SHANGHAI))
    assert ForwardEventStudy(calendar, (2,)).evaluate(event, path).horizons[0].ret == D(0)
