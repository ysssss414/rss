"""Versioned causal dependencies for Stage 1 primitives, without strategy decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .indicator_prices import PRICE_BASIS


GRAPH_VERSION = "STAGE1_PRIMITIVE_GRAPH_V1"


@dataclass(frozen=True)
class PrimitiveSpec:
    primitive_id: str
    dependencies: tuple[str, ...]
    observation_kind: str
    minimum_observations: int
    price_basis: str
    replay_scope: str
    availability_rule: str = "MAX_INPUT_AVAILABLE_AT_AFTER_T_EOD"


REGISTRY = {
    "IS_CLOSE_LIMIT_UP_V1": PrimitiveSpec(
        "IS_CLOSE_LIMIT_UP_V1", ("RAW_TRADING_CLOSE", "DAILY_TRADING_CONSTRAINT"),
        "limit_constraint", 1, "RAW_TRADING_PRICE", "T_OBSERVATION"),
    "LIMIT_UP_COUNT_5_V1": PrimitiveSpec(
        "LIMIT_UP_COUNT_5_V1", ("IS_CLOSE_LIMIT_UP_V1", "QUALIFIED_10_PERCENT_WINDOW"),
        "limit_constraint", 5, "RAW_TRADING_PRICE", "LAST_5_QUALIFIED_OBSERVATIONS"),
    "MA5_SMA_V1": PrimitiveSpec(
        "MA5_SMA_V1", ("INDICATOR_PRICE_SERIES",),
        "indicator_price", 5, PRICE_BASIS, "LAST_5_VALID_OBSERVATIONS"),
    "RSI14_TONGHUASHUN_V1": PrimitiveSpec(
        "RSI14_TONGHUASHUN_V1", ("INDICATOR_PRICE_SERIES",),
        "indicator_price", 2, PRICE_BASIS, "ALL_VALID_OBSERVATIONS_SINCE_RESET"),
}


@dataclass(frozen=True)
class HistoryRequirements:
    price_history_required: int
    limit_constraint_history_required: int
    base_universe_minimum: int
    price_replay_scope: str
    dependency_graph_version: str


def required_history(enabled: tuple[str, ...]) -> HistoryRequirements:
    if len(set(enabled)) != len(enabled) or any(name not in REGISTRY for name in enabled):
        raise ValueError("Unknown or repeated primitive")
    specs = [REGISTRY[name] for name in enabled]
    price = max((spec.minimum_observations for spec in specs
                 if spec.observation_kind == "indicator_price"), default=0)
    constraint = max((spec.minimum_observations for spec in specs
                      if spec.observation_kind == "limit_constraint"), default=0)
    replay = ("ALL_VALID_OBSERVATIONS_SINCE_RESET"
              if "RSI14_TONGHUASHUN_V1" in enabled else "FINITE_WINDOW")
    return HistoryRequirements(price, constraint, max(price, constraint), replay, GRAPH_VERSION)


@dataclass(frozen=True)
class PrimitiveRunContext:
    data_snapshot_id: str
    universe_contract_version: str
    indicator_price_basis_version: str = PRICE_BASIS
    is_close_limit_up_version: str = "IS_CLOSE_LIMIT_UP_V1"
    limit_up_count_version: str = "LIMIT_UP_COUNT_5_V1"
    rsi_version: str = "RSI14_TONGHUASHUN_V1"
    ma_version: str = "MA5_SMA_V1"
    dependency_graph_version: str = GRAPH_VERSION

    def __post_init__(self) -> None:
        if (not self.data_snapshot_id or self.universe_contract_version != "1"
                or self.indicator_price_basis_version != PRICE_BASIS
                or self.is_close_limit_up_version not in REGISTRY
                or self.limit_up_count_version not in REGISTRY
                or self.rsi_version not in REGISTRY or self.ma_version not in REGISTRY
                or self.dependency_graph_version != GRAPH_VERSION):
            raise ValueError("Incomplete or unqualified primitive run context")
