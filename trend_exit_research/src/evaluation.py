"""Future-only annotations. Never imported by the signal or risk-state engines."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json

import pandas as pd

from research.foundation import RunContext, TradingCalendar, execution_policy


def _path_status(path: list[dict], start: dict) -> str:
    if any(item["trading_status"] != "TRADING" or item["issue"] for item in path):
        return "MISSING_INVALID_OR_NON_TRADING_PATH"
    if any(item["factor"] != start["factor"] or item.get("is_xr_sec") is True or item.get("is_wd_sec") is True for item in path):
        return "UNQUALIFIED_ACTION_PATH"
    return "AVAILABLE"


def _execution(source: list[dict], position: int, row: dict) -> dict:
    result = {"next_exchange_session": source[position+1]["trade_date"] if position+1 < len(source) else None,
              "next_observed_trading_date": None, "skipped_suspended_sessions": 0,
              "order_model_at": None, "order_status": "NOT_REQUESTED", "execution_status": "NOT_REQUESTED",
              "actual_execution_status": "NOT_OBSERVED_NO_ORDER_SENT", "execution_reason": None,
              "execution_model_price": None, "vendor_open_diagnostic": None, "drawdown_to_execution": None}
    if not (row["reduce_required"] or row["clear_required"]):
        return result
    target = position + 1
    while target < len(source) and source[target]["trading_status"] == "SUSPENDED":
        result["skipped_suspended_sessions"] += 1
        target += 1
    if target >= len(source):
        result.update(execution_status="EXECUTION_UNRESOLVED", order_status="NO_SESSION_AVAILABLE", execution_reason="NO_FOLLOWING_TRADING_SESSION")
        return result
    current = source[target]
    day = current["trade_date"]
    result.update(next_observed_trading_date=day if current["trading_status"] == "TRADING" else None,
                  order_model_at=day + "T09:15:00+08:00", order_status="RESEARCH_ORDER_INTENT_ONLY")
    if current["trading_status"] != "TRADING" or current["issue"]:
        result.update(execution_status="EXECUTION_UNRESOLVED", execution_reason="MISSING_OR_INVALID_NEXT_TRADING_DATA")
        return result
    if any(item["factor"] != source[position]["factor"] or item.get("is_xr_sec") is True or item.get("is_wd_sec") is True
           for item in source[position+1:target+1]):
        result.update(execution_status="EXECUTION_UNRESOLVED", execution_reason="UNQUALIFIED_ACTION_BEFORE_EXECUTION")
        return result
    try:
        opening, lower = Decimal(str(current["raw_open"])), Decimal(str(current.get("low_limited")))
        if opening.is_finite() and lower.is_finite():
            result["vendor_open_diagnostic"] = "AT_OR_BELOW_VENDOR_LIMIT" if opening <= lower else "ABOVE_VENDOR_LOWER_LIMIT"
    except (InvalidOperation, TypeError, ValueError):
        pass
    # Vendor EOD limits are not converted into qualified morning constraints.
    constraint = current.get("daily_constraint")
    if not isinstance(constraint, dict):
        result.update(execution_status="EXECUTION_UNRESOLVED", execution_reason="MORNING_DAILY_CONSTRAINT_UNQUALIFIED")
        return result
    signal_day = date.fromisoformat(row["trade_date"])
    calendar = TradingCalendar((signal_day, date.fromisoformat(day)), "RESEARCH_NEXT_OBSERVED_TRADING_SESSION_V0_1")
    policy = execution_policy("NEXT_SESSION_SELL_V1")
    model = policy.execute(context=RunContext(row["source_snapshot_id"], "gate-b-retry3-daily-contract/1", "1",
        policy.policy_id, "1", calendar.version), calendar=calendar, security_id=row["security_id"],
        signal_date=signal_day, signal_at=datetime.fromisoformat(row["decision_at"]),
        latest_input_at=datetime.fromisoformat(row["decision_at"]), side="SELL",
        next_day={"security_id": row["security_id"], "trade_date": day, "trading_status": current["trading_status"],
                  "raw_open": str(current["raw_open"]), "daily_constraint": constraint})
    result.update(execution_status=model.status, execution_reason=model.reason,
                  execution_model_price=float(model.execution_price) if model.execution_price is not None else None)
    if model.status == "MODELLED_FILL" and pd.notna(row["rolling_high"]):
        result["drawdown_to_execution"] = float(model.execution_price) / row["rolling_high"] - 1
    return result


def evaluate(daily: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Append observable execution and 5/10/20-session outcomes to a separate table."""
    source = frame.to_dict("records")
    positions = {item["trade_date"]: i for i, item in enumerate(source)}
    known_ma = {item["trade_date"]: item for item in daily.to_dict("records")}
    rows = []
    for row in daily.to_dict("records"):
        i = positions[row["trade_date"]]
        output = {key: row[key] for key in ("security_id", "trade_date", "source_snapshot_id", "snapshot_manifest_sha256",
                  "risk_rule_version", "risk_config_sha256", "risk_scenario", "confirmation_days", "POSITION_RISK_STATE",
                  "reduce_required", "clear_required")}
        output.update(outcome_only=True, outcome_data_as_of=source[-1]["trade_date"],
                      drawdown_to_confirmation=row["drawdown"] if row["reduce_required"] or row["clear_required"] else None,
                      **_execution(source, i, row))
        for horizon in (5, 10, 20):
            path = source[i+1:i+horizon+1]
            status = "RIGHT_CENSORED" if len(path) != horizon else _path_status(path, source[i])
            if pd.isna(row["indicator_close"]):
                status = "INVALID_SIGNAL_DAY"
            output[f"forward_{horizon}_status"] = status
            output[f"forward_{horizon}_date"] = path[-1]["trade_date"] if len(path) == horizon else None
            output[f"forward_return_{horizon}"] = path[-1]["raw_close"] / row["raw_close"] - 1 if status == "AVAILABLE" else None
        period = 55 if row["clear_required"] else 28 if row["reduce_required"] else None
        path = source[i+1:i+6]
        output.update(recross_current_ma_within5=None, recross_ma_period=period, recross5_status="NOT_CONFIRMED_SIGNAL")
        if period:
            status = "RIGHT_CENSORED" if len(path) < 5 else _path_status(path, source[i])
            if status == "AVAILABLE" and any(item["trade_date"] not in known_ma for item in path):
                status = "MA_EVALUATION_RANGE_CENSORED"
            output["recross5_status"] = status
            if status == "AVAILABLE":
                output["recross_current_ma_within5"] = any(item["raw_close"] >= known_ma[item["trade_date"]][f"ma{period}"] for item in path)
        rows.append(output)
    result = pd.DataFrame(rows)
    if not result.empty:
        numeric = ["drawdown_to_confirmation", "execution_model_price", "drawdown_to_execution", "forward_return_5", "forward_return_10", "forward_return_20"]
        result[numeric] = result[numeric].astype(float)
    return result


def comparison_rows(daily: pd.DataFrame, frame: pd.DataFrame, config: dict, *, start=None, end=None) -> list[dict]:
    from .risk import apply_risk
    rows = []
    for days in (1, 2, 3):
        risk, _ = apply_risk(daily, config, confirmation_days=days)
        outcomes = evaluate(risk, frame)
        if start and end:
            outcomes = outcomes.loc[outcomes.trade_date.between(start, end)]
            risk = risk.loc[risk.trade_date.between(start, end)]
        confirmed = outcomes.loc[outcomes.reduce_required | outcomes.clear_required]
        evaluable = confirmed.loc[confirmed.recross5_status.eq("AVAILABLE")]
        first_break_change = pd.concat([risk.loc[risk.reduce_required, "ma28_change_since_first_break"],
                                       risk.loc[risk.clear_required, "ma55_change_since_first_break"]])
        rows.append({"security_id": frame.security_id.iloc[0], "risk_rule_version": config["version"],
            "confirmation_days": days, "ma28_confirmations": int(risk.reduce_required.sum()),
            "ma55_confirmations": int(risk.clear_required.sum()), "recross5_evaluable": len(evaluable),
            "recross5_count": int(evaluable.recross_current_ma_within5.eq(True).sum()),
            "mean_peak_drawdown_at_confirmation": confirmed.drawdown_to_confirmation.mean(),
            "mean_change_since_first_break": first_break_change.mean(),
            "modelled_execution_count": int(confirmed.execution_status.eq("MODELLED_FILL").sum()),
            "mean_peak_drawdown_at_modelled_execution": confirmed.drawdown_to_execution.mean(),
            "interpretation": "DESCRIPTIVE_PAIRED_CASES_NO_OPTIMAL_PARAMETER_SELECTION"})
    return rows
