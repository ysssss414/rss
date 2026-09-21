from __future__ import annotations

from dataclasses import dataclass
from datetime import date


SCHEMA_VERSION = "daily-bars/1"
QUALITY_POLICY_VERSION = "stage0/1"
STATUSES = {"TRADING", "SUSPENDED", "NOT_LISTED", "DELISTED", "UNKNOWN"}
RAW_PRICES = ["raw_open", "raw_high", "raw_low", "raw_close"]
BAR_COLUMNS = [
    "ts_code", "trade_date", *RAW_PRICES, "volume", "amount",
    "trading_status", "suspended", "available_at", "source", "schema_version",
    "snapshot_id", "retrieved_at", "quality_status", "volume_unit", "amount_unit",
]


class DataContractError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        if message is None:
            code, message = "DATA_SOURCE_ERROR", code
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DataRequest:
    codes: tuple[str, ...]
    start: date
    end: date

    def __post_init__(self):
        if self.start > self.end or not self.codes or len(set(self.codes)) != len(self.codes):
            raise DataContractError("INVALID_ARGUMENT", "Invalid date range or securities")
        if any(not code.endswith((".SH", ".SZ", ".BJ")) or len(code) != 9
               or not code[:6].isdigit() for code in self.codes):
            raise DataContractError("INVALID_ARGUMENT", "Exchange-qualified security codes required")


@dataclass(frozen=True)
class AnalysisWindow:
    """Daily EOD cutoffs in Asia/Shanghai; intraday decisions are unsupported."""
    calculation_start: date
    signal_start: date
    signal_end: date
    decision_as_of: date
    outcome_as_of: date

    def __post_init__(self):
        if not (self.calculation_start <= self.signal_start <= self.signal_end
                <= self.decision_as_of <= self.outcome_as_of):
            raise DataContractError("INVALID_ARGUMENT", "Invalid decision/outcome boundaries")
