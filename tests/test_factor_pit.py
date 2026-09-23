from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest

from research.factor_pit import (
    CorporateAction, FactorRatioSnapshot, common_normalization_preserves_ratio, implemented_events,
    ratio_matches, reconstructed_ratio,
)
from research.foundation import TradingCalendar
from research.indicator_prices import AdjustmentFactor, RawCloseObservation, build_pit_adjusted_close
from research.primitives import rsi14
from research.rsi_warmup import plan_rsi_replay


def action(name, kind, effective, factor, *, known="2024-01-02", state="IMPLEMENTED",
           version=1, resolved=True, evidence=None):
    return CorporateAction(name, kind, date.fromisoformat(effective), date.fromisoformat(known),
                           Decimal(factor), state, version, resolved, evidence)


@pytest.mark.parametrize("kind,factor", [
    ("CASH", "1.02"), ("BONUS", "1.10"), ("TRANSFER", "1.40"),
    ("CASH_AND_STOCK", "1.25"), ("RIGHTS", "1.06"),
])
def test_action_types_compose_at_effective_date(kind, factor):
    event = action(kind, kind, "2024-01-03", factor)
    before = Decimal("10.000000")
    after = before * Decimal(factor)
    assert ratio_matches(reconstructed_ratio((event,), date(2024, 1, 2), date(2024, 1, 3)),
                         before, after)
    assert reconstructed_ratio((event,), date(2024, 1, 2), date(2024, 1, 2)) == 1
    assert not ratio_matches(Decimal(factor), before, after)  # Wrong orientation.


def test_multiple_events_and_future_exclusion():
    first = action("first", "CASH", "2024-01-03", "1.02")
    second = action("second", "TRANSFER", "2024-01-04", "1.4")
    later = action("later", "RIGHTS", "2024-01-05", "1.06")
    d, t = date(2024, 1, 2), date(2024, 1, 4)
    assert reconstructed_ratio((first, second), d, t) == reconstructed_ratio(
        (first, second, later), d, t)
    assert ratio_matches(reconstructed_ratio((first, second, later), d, t),
                         Decimal("10"), Decimal("14.28"))


def test_common_normalization_allowed_but_non_common_rejected():
    d, t = date(2024, 1, 2), date(2024, 1, 3)
    original = {d: Decimal("10"), t: Decimal("12")}
    assert common_normalization_preserves_ratio(original,
                                               {d: Decimal("20"), t: Decimal("24")}, d, t)
    assert not common_normalization_preserves_ratio(original,
                                                   {d: Decimal("20"), t: Decimal("25")}, d, t)


def test_changed_proposal_never_admitted_before_final_effective_terms():
    proposal = action("distribution", "BONUS", "2024-01-05", "1.4",
                      state="PROPOSAL", version=1)
    final = action("distribution", "CASH_AND_STOCK", "2024-01-05", "1.401",
                   known="2024-01-04", version=2, evidence="issuer-final-implementation")
    assert implemented_events((proposal,), date(2024, 1, 5)) == ()
    assert reconstructed_ratio((proposal, final), date(2024, 1, 4), date(2024, 1, 4)) == 1
    assert abs(reconstructed_ratio((proposal, final), date(2024, 1, 4), date(2024, 1, 5))
               - Decimal(1) / Decimal("1.401")) < Decimal("1e-27")
    with pytest.raises(ValueError, match="Unresolved"):
        implemented_events((proposal, action("distribution", "CASH_AND_STOCK", "2024-01-05",
                                             "1.401", version=2, resolved=False)), date(2024, 1, 5))
    with pytest.raises(ValueError, match="Unresolved"):
        implemented_events((proposal, action("distribution", "CASH_AND_STOCK", "2024-01-05",
                                             "1.401", version=2)), date(2024, 1, 5))


def test_late_correction_and_conflicting_versions_fail_closed():
    late = action("late", "CASH", "2024-01-03", "1.02", known="2024-01-04")
    with pytest.raises(ValueError, match="late"):
        reconstructed_ratio((late,), date(2024, 1, 2), date(2024, 1, 3))
    same_day = action("same_day", "CASH", "2024-01-03", "1.02", known="2024-01-03")
    with pytest.raises(ValueError, match="late"):
        reconstructed_ratio((same_day,), date(2024, 1, 2), date(2024, 1, 3))
    first = action("same", "CASH", "2024-01-03", "1.02")
    with pytest.raises(ValueError, match="Conflicting"):
        implemented_events((first, first), date(2024, 1, 3))


def test_unresolved_future_revision_cannot_affect_earlier_ratio():
    early = action("early", "CASH", "2024-01-03", "1.02")
    future = action("future", "CASH", "2024-01-05", "1.5",
                    known="2024-01-10", resolved=False)
    assert reconstructed_ratio((early,), date(2024, 1, 2), date(2024, 1, 3)) == reconstructed_ratio(
        (early, future), date(2024, 1, 2), date(2024, 1, 3))


def test_superseded_effective_date_is_not_applied():
    proposal = action("revised", "BONUS", "2024-01-03", "1.4", state="PROPOSAL")
    final = action("revised", "CASH_AND_STOCK", "2024-01-05", "1.401",
                   known="2024-01-04", version=2, evidence="issuer-final-implementation")
    assert reconstructed_ratio((proposal, final), date(2024, 1, 2), date(2024, 1, 3)) == 1
    assert reconstructed_ratio((proposal, final), date(2024, 1, 2), date(2024, 1, 5)) < 1


def test_vendor_changed_final_only_row_requires_independent_revision_evidence():
    final = CorporateAction("changed", "CASH_AND_STOCK", date(2024, 1, 5),
                            date(2024, 1, 4), Decimal("1.401"), "IMPLEMENTED", 1,
                            True, None, True)
    with pytest.raises(ValueError, match="Unresolved"):
        implemented_events((final,), date(2024, 1, 5))


def test_factor_ratio_snapshot_rejects_future_dependent_scaling():
    d, t = date(2024, 1, 2), date(2024, 1, 3)
    event = action("cash", "CASH", "2024-01-03", "1.2")
    original = FactorRatioSnapshot("TEST.SH", "frozen", "a" * 64,
                                   {d: Decimal("10"), t: Decimal("12")}, (event,))
    assert abs(original.ratio(d, t) - Decimal("10") / Decimal("12")) < Decimal("1e-27")
    common = FactorRatioSnapshot("TEST.SH", "common", "b" * 64,
                                 {d: Decimal("20"), t: Decimal("24")}, (event,))
    assert abs(common.ratio(d, t) - original.ratio(d, t)) < Decimal("1e-27")
    non_common = FactorRatioSnapshot("TEST.SH", "noncommon", "c" * 64,
                                     {d: Decimal("20"), t: Decimal("25")}, (event,))
    with pytest.raises(ValueError, match="disagrees"):
        non_common.ratio(d, t)


def test_factor_ratio_drives_pit_adjusted_price_and_full_prefix_rsi():
    # End-to-end contract test; real-data adapter smoke is recorded separately.
    sh = timezone(timedelta(hours=8))
    days = tuple(date(2024, 1, n) for n in (2, 3, 4, 5))
    calendar = TradingCalendar(days, "test-calendar")
    raw = [RawCloseObservation("TEST.SH", day, str(Decimal(10 + i)), "TRADING", True,
                               f"{day.isoformat()}T15:30:00+08:00", "test-snapshot")
           for i, day in enumerate(days)]
    factors = [AdjustmentFactor("TEST.SH", day, value, f"{day.isoformat()}T15:30:00+08:00",
                                "test-snapshot", "a" * 64, True)
               for day, value in zip(days, ("1", "1.1", "1.1", "1.1"))]
    cutoff = datetime(2024, 1, 5, 16, tzinfo=sh)
    series = build_pit_adjusted_close(security_id="TEST.SH", as_of_date=days[-1],
                                      decision_at=cutoff, reset_date=days[0],
                                      source_snapshot_id="test-snapshot", factor_schema="daily",
                                      calendar=calendar, raw=raw, factors=factors)
    assert series.observations[0].price == Decimal(10) / Decimal("1.1")
    plan = plan_rsi_replay(series, full_prefix_available=True)
    assert plan.status == "READY" and plan.canonical
    assert rsi14(plan.series, decision_at=cutoff).status == "READY"
