"""Causal T-close features and exploratory A/B/C/D flags, never orders."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path

import pandas as pd

from research.data.adjustment import adjusted_bars
from research.factor_pit import FactorRatioSnapshot
from research.indicator_prices import PRICE_BASIS, SHANGHAI
from research.real_materialization import bar_validation_status


METRICS = ["indicator_close", "ma13", "ma28", "ma55", "ma13_slope", "ma28_slope", "ma55_slope",
           "ma28_ma55_distance", "ma_distance_change", "close_vs_ma28", "close_vs_ma55",
           "rolling_high", "drawdown", "support_prior", "swing_high", "swing_low",
           "rebound_high", "rebound_low", "amount_mean5", "amount_mean10", "amount_mean20",
           "amount_sum5", "amount_sum10", "amount_sum20", "amount_change5", "amount_change10",
           "amount_change20", "amount_ratio5_20"]
SIGNAL_COLUMNS = ["security_id", "trade_date", "source_snapshot_id", "snapshot_manifest_sha256",
                  "parameter_version", "config_sha256", "factor_source_hash", "factor_policy",
                  "availability_basis", "decision_at", "latest_input_date", "signal", "reason"]


def config_hash(config: dict) -> str:
    return sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    keys = {"version", "break_buffer", "confirm_days", "slope_days", "peak_window", "support_window",
            "swing_reversal", "rebound_min", "repair_grace_days"}
    if set(config) != keys or not isinstance(config["version"], str) or not config["version"]:
        raise ValueError("Unsupported candidate config schema")
    for key in ("confirm_days", "slope_days", "peak_window", "support_window", "repair_grace_days"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"Positive integer required: {key}")
    if config["peak_window"] < 55:
        raise ValueError("peak_window must cover MA55")
    for key in ("break_buffer", "swing_reversal", "rebound_min"):
        if type(config[key]) not in (float, int) or not 0 < config[key] < 1:
            raise ValueError(f"Fraction in (0,1) required: {key}")
    return config


def _available(row: dict, day: str) -> bool:
    decision = datetime.combine(date.fromisoformat(day), time(15, 30), SHANGHAI)
    try:
        raw = datetime.fromisoformat(row["raw_available_at"])
        factor = datetime.fromisoformat(row["factor_available_at"])
        return (raw.utcoffset() is not None and factor.utcoffset() is not None
                and raw.astimezone(SHANGHAI).date().isoformat() == day
                and raw.astimezone(SHANGHAI).time() >= time(15)
                and max(raw, factor) <= decision)
    except (TypeError, ValueError, KeyError):
        return False


def calculate(frame: pd.DataFrame, config: dict, *, actions=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """actions=None: restart at unqualified events; supplied complete events: verify ratios.

    Historical windows are re-anchored at EACH T, including slope comparisons.
    Suspended sessions emit no prices/signals and do not count as observations.
    A missing/invalid session resets history: no indicators bridge an unknown gap.
    """
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()
    if frame.security_id.nunique() != 1 or frame.trade_date.tolist() != sorted(set(frame.trade_date)):
        raise ValueError("Require one security and unique ascending calendar dates")
    history, outputs, signals = [], [], []
    counts = {28: 0, 55: 0}
    episode = None
    swing = {"direction": 0, "extreme": None, "date": None, "high": None, "low": None,
             "high_date": None, "low_date": None, "high_known_at": None, "low_known_at": None}
    previous_factor = None
    previous_structure_break = False
    limit = max(55 + config["slope_days"], config["peak_window"], config["support_window"] + 1, 40)
    identity = config_hash(config)
    for source in frame.to_dict("records"):
        day = source["trade_date"]
        factor = float(source["factor"]) if pd.notna(source["factor"]) else float("nan")
        reason = source["issue"]
        if not reason and bar_validation_status({key: source.get(f"raw_{key}") for key in
                ("open", "high", "low", "close")} | {key: source.get(key) for key in ("volume", "amount")}) != "BAR_VALID":
            reason = "INVALID_BAR"
        if not reason and (not math.isfinite(factor) or factor <= 0):
            reason = "MISSING_OR_INVALID_FACTOR"
        event = (source.get("is_xr_sec") is True or source.get("is_wd_sec") is True
                 or previous_factor is not None and math.isfinite(factor) and factor != previous_factor)
        if actions is None and event:
            reason = "UNQUALIFIED_ACTION_RESET"
        elif not reason and not _available(source, day):
            reason = "INPUT_UNAVAILABLE_AT_T_CLOSE"
        output = {key: source[key] for key in ("security_id", "trade_date", "raw_open", "raw_high", "raw_low",
                   "raw_close", "volume", "amount", "turnover_rate", "trading_status", "factor",
                   "source_snapshot_id", "snapshot_manifest_sha256", "factor_source_hash", "availability_basis",
                   "raw_available_at", "factor_available_at")}
        output.update({key: None for key in METRICS})
        output.update(parameter_version=config["version"], config_sha256=identity, price_basis=PRICE_BASIS,
                      adjustment_anchor=day, latest_input_date=day,
                      decision_at=day + "T15:30:00+08:00", indicator_status=reason or "WARMUP",
                      factor_policy="VERIFIED_EVENT_RATIOS" if actions is not None else "EVENT_FREE_SEGMENT_ONLY",
                      valid_observations=0, signal_eligible=False, A=False, B=False, C=False, D=False,
                      swing_high_date=None, swing_low_date=None, swing_high_known_at=None, swing_low_known_at=None,
                      rebound_start=None, trigger_reasons="")
        if math.isfinite(factor) and factor > 0:
            previous_factor = factor
        if reason:
            counts = {28: 0, 55: 0}
            if reason != "SUSPENDED":
                history, episode = [], None
                swing = dict.fromkeys(swing)
                swing["direction"] = 0
                previous_structure_break = False
            outputs.append(output)
            continue
        history.append(source)
        history = history[-limit:]
        raw = pd.DataFrame(history).rename(columns={"security_id": "ts_code"})
        factors = raw[["ts_code", "trade_date", "factor", "factor_available_at"]].rename(
            columns={"factor_available_at": "available_at"})
        try:
            if actions is not None:
                cache = FactorRatioSnapshot(source["security_id"], source["source_snapshot_id"],
                    source["factor_source_hash"], {date.fromisoformat(x["trade_date"]): Decimal(str(x["factor"]))
                    for x in history}, tuple(actions))
                for item in history:
                    cache.ratio(date.fromisoformat(item["trade_date"]), date.fromisoformat(day))
            prices = adjusted_bars(raw, factors, mode="qfq", anchor=date.fromisoformat(day),
                                   as_of=date.fromisoformat(day), factor_schema="daily")
        except ValueError as exc:
            output["indicator_status"] = "UNQUALIFIED_FACTOR_RATIO"
            output["trigger_reasons"] = str(exc)
            history, episode, counts = [], None, {28: 0, 55: 0}
            swing = dict.fromkeys(swing)
            swing["direction"] = 0
            previous_structure_break = False
            outputs.append(output)
            continue
        closes, highs, lows = prices.close.tolist(), prices.high.tolist(), prices.low.tolist()
        n, close = len(closes), closes[-1]
        output.update(valid_observations=n, indicator_close=close, indicator_status="READY" if n >= limit else "WARMUP",
                      signal_eligible=n >= limit)
        slope = config["slope_days"]
        for period in (13, 28, 55):
            if n >= period:
                ma = sum(closes[-period:]) / period
                output[f"ma{period}"] = ma
                if n >= period + slope:
                    previous_ma = sum(closes[-period-slope:-slope]) / period
                    output[f"ma{period}_slope"] = ma / previous_ma - 1
        if output["ma55"] is not None:
            output["ma28_ma55_distance"] = output["ma28"] / output["ma55"] - 1
            output["close_vs_ma28"] = close / output["ma28"] - 1
            output["close_vs_ma55"] = close / output["ma55"] - 1
            if n >= 55 + slope:
                prior_distance = (sum(closes[-28-slope:-slope]) / 28) / (sum(closes[-55-slope:-slope]) / 55) - 1
                output["ma_distance_change"] = output["ma28_ma55_distance"] - prior_distance
        if n >= config["peak_window"]:
            output["rolling_high"] = max(highs[-config["peak_window"]:])
            output["drawdown"] = close / output["rolling_high"] - 1
        if n > config["support_window"]:
            output["support_prior"] = min(lows[-config["support_window"]-1:-1])
        amounts = raw.amount.tolist()
        for period in (5, 10, 20):
            if n >= period:
                total = sum(amounts[-period:])
                output[f"amount_sum{period}"] = total
                output[f"amount_mean{period}"] = total / period
                if n >= period * 2:
                    prior = sum(amounts[-2*period:-period])
                    output[f"amount_change{period}"] = total / prior - 1 if prior > 0 else None
        if output["amount_mean20"]:
            output["amount_ratio5_20"] = output["amount_mean5"] / output["amount_mean20"]
        # Store swing/rebound extrema in close*factor units; translate to the T anchor.
        value = close * factor
        if swing["extreme"] is None:
            swing.update(extreme=value, date=day)
        reversal = config["swing_reversal"]
        direction = swing["direction"]
        if direction >= 0 and value >= swing["extreme"] or direction < 0 and value <= swing["extreme"]:
            swing.update(extreme=value, date=day)
        elif direction >= 0 and value <= swing["extreme"] * (1 - reversal):
            swing.update(high=swing["extreme"], high_date=swing["date"], high_known_at=day,
                         direction=-1, extreme=value, date=day)
        elif direction < 0 and value >= swing["extreme"] * (1 + reversal):
            swing.update(low=swing["extreme"], low_date=swing["date"], low_known_at=day,
                         direction=1, extreme=value, date=day)
        for kind in ("high", "low"):
            output[f"swing_{kind}"] = swing[kind] / factor if swing[kind] is not None else None
            output[f"swing_{kind}_date"] = swing[f"{kind}_date"]
            output[f"swing_{kind}_known_at"] = swing[f"{kind}_known_at"]
        reasons = []
        if output["signal_eligible"]:
            for period, label in ((28, "A"), (55, "C")):
                below = close < output[f"ma{period}"] * (1 - config["break_buffer"])
                counts[period] = counts[period] + 1 if below else 0
                if counts[period] == config["confirm_days"]:
                    output[label] = True
                    reasons.append({"signal": label, "rule": "FIRST_CONFIRMED_MA_BREAK_IN_EPISODE",
                                    "ma_period": period, "close": close, "ma": output[f"ma{period}"],
                                    "below_count": counts[period], "buffer": config["break_buffer"]})
            if output["A"]:
                episode = {"start": day, "age": 0, "low": value, "high": value, "rebounded": False, "fired": False}
            if episode:
                episode["age"] += 1
                if value < episode["low"]:
                    episode.update(low=value, high=value)
                episode["high"] = max(episode["high"], value)
                episode["rebounded"] |= value / episode["low"] - 1 >= config["rebound_min"]
                output.update(rebound_start=episode["start"], rebound_low=episode["low"] / factor,
                              rebound_high=episode["high"] / factor)
                if close >= max(output["ma13"], output["ma28"]):
                    episode = None
                elif (not episode["fired"] and episode["age"] >= config["repair_grace_days"]
                      and episode["rebounded"] and close < min(output["ma13"], output["ma28"])
                      and output["ma13_slope"] <= 0 and close < closes[-2]):
                    output["B"], episode["fired"] = True, True
                    reasons.append({"signal": "B", "rule": "REBOUND_THEN_DECLINE_BELOW_MA13_AND_MA28",
                                    "break_date": episode["start"], "age": episode["age"],
                                    "rebound_min": config["rebound_min"], "rebound_high": output["rebound_high"],
                                    "rebound_low": output["rebound_low"], "close": close,
                                    "ma13": output["ma13"], "ma28": output["ma28"], "ma13_slope": output["ma13_slope"]})
            structure_break = close < output["support_prior"] * (1 - config["break_buffer"])
            if structure_break and not previous_structure_break and close >= output["ma55"]:
                output["D"] = True
                reasons.append({"signal": "D", "rule": "PRIOR_ROLLING_LOW_BREAK_WHILE_AT_OR_ABOVE_MA55",
                                "close": close, "prior_support": output["support_prior"],
                                "ma55": output["ma55"], "buffer": config["break_buffer"]})
            previous_structure_break = structure_break
        output["trigger_reasons"] = json.dumps(reasons, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        outputs.append(output)
        for item in reasons:
            signals.append({key: output[key] for key in ("security_id", "trade_date", "source_snapshot_id",
                "snapshot_manifest_sha256", "parameter_version", "config_sha256", "factor_source_hash",
                "factor_policy", "availability_basis", "decision_at", "latest_input_date")} |
                {"signal": item["signal"], "reason": json.dumps(item, sort_keys=True, separators=(",", ":"))})
    daily = pd.DataFrame(outputs)
    # Schema must not depend on whether a future row eventually populates a feature.
    daily[METRICS] = daily[METRICS].astype(float)
    for key in ("swing_high_date", "swing_low_date", "swing_high_known_at", "swing_low_known_at", "rebound_start"):
        daily[key] = daily[key].astype("string")
    return daily, pd.DataFrame(signals, columns=SIGNAL_COLUMNS).astype("string")
