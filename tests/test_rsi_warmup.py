"""RSI warm-up policy and deterministic convergence validation."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from research.dependencies import (
    GRAPH_VERSION,
    REGISTRY,
    RSI_WARMUP_POLICY,
    PrimitiveRunContext,
    required_history,
)
from research.foundation import SHANGHAI
from research.indicator_prices import IndicatorPrice, IndicatorPriceSeries, PRICE_BASIS
from research.primitives import rsi14
from research.rsi_warmup import (
    FULL_AVAILABLE_PREFIX,
    POLICY,
    TRUNCATED_SLICE,
    plan_rsi_replay,
)
from scripts.validate_stage1_trade_lifecycle_exit import (
    build_convergence_report,
    verify_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "synthetic-warmup-snapshot"


def series(count):
    start = date(2020, 1, 1)
    pattern = tuple(Decimal(value) for value in ("1", "1", "-0.8"))
    price = Decimal("100")
    rows = []
    for index in range(count):
        if index:
            price += pattern[(index - 1) % len(pattern)]
        day = start + timedelta(days=index)
        rows.append(IndicatorPrice(
            "SYNTHETIC", day, price, PRICE_BASIS,
            datetime.combine(day, time(15), SHANGHAI),
            SNAPSHOT, True, "TRADING",
        ))
    return IndicatorPriceSeries(
        "SYNTHETIC", rows[-1].trade_date, rows[0].trade_date,
        PRICE_BASIS, SNAPSHOT, "synthetic-calendar/1",
        "effective_events", tuple(rows),
    )


def rsi(replay):
    return rsi14(
        replay,
        decision_at=datetime.combine(replay.as_of_date, time(15, 10), SHANGHAI),
    )


def test_dependency_graph_expresses_readiness_prefix_and_truncated_policy():
    spec = REGISTRY["RSI14_PROJECT_V1"]
    assert GRAPH_VERSION == "STAGE1_PRIMITIVE_GRAPH_V2"
    assert spec.minimum_observations == POLICY.minimum_ready_observations == 2
    assert spec.requires_full_prefix is True
    assert spec.replay_origin == "LATEST_LISTING_OR_RELISTING"
    assert spec.truncated_slice_min_warmup == 120
    assert spec.truncated_slice_default_warmup == 150
    context = PrimitiveRunContext(SNAPSHOT, "1")
    assert context.rsi_warmup_policy == RSI_WARMUP_POLICY
    assert context.rsi_truncated_slice_min_warmup == 120
    assert context.rsi_truncated_slice_default_warmup == 150


@pytest.mark.parametrize("count", [80, 135, 180])
def test_full_available_prefix_is_canonical_even_when_listing_history_under_120(count):
    full = series(count)
    plan = plan_rsi_replay(full, full_prefix_available=True)
    assert plan.mode == FULL_AVAILABLE_PREFIX
    assert plan.status == "READY" and plan.canonical is True
    assert plan.selected_observations == count
    assert plan.series.observations == full.observations
    assert rsi(plan.series).ready


def test_warmup_policy_is_not_historical_universe_eligibility():
    requirements = required_history(("RSI14_PROJECT_V1",))
    assert requirements.price_history_required == 2
    assert requirements.base_universe_minimum == 2
    assert requirements.truncated_slice_min_warmup == 120
    assert requirements.truncated_slice_default_warmup == 150
    assert POLICY.affects_historical_universe_eligibility is False


@pytest.mark.parametrize("window", [60, 90])
def test_below_120_is_diagnostic_only_not_an_admissible_truncated_warmup(window):
    with pytest.raises(ValueError, match="at least 120"):
        plan_rsi_replay(
            series(200), full_prefix_available=False,
            warmup_observations=window,
        )


@pytest.mark.parametrize(("available", "selected", "target_met", "status"), [
    (80, 80, False, "INSUFFICIENT_TRUNCATED_WARMUP"),
    (135, 135, False, "READY"),
    (180, 150, True, "READY"),
])
def test_default_150_target_with_120_minimum(available, selected, target_met, status):
    plan = plan_rsi_replay(series(available), full_prefix_available=False)
    assert plan.mode == TRUNCATED_SLICE and plan.canonical is False
    assert plan.status == status
    assert plan.selected_observations == selected
    assert plan.target_met is target_met
    assert (plan.series is not None) is (status == "READY")


@pytest.mark.parametrize("window", [120, 150, 180])
def test_explicit_admissible_truncated_warmups_select_exact_suffix(window):
    plan = plan_rsi_replay(
        series(220), full_prefix_available=False,
        warmup_observations=window,
    )
    assert plan.status == "READY"
    assert plan.selected_observations == window
    assert len([row for row in plan.series.observations
                if row.state != "SUSPENDED"]) == window
    assert rsi(plan.series).ready


def test_available_full_prefix_cannot_be_silently_truncated_to_150():
    with pytest.raises(ValueError, match="cannot truncate"):
        plan_rsi_replay(
            series(220), full_prefix_available=True,
            warmup_observations=150,
        )


def test_deterministic_convergence_artifact_includes_threshold_decisions():
    actual = build_convergence_report()
    frozen = json.loads((
        ROOT / "artifacts/stage1_trade_lifecycle_exit/rsi_convergence_validation.json"
    ).read_bytes())
    assert actual == frozen
    assert list(actual["overall"]) == [
        "WARMUP_60", "WARMUP_90", "WARMUP_120", "WARMUP_150", "WARMUP_180",
    ]
    for window in actual["overall"].values():
        assert set(window) == {
            "sample_count", "max_absolute_error", "p95_absolute_error",
            "classification_mismatch_gt_70",
            "classification_mismatch_lt_70", "recross_70_mismatch",
        }
    threshold = actual["by_series"]["threshold_near_70"]
    assert threshold["WARMUP_120"]["sample_count"] > 0
    assert actual["algorithm_modified"] is False


def test_trade_lifecycle_exit_manifest_integrity():
    assert verify_manifest() == 24
