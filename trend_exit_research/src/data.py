"""Read the existing frozen RSS snapshot; no acquisition or cache writes."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from research.real_materialization import bar_validation_status
from research.snapshot import FrozenResearchSnapshot


DEFAULT_SNAPSHOT = Path("data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1")


def stock_frame(snapshot: FrozenResearchSnapshot, sample: dict, master: pd.DataFrame) -> pd.DataFrame:
    code = sample["resolved_code"]
    identity = master.loc[master.security_id.eq(code)].iloc[0]
    calendar = snapshot.load_calendar()
    calendar = calendar.loc[calendar.is_trading_day.eq(True), ["trade_date"]].copy()
    calendar["trade_date"] = pd.to_datetime(calendar.trade_date).dt.strftime("%Y-%m-%d")
    calendar = calendar.loc[calendar.trade_date.ge(str(identity.listing_date)[:10])]
    frames = [snapshot.load_daily_bars([code]), snapshot.load_daily_status([code]),
              snapshot.load_adjustment_factor([code])]
    for label, frame in zip(("bars", "status", "factor"), frames):
        frame["trade_date"] = pd.to_datetime(frame.trade_date).dt.strftime("%Y-%m-%d")
        if frame.duplicated(["security_id", "trade_date"]).any():
            raise ValueError(f"Duplicate {label} security/date: {code}")
        if not frame.security_id.eq(code).all() or not set(frame.trade_date) <= set(calendar.trade_date):
            raise ValueError(f"Unexpected {label} security/date: {code}")
        frame[f"has_{label}"] = True
    result = calendar.assign(security_id=code)
    for frame in frames:
        result = result.merge(frame, on=["security_id", "trade_date"], how="left", validate="one_to_one")
    delisted = None if pd.isna(identity.delisting_date) else str(identity.delisting_date)[:10]
    issues = []
    statuses = []
    for row in result.to_dict("records"):
        flags = (row.get("is_susp_sec"), row.get("is_wd_sec"), row.get("is_xr_sec"))
        if row.get("has_status") is not True or any(pd.isna(flag) for flag in flags):
            state, issue = "UNKNOWN", "MISSING_OR_UNKNOWN_STATUS"
        elif delisted and row["trade_date"] >= delisted:
            state, issue = "DELISTED", "DELISTED"
        elif row["is_susp_sec"]:
            state, issue = "SUSPENDED", "SUSPENDED"
        elif row.get("has_bars") is not True:
            state, issue = "TRADING", "MISSING_BAR"
        elif bar_validation_status(row) != "BAR_VALID":
            state, issue = "TRADING", "INVALID_BAR"
        else:
            state, issue = "TRADING", ""
        if not issue and (not math.isfinite(float(row.get("factor", float("nan")))) or row["factor"] <= 0):
            issue = "MISSING_OR_INVALID_FACTOR"
        statuses.append(state)
        issues.append(issue)
    result["trading_status"], result["issue"] = statuses, issues
    result = result.rename(columns={key: f"raw_{key}" for key in ("open", "high", "low", "close")})
    result["turnover_rate"] = result.get("turnover_rate", float("nan"))
    result["source_snapshot_id"] = snapshot.snapshot_id
    result["snapshot_manifest_sha256"] = snapshot.validation["manifest_sha256"]
    result["factor_source_hash"] = snapshot.manifest["datasets"]["adjustment_factor"]["fingerprint"]
    result["availability_basis"] = "ASSUMED_15_30_SHANGHAI_NOT_HISTORICAL_PUBLICATION_PROOF"
    result["raw_available_at"] = result.trade_date + "T15:30:00+08:00"
    result["factor_available_at"] = result.raw_available_at
    return result.sort_values("trade_date").reset_index(drop=True)


def coverage_row(sample: dict, frame: pd.DataFrame, snapshot: FrozenResearchSnapshot) -> dict:
    row = dict(sample)
    first, last = snapshot.manifest["calendar"]["start"], snapshot.manifest["calendar"]["end"]
    start = sample["analysis_start"] or first
    end = sample["analysis_end"] or (sample.get("observation_start") if sample.get("sample_type") == "FORWARD_OBSERVATION" else last)
    row.update(effective_start=start, effective_end=end, interval_basis="MANUAL" if
               sample["analysis_start"] or sample["analysis_end"] else "SNAPSHOT_RANGE_FOR_COVERAGE_AND_SMOKE_ONLY",
               source_snapshot_id=snapshot.snapshot_id, snapshot_manifest_sha256=snapshot.validation["manifest_sha256"],
               range_status="PARTIAL_OUTSIDE_SNAPSHOT" if start < first or end > last else "WITHIN_SNAPSHOT")
    selected = frame.loc[frame.trade_date.between(start, end)] if not frame.empty else frame
    issues = selected.issue.value_counts().to_dict() if not selected.empty else {}
    expected = len(selected)
    valid = int(selected.issue.eq("").sum()) if expected else 0
    suspended = int(selected.issue.eq("SUSPENDED").sum()) if expected else 0
    row.update(expected_sessions=expected, usable_trading_bars=valid, suspended_sessions=suspended,
               data_coverage=(valid + suspended) / expected if expected else 0,
               first_available=frame.loc[frame.has_bars.eq(True), "trade_date"].min() if not frame.empty else "",
               last_available=frame.loc[frame.has_bars.eq(True), "trade_date"].max() if not frame.empty else "",
               raw_bar_sessions=int(selected.has_bars.eq(True).sum()) if expected else 0,
               status_sessions=int(selected.has_status.eq(True).sum()) if expected else 0,
               factor_sessions=int(selected.has_factor.eq(True).sum()) if expected else 0,
               missing_dates="|".join(selected.loc[~selected.issue.isin(["", "SUSPENDED"]), "trade_date"]) if expected else "",
               missing_reasons="|".join(f"{key}:{value}" for key, value in sorted(issues.items()) if key and key != "SUSPENDED"),
               action_marked_sessions=int((selected.is_xr_sec.eq(True) | selected.is_wd_sec.eq(True)).sum()) if expected else 0,
               factor_policy="EVENT_FREE_SEGMENT_ONLY_RESET_ON_ACTION_OR_FACTOR_CHANGE",
               turnover_coverage=int(selected.turnover_rate.notna().sum()) / expected if expected else 0)
    if sample["resolution_status"] != "RESOLVED":
        row["missing_reasons"] = sample["resolution_status"]
    elif not expected:
        row["missing_reasons"] = "NO_SESSIONS_IN_SNAPSHOT_INTERVAL"
    return row
