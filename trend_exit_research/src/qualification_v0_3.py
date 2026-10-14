"""Experimental qualification, separate from the frozen default TREND risk state."""
from __future__ import annotations

from collections import deque

import pandas as pd

from .indicators import config_hash


def qualify(daily, config):
    history = deque(maxlen=config["runup_window"] + 1)
    rows = []
    for source in daily.to_dict("records"):
        row = dict(source)
        close = row["indicator_close"]
        suspended = row["indicator_status"] == "SUSPENDED"
        valid = row["trading_status"] == "TRADING" and pd.notna(close)
        if not valid and not suspended:
            history.clear()
        if valid:
            history.append((close * row["factor"], row["trade_date"]))
        change = history[-1][0] / history[0][0] - 1 if valid and len(history) == history.maxlen else None
        required = [row[k] for k in ("ma28", "ma55", "ma28_slope", "ma55_slope", "rolling_high")]
        data_ready = valid and row["valid_observations"] >= config["min_observations"] and all(pd.notna(x) for x in required) and change is not None
        failed = []
        if data_ready:
            rules = {"MA28_DIRECTION": row["ma28_slope"] > config["min_ma28_slope"],
                "MA55_DIRECTION": row["ma55_slope"] > config["min_ma55_slope"],
                "MA28_ABOVE_MA55": row["ma28"] / row["ma55"] - 1 > config["min_ma28_over_ma55"],
                "RECENT_RUNUP": change >= config["min_runup"],
                "CLOSE_AT_OR_ABOVE_MA28": close >= row["ma28"],
                "NOT_OVEREXTENDED_MA55": close / row["ma55"] - 1 <= config["max_close_over_ma55"],
                "NEAR_KNOWN_PEAK": close / row["rolling_high"] - 1 >= -config["max_distance_below_peak"]}
            failed = [key for key, passed in rules.items() if not passed]
        state = "TREND_DATA_UNQUALIFIED" if not data_ready else "TREND_NOT_QUALIFIED" if failed else "TREND_ELIGIBLE"
        row.update(trend_qualification=state, trend_qualification_reason="INSUFFICIENT_OR_INVALID_HISTORY" if not data_ready else "|".join(failed) or "ALL_PREDECLARED_CONDITIONS",
            trend_qualification_version=config["version"], trend_config_sha256=config_hash(config), recent_runup=change,
            runup_reference_date=history[0][1] if change is not None else None,
            trend_data_grade="COMPUTATIONAL_CAUSALITY_ONLY;HISTORICAL_PUBLICATION_TIME_UNQUALIFIED")
        rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.astype({key: daily[key].dtype for key in daily.columns})
        result["recent_runup"] = result.recent_runup.astype(float)
        result["runup_reference_date"] = result.runup_reference_date.astype("string")
    return result
