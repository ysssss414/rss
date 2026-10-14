"""Frozen MA confirmation and risk-cap research, independent of Stage 1."""
from __future__ import annotations

from collections import deque
import json
import math
from pathlib import Path

import pandas as pd

from .indicators import config_hash


VERSION = "TREND_EXIT_BASELINE_V0_1"
STATES = ("TREND", "DEFENSIVE", "BOX_LOW_EXPOSURE", "EXIT_PENDING", "EXIT_REQUIRED", "REENTRY_CANDIDATE")


def load_risk_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {"version", "confirmation_days", "repair_confirm_days", "box_state_enabled", "box_window",
                "box_max_width", "box_near_peak", "box_min_runup", "position_caps"}
    if set(value) != expected or value["version"] != VERSION or value["confirmation_days"] != 3:
        raise ValueError("Frozen baseline requires TREND_EXIT_BASELINE_V0_1 and three-day confirmation")
    if type(value["box_state_enabled"]) is not bool:
        raise ValueError("box_state_enabled must be boolean")
    for key in ("box_window", "repair_confirm_days"):
        if type(value[key]) is not int or value[key] < 1:
            raise ValueError("Positive window required")
    for key in ("box_max_width", "box_near_peak", "box_min_runup"):
        if type(value[key]) not in (int, float) or not 0 < value[key] < 1:
            raise ValueError("Box thresholds must be fractions in (0,1)")
    caps = value["position_caps"]
    if set(caps) != set(STATES) or caps["EXIT_REQUIRED"] != 0 or caps["REENTRY_CANDIDATE"] != 0:
        raise ValueError("Explicit state caps required; exit and reentry default to zero")
    if any(cap is not None and (type(cap) not in (float, int) or not 0 <= cap <= 1) for cap in caps.values()):
        raise ValueError("Position caps must be null or in [0,1]")
    for safer, riskier in (("DEFENSIVE", "TREND"), ("BOX_LOW_EXPOSURE", "DEFENSIVE"), ("EXIT_PENDING", "DEFENSIVE")):
        if caps[safer] is not None and caps[riskier] is not None and caps[safer] > caps[riskier]:
            raise ValueError("More defensive states cannot increase the configured cap")
    return value


def combine_risk_caps(*, stock=None, market=None, sector=None, account=None, total_budget=None) -> dict:
    """Zero dominates. Missing risk budgets never imply an unrestricted portfolio."""
    components = dict(stock=stock, market=market, sector=sector, account=account, total_budget=total_budget)
    if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1)
           for value in components.values()):
        raise ValueError("Risk budgets must be null or fractions in [0,1]")
    known = [value for value in components.values() if value is not None]
    missing = [key for key, value in components.items() if value is None]
    cap = min(known) if known else None
    return {"upper_bound": cap, "complete": not missing or cap == 0, "missing_components": missing}


def apply_risk(daily: pd.DataFrame, config: dict, *, confirmation_days=3) -> tuple[pd.DataFrame, pd.DataFrame]:
    if confirmation_days not in (1, 2, 3):
        raise ValueError("Only predeclared 1/2/3-day scenarios are supported")
    counts = {28: 0, 55: 0}
    starts = {28: None, 55: None}
    exited = defensive = reentry = False
    repair = 0
    history = deque(maxlen=max(60, config["box_window"] + 1))
    rows, logs = [], []
    scenario = f"{VERSION}/CONFIRM_{confirmation_days}"
    rule_hash = config_hash(config)
    for source in daily.to_dict("records"):
        row = dict(source)
        day, close = row["trade_date"], row["indicator_close"]
        valid = row["trading_status"] == "TRADING" and pd.notna(close)
        suspended = row["indicator_status"] == "SUSPENDED"
        row.update(risk_rule_version=VERSION, risk_config_sha256=rule_hash, risk_scenario=scenario,
                   confirmation_days=confirmation_days, reduce_required=False, clear_required=False,
                   box_candidate=False, box_upper=None, box_lower=None, risk_state_reason="",
                   target_trend_position=None, add_allowed=False, risk_state_valid=False)
        for period in (28, 55):
            label = f"ma{period}"
            ma = row[label]
            ready = valid and pd.notna(ma)
            event = None
            if ready:
                if close < ma:  # Strict close<MA, zero buffer; each day's own T-anchored MA.
                    counts[period] += 1
                    if counts[period] == 1:
                        starts[period] = (day, close * row["factor"])
                        event = f"MA{period}_BREAK_PENDING"
                    if counts[period] == confirmation_days:
                        event = f"MA{period}_BREAK_CONFIRMED"
                        if period == 28:
                            defensive = row["reduce_required"] = True
                        else:
                            exited = row["clear_required"] = True
                            reentry = False
                else:
                    counts[period], starts[period] = 0, None
            elif not suspended:
                counts[period], starts[period] = 0, None
            status = (f"MA{period}_BREAK_CONFIRMED" if counts[period] >= confirmation_days else
                      f"MA{period}_BREAK_PENDING" if counts[period] else f"MA{period}_ABOVE_OR_EQUAL")
            row.update({f"{label}_break_count": counts[period], f"{label}_break_state": status,
                        f"{label}_input_status": "VALID" if ready else "SUSPENDED_PAUSED" if suspended else "UNAVAILABLE",
                        f"{label}_episode_start": starts[period][0] if starts[period] else None,
                        f"{label}_change_since_first_break": close * row["factor"] / starts[period][1] - 1
                        if ready and starts[period] else None})
            if event:
                logs.append({key: row[key] for key in ("security_id", "trade_date", "source_snapshot_id",
                    "snapshot_manifest_sha256", "risk_rule_version", "risk_config_sha256", "risk_scenario",
                    "confirmation_days", "decision_at")} | {"signal": event, "ma_period": period,
                    "close": close, "ma": ma, "count": counts[period], "episode_start": starts[period][0],
                    "reason": f"{counts[period]} valid closes strictly below each day's MA{period}; no backdating"})
        risk_ready = valid and pd.notna(row["ma28"]) and pd.notna(row["ma55"])
        recovered = (risk_ready and close >= max(row["ma28"], row["ma55"])
                     and pd.notna(row["ma28_slope"]) and pd.notna(row["ma55_slope"])
                     and row["ma28_slope"] >= 0 and row["ma55_slope"] >= 0)
        repair = repair + 1 if recovered else repair if suspended else 0
        if repair >= config["repair_confirm_days"]:
            defensive = False
            if exited:
                reentry = True  # Evaluation only. Never automatically promotes to TREND or buys.
        if valid:
            history.append((row["raw_high"] * row["factor"], row["raw_low"] * row["factor"]))
        elif not suspended:
            history.clear()
        window = config["box_window"]
        if risk_ready and len(history) > window and pd.notna(row["rolling_high"]):
            prior = list(history)[-window-1:-1]
            upper, lower = max(x[0] for x in prior) / row["factor"], min(x[1] for x in prior) / row["factor"]
            runup_low = min(x[1] for x in history) / row["factor"]
            row.update(box_upper=upper, box_lower=lower,
                       box_candidate=(upper / lower - 1 <= config["box_max_width"]
                         and close >= row["rolling_high"] * (1 - config["box_near_peak"])
                         and close / runup_low - 1 >= config["box_min_runup"]))
        if exited:
            state = "REENTRY_CANDIDATE" if reentry and recovered else "EXIT_REQUIRED"
        elif counts[55] > 0:
            state = "EXIT_PENDING"
        elif defensive:
            state = "DEFENSIVE"
        elif config["box_state_enabled"] and row["box_candidate"]:
            state = "BOX_LOW_EXPOSURE"
        else:
            state = "TREND"
        cap = config["position_caps"][state]
        if state == "EXIT_PENDING" and defensive:
            known = [x for x in (cap, config["position_caps"]["DEFENSIVE"]) if x is not None]
            cap = min(known) if known else None
        if not risk_ready:
            cap = 0
        row.update(POSITION_RISK_STATE=state, position_cap=cap,
                   position_cap_status="UNSPECIFIED" if cap is None else "EXPLICIT_BOUND",
                   cap_parameter=f"position_caps.{state}", risk_state_valid=risk_ready,
                   add_allowed=risk_ready and state in {"TREND", "BOX_LOW_EXPOSURE"},
                   risk_state_reason="DATA_UNAVAILABLE_OR_WARMUP" if not risk_ready else state,
                   repair_count=repair, target_trend_position=0 if state in {"EXIT_REQUIRED", "REENTRY_CANDIDATE"} else None,
                   preferred_action="CLEAR" if row["clear_required"] else "REDUCE_CAP" if row["reduce_required"] else "NONE")
        row["combined_risk_upper_bound"] = combine_risk_caps(stock=cap)["upper_bound"]
        row["portfolio_budget_status"] = "ZERO_BOUND" if cap == 0 else "INCOMPLETE_MARKET_SECTOR_ACCOUNT_TOTAL_BUDGET"
        rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.astype({key: daily[key].dtype for key in daily.columns})
        numeric = ["position_cap", "combined_risk_upper_bound", "target_trend_position", "box_upper", "box_lower",
                   "ma28_change_since_first_break", "ma55_change_since_first_break"]
        result[numeric] = result[numeric].astype(float)
        for key in ("ma28_episode_start", "ma55_episode_start"):
            result[key] = result[key].astype("string")
    return result, pd.DataFrame(logs, columns=["security_id", "trade_date", "source_snapshot_id", "snapshot_manifest_sha256",
        "risk_rule_version", "risk_config_sha256", "risk_scenario", "confirmation_days", "decision_at", "signal",
        "ma_period", "close", "ma", "count", "episode_start", "reason"])
