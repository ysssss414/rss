from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .contracts import BAR_COLUMNS, RAW_PRICES, SCHEMA_VERSION, STATUSES, DataContractError, DataRequest


def validate_calendar(values, start: date, end: date) -> list[date]:
    try:
        dates = [pd.Timestamp(str(v)).date() for v in values]
    except (ValueError, TypeError) as exc:
        raise DataContractError("INVALID_DATE", "Invalid calendar date") from exc
    if any(pd.isna(d) for d in dates) or dates != sorted(set(dates)):
        raise DataContractError("INVALID_CALENDAR", "Calendar must be unique and ascending")
    if any(d < start or d > end for d in dates):
        raise DataContractError("AS_OF_VIOLATION", "Calendar outside requested interval")
    return dates


def validate_timestamps(values: pd.Series, field: str) -> pd.Series:
    try:
        times = pd.to_datetime(values, utc=True, format="mixed", errors="raise")
    except (ValueError, TypeError) as exc:
        raise DataContractError("INVALID_AVAILABILITY", f"Invalid {field}") from exc
    if times.isna().any() or not values.astype(str).str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).all():
        raise DataContractError("INVALID_AVAILABILITY", f"{field} requires an explicit timezone and non-null timestamp")
    return times


def available_mask(frame: pd.DataFrame, cutoff: date) -> pd.Series:
    """Only explicitly published data available by the Shanghai EOD cutoff."""
    if "available_at" not in frame:
        raise DataContractError("INVALID_AVAILABILITY", "Missing available_at")
    times = validate_timestamps(frame["available_at"], "available_at")
    boundary = (pd.Timestamp(cutoff).tz_localize("Asia/Shanghai") + pd.Timedelta(days=1))
    return times < boundary


def validate_price_values(result: pd.DataFrame, trading: pd.Series) -> pd.DataFrame:
    """Shared numeric policy; legacy CSVs can omit volume but cannot invent it."""
    numeric = RAW_PRICES + (["volume"] if "volume" in result else []) + ["amount"]
    for column in numeric:
        try:
            result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
        except (ValueError, TypeError) as exc:
            raise DataContractError("INVALID_NUMBER", f"Non-numeric {column}") from exc
        if np.isinf(result[column]).any():
            raise DataContractError("INVALID_NUMBER", f"Infinite {column}")
    valid = result.loc[trading]
    if valid[numeric].isna().any().any():
        raise DataContractError("MISSING_VALUE", "Trading bars require all supplied OHLCV/amount fields")
    if (result[RAW_PRICES].le(0).any().any()
            or result[[c for c in ("volume", "amount") if c in result]].lt(0).any().any()
            or (result.raw_high < result[["raw_open", "raw_close", "raw_low"]].max(axis=1)).any()
            or (result.raw_low > result[["raw_open", "raw_close", "raw_high"]].min(axis=1)).any()):
        raise DataContractError("INVALID_OHLC", "Invalid prices or trading values")
    return result


def validate_bars(frame: pd.DataFrame, request: DataRequest) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise DataContractError("SCHEMA_MISMATCH", "Missing fields: " + ", ".join(sorted(missing)))
    result = frame[BAR_COLUMNS].copy()
    if result.empty:
        result.attrs["identical_duplicates_removed"] = 0
        return result
    if result.ts_code.isna().any() or not result.ts_code.isin(request.codes).all():
        raise DataContractError("SYMBOL_MISMATCH", "Response identity differs from requested securities")
    try:
        dates = pd.to_datetime(result.trade_date, format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError) as exc:
        raise DataContractError("INVALID_DATE", "Invalid bar date") from exc
    if dates.isna().any():
        raise DataContractError("INVALID_DATE", "Missing bar date")
    result["trade_date"] = dates.dt.date.map(date.isoformat)
    if not dates.dt.date.between(request.start, request.end).all() or not available_mask(result, request.end).all():
        raise DataContractError("AS_OF_VIOLATION", "Response contains unavailable or out-of-range data")
    if not result.trading_status.isin(STATUSES).all():
        raise DataContractError("INVALID_STATUS", "Unrecognized trading status")
    for column in ("source", "schema_version", "retrieved_at", "quality_status"):
        if result[column].isna().any() or result[column].astype(str).str.strip().eq("").any():
            raise DataContractError("SCHEMA_MISMATCH", f"Missing {column}")
    if not result.schema_version.eq(SCHEMA_VERSION).all():
        raise DataContractError("SCHEMA_MISMATCH", "Unsupported schema version")
    validate_timestamps(result.retrieved_at, "retrieved_at")
    if not result.volume_unit.eq("share").all() or not result.amount_unit.eq("CNY").all():
        raise DataContractError("UNSUPPORTED_UNIT", "Expected volume=share and amount=CNY")
    trading = result.trading_status.eq("TRADING")
    suspended = result.trading_status.eq("SUSPENDED")
    unknown = result.trading_status.eq("UNKNOWN")
    # Do not silently coerce strings (including "false") to truthy booleans.
    if any(not (isinstance(v, (bool, np.bool_)) or pd.isna(v)) for v in result.suspended):
        raise DataContractError("INVALID_STATUS", "suspended must be boolean or null")
    if (result.loc[trading, "suspended"].isna().any()
            or result.loc[trading, "suspended"].eq(True).any()
            or result.loc[suspended, "suspended"].isna().any()
            or result.loc[suspended, "suspended"].eq(False).any()
            or result.loc[unknown, "suspended"].notna().any()):
        raise DataContractError("INVALID_STATUS", "Status/suspended disagreement")
    result = validate_price_values(result, trading)
    duplicates = result.duplicated(["ts_code", "trade_date"], keep=False)
    # Transport timestamps/provenance do not turn identical observations into conflicts.
    observation = [c for c in BAR_COLUMNS if c not in {"snapshot_id", "retrieved_at"}]
    for _, group in result.loc[duplicates].groupby(["ts_code", "trade_date"]):
        if len(group[observation].drop_duplicates()) != 1:
            raise DataContractError("CONFLICTING_DUPLICATE", "Conflicting security/date observations")
    removed = int(result.duplicated(["ts_code", "trade_date"]).sum())
    result = result.sort_values(["ts_code", "trade_date", "retrieved_at"], kind="stable").drop_duplicates(["ts_code", "trade_date"]).reset_index(drop=True)
    result.attrs["identical_duplicates_removed"] = removed
    return result
