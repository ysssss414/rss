"""Preserve BaoStock raw fields and grade causal, unadjusted observations."""
from __future__ import annotations

from decimal import Decimal
import math

import pandas as pd

from research.data.validation import validate_price_values
from .baostock_v0_5 import FIELDS, MAP
from .capture_v0_4 import bounded_request


def validate_bars(raw, code, start, end):
    bounded_request(code, start, end, "bars")
    if list(raw.columns) != FIELDS.split(","):
        raise ValueError("VENDOR_FIELDS_MISMATCH")
    frame = raw.copy()
    if frame.empty:
        return frame, {"status": "EMPTY_RESPONSE", "valid_bars": 0, "suspended": 0}
    if not frame.code.eq(MAP[code]).all() or not frame.date.between(start, end).all():
        raise ValueError("VENDOR_RESPONSE_OUT_OF_SCOPE")
    dates = pd.to_datetime(frame.date, format="%Y-%m-%d", errors="raise")
    if dates.isna().any() or frame.date.duplicated().any():
        raise ValueError("INVALID_OR_DUPLICATE_SECURITY_DAY")
    if not frame.adjustflag.eq("3").all() or not frame.tradestatus.isin(["0", "1"]).all() or not frame.isST.isin(["0", "1"]).all():
        raise ValueError("INVALID_ADJUSTMENT_OR_STATUS")
    for field in ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg"]:
        values = frame[field].replace("", float("nan"))
        frame[field] = pd.to_numeric(values, errors="raise").astype(float)
        if any(math.isinf(v) for v in frame[field]):
            raise ValueError("NONFINITE_VENDOR_NUMBER")
    trading = frame.tradestatus.eq("1")
    required = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg"]
    if frame.loc[trading, required].isna().any().any() or frame.preclose.isna().any() or (frame.preclose <= 0).any():
        raise ValueError("MISSING_VALID_TRADING_NUMBER")
    validate_price_values(frame.rename(columns={p: f"raw_{p}" for p in ("open", "high", "low", "close")}), trading)
    if (frame.loc[trading, ["volume", "amount"]] <= 0).any().any() or (frame.loc[trading, "turn"] < 0).any():
        raise ValueError("INVALID_TRADING_LIQUIDITY")
    suspended = ~trading
    if frame.loc[suspended, ["volume", "amount"]].ne(0).any().any():
        raise ValueError("SUSPENSION_LIQUIDITY_CONFLICT")
    if not frame.loc[suspended, ["open", "high", "low", "close"]].eq(frame.loc[suspended, "preclose"], axis=0).all().all():
        raise ValueError("SUSPENSION_PRICE_CONFLICT")
    if ((frame.loc[trading, "close"] / frame.loc[trading, "preclose"] - 1)*100-frame.loc[trading, "pctChg"]).abs().gt(0.000002).any():
        raise ValueError("PCT_CHANGE_REFERENCE_CONFLICT")
    return frame.sort_values("date").reset_index(drop=True), {"status": "RAW_DATA_QUALIFIED",
        "valid_bars": int(trading.sum()), "suspended": int(suspended.sum()), "rows": len(frame),
        "actual_start": frame.date.min(), "actual_end": frame.date.max(),
        "units_basis": "VENDOR_DECLARATION;PRICE_CNY;VOLUME_SHARE;AMOUNT_CNY;TURN_PERCENT",
        "strict_pit_execution": "UNQUALIFIED"}


def validate_calendar(raw, start, end):
    if list(raw.columns) != ["calendar_date", "is_trading_day"] or raw.empty:
        raise ValueError("EMPTY_OR_INVALID_CALENDAR")
    if raw.calendar_date.duplicated().any() or not raw.calendar_date.between(start, end).all() or not raw.is_trading_day.isin(["0", "1"]).all():
        raise ValueError("CALENDAR_SCOPE_OR_STATUS")
    expected = pd.date_range(start, end).strftime("%Y-%m-%d").tolist()
    if sorted(raw.calendar_date) != expected:
        raise ValueError("CALENDAR_NATURAL_DAY_COVERAGE_INCOMPLETE")
    return raw.sort_values("calendar_date").reset_index(drop=True)


def causal_grid(raw, calendar, code, *, prior_close=None, tolerance="0.000001"):
    days = calendar.loc[calendar.is_trading_day.eq("1"), "calendar_date"]
    if not set(raw.date) <= set(days):
        raise ValueError("BAR_NOT_IN_TRADING_CALENDAR")
    frame = pd.DataFrame({"trade_date": days}).merge(raw.rename(columns={"date": "trade_date"}),
        how="left", on="trade_date", validate="one_to_one")
    previous, rows = prior_close, []
    for source in frame.to_dict("records"):
        row = dict(source, security_id=code)
        missing = pd.isna(row.get("code"))
        suspended = not missing and row["tradestatus"] == "0"
        if missing:
            issue = "MISSING_BAR_AND_STATUS"
        elif previous is None:
            issue = "INITIAL_PREDECESSOR_UNVERIFIED"
        elif abs(Decimal(str(row["preclose"]))-Decimal(str(previous))) > Decimal(tolerance):
            issue = "CORPORATE_ACTION_EVIDENCE_REQUIRED"
        else:
            issue = "SUSPENDED" if suspended else ""
        row.update(issue=issue, trading_status="UNKNOWN" if missing else "SUSPENDED" if suspended else "TRADING",
            previous_known_raw_close=previous, preclose_minus_prior_close=row.get("preclose")-previous
                if previous is not None and not missing else None)
        rows.append(row)
        previous = None if missing else row["close"]
    return pd.DataFrame(rows)
