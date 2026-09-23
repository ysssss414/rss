"""RSI replay-origin and truncated-slice warm-up policy; no strategy eligibility."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .dependencies import RSI_WARMUP_POLICY
from .indicator_prices import IndicatorPriceSeries, PRICE_BASIS


FULL_AVAILABLE_PREFIX = "FULL_AVAILABLE_PREFIX"
TRUNCATED_SLICE = "TRUNCATED_SLICE"


@dataclass(frozen=True)
class RsiWarmupPolicy:
    policy_id: str = RSI_WARMUP_POLICY
    minimum_ready_observations: int = 2
    requires_full_prefix: bool = True
    canonical_replay_origin: str = "LATEST_LISTING_OR_RELISTING"
    truncated_slice_min_warmup: int = 120
    truncated_slice_default_warmup: int = 150
    affects_historical_universe_eligibility: bool = False


POLICY = RsiWarmupPolicy()


@dataclass(frozen=True)
class RsiReplayPlan:
    policy_id: str
    mode: str
    status: str
    available_observations: int
    selected_observations: int
    requested_warmup_observations: int | None
    target_met: bool
    canonical: bool
    series: IndicatorPriceSeries | None


def plan_rsi_replay(
    series: IndicatorPriceSeries,
    *,
    full_prefix_available: bool,
    warmup_observations: int | None = None,
) -> RsiReplayPlan:
    """Select a canonical full prefix or an explicitly non-canonical warm-up slice."""
    if type(full_prefix_available) is not bool:
        raise ValueError("full_prefix_available must be explicit")
    rows = tuple(sorted(series.observations, key=lambda row: row.trade_date))
    if (series.price_basis != PRICE_BASIS or not series.security_id
            or not series.source_snapshot_id or series.reset_date > series.as_of_date
            or rows != series.observations
            or len({row.trade_date for row in rows}) != len(rows)
            or any(row.security_id != series.security_id
                   or row.source_snapshot_id != series.source_snapshot_id
                   or row.price_basis != PRICE_BASIS
                   or not series.reset_date <= row.trade_date <= series.as_of_date
                   for row in rows)):
        raise ValueError("Invalid RSI price-series lineage")
    trading = tuple(row for row in rows if row.state != "SUSPENDED")
    available = len(trading)

    if full_prefix_available:
        if warmup_observations is not None:
            raise ValueError("A fixed warm-up cannot truncate an available full prefix")
        status = "READY" if available >= POLICY.minimum_ready_observations else "NOT_ENOUGH_HISTORY"
        return RsiReplayPlan(
            POLICY.policy_id, FULL_AVAILABLE_PREFIX, status, available, available,
            None, True, True, series,
        )

    target = (POLICY.truncated_slice_default_warmup
              if warmup_observations is None else warmup_observations)
    if type(target) is not int or target < POLICY.truncated_slice_min_warmup:
        raise ValueError("Truncated RSI warm-up must be at least 120 observations")
    selected = min(available, target)
    if selected < POLICY.truncated_slice_min_warmup:
        return RsiReplayPlan(
            POLICY.policy_id, TRUNCATED_SLICE, "INSUFFICIENT_TRUNCATED_WARMUP",
            available, selected, target, False, False, None,
        )
    start = trading[-selected].trade_date
    sliced = replace(
        series,
        reset_date=start,
        observations=tuple(row for row in rows if row.trade_date >= start),
    )
    return RsiReplayPlan(
        POLICY.policy_id, TRUNCATED_SLICE, "READY", available, selected,
        target, available >= target, False, sliced,
    )
