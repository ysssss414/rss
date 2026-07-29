from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


CANDIDATE_COLUMNS = [
    "candidate_cycle_id",
    "ts_code",
    "stock_name",
    "sequence_start_date",
    "initial_board_count",
    "qualified_date",
    "qualified_board_count",
    "last_limit_up_date",
    "max_board_count",
    "current_stage",
    "observation_days",
    "latest_trade_date",
    "latest_close",
    "latest_ma5",
    "latest_rsi14",
    "first_ma5_touch_date",
    "first_ma5_touch_close_recovered",
    "md1_date",
    "md1_result",
    "below70_start_date",
    "below70_trading_days",
    "below70_min_rsi",
    "md2_date",
    "md2_result",
    "cycle_expiry_date",
    "invalid_reason",
    "data_complete",
]

SIGNAL_COLUMNS = [
    "signal_date",
    "candidate_cycle_id",
    "ts_code",
    "stock_name",
    "signal_type",
    "qualified_date",
    "qualified_board_count",
    "last_limit_up_date",
    "max_board_count",
    "observation_day",
    "rsi14",
    "previous_rsi14",
    "close",
    "high",
    "low",
    "ma5",
    "below70_trading_days",
    "below70_min_rsi",
    "price_adjustment",
    "run_as_of",
]

CURRENT_STAGES = {
    "NOT_QUALIFIED",
    "LIMIT_UP_ACTIVE",
    "WAITING_MD1",
    "WAITING_MD2",
    "WAITING_BOTH",
    "SIGNAL_TRIGGERED",
    "INVALID",
    "EXPIRED",
}


@dataclass
class CycleState:
    candidate_cycle_id: str
    ts_code: str
    stock_name: str
    sequence_start_date: date
    initial_board_count: int
    last_limit_up_date: date
    max_board_count: int
    qualified_date: date | None = None
    qualified_board_count: int | None = None
    current_stage: str = "NOT_QUALIFIED"
    observation_days: int = 0
    latest_trade_date: date | None = None
    latest_close: float | None = None
    latest_ma5: float | None = None
    latest_rsi14: float | None = None
    first_ma5_touch_date: date | None = None
    first_ma5_touch_close_recovered: bool | None = None
    md1_date: date | None = None
    md1_result: str = "NOT_REACHED"
    below70_start_date: date | None = None
    below70_trading_days: int = 0
    below70_min_rsi: float | None = None
    below70_active: bool = False
    md2_date: date | None = None
    md2_result: str = "NO_REBREAK"
    cycle_expiry_date: date | None = None
    invalid_reason: str = ""
    data_complete: bool = True

    @property
    def qualified(self) -> bool:
        return self.qualified_date is not None

    def as_row(self) -> dict[str, Any]:
        row = {
            field: getattr(self, field)
            for field in CANDIDATE_COLUMNS
            if field != "latest_rsi14"
        }
        row["latest_rsi14"] = self.latest_rsi14
        return row


@dataclass(frozen=True)
class ReplayResult:
    candidate_cycles: pd.DataFrame
    signals: pd.DataFrame
