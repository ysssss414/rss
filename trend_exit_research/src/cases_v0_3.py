"""Explicit target windows; never replace an uncovered case with another rally."""
from __future__ import annotations

import csv
from datetime import date
import re

import pandas as pd


def read_cases(path, samples):
    registry = {x["stock_name"]: x for x in samples}
    seen, result = set(), []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            if None in source:
                raise ValueError("Malformed target case row")
            row = {k: (v or "").strip() for k, v in source.items()}
            identity = row["case_id"]
            if not re.fullmatch(r"[A-Z0-9_]+", identity) or identity in seen:
                raise ValueError("Unique explicit case_id required")
            seen.add(identity)
            registered = registry.get(row["stock_name"])
            if registered is None:
                raise ValueError("Case stock must belong to the original manual registry")
            row["resolution_status"] = registered["resolution_status"]
            row["resolved_code"] = registered["resolved_code"]
            if row["stock_code"] != row["resolved_code"] and row["resolution_status"] == "RESOLVED":
                row["resolution_status"] = "CODE_NAME_MISMATCH"
            for purpose in ("study", "warmup", "evaluation"):
                start, end = row[f"{purpose}_start"], row[f"{purpose}_end"]
                if bool(start) != bool(end):
                    raise ValueError("Both interval endpoints required")
                if start and (date.fromisoformat(start).isoformat() != start or
                        date.fromisoformat(end).isoformat() != end or start > end):
                    raise ValueError("Invalid target interval")
            if row["study_start"]:
                if not row["warmup_start"] or not row["evaluation_start"]:
                    raise ValueError("Explicit warmup/study/evaluation purposes required")
                if not row["warmup_end"] < row["study_start"] <= row["study_end"] < row["evaluation_start"]:
                    raise ValueError("Warmup/study/evaluation intervals must not overlap")
            if row["compute"] not in ("true", "false"):
                raise ValueError("Explicit compute flag required")
            row["compute"] = row["compute"] == "true"
            result.append(row)
    if set(x["stock_name"] for x in result) != set(registry):
        raise ValueError("Target registry must preserve the original stock set")
    return result


def audit_case(case, frame, snapshot):
    row = dict(case)
    calendar = snapshot.manifest["calendar"]
    row.update(source_snapshot_id=snapshot.snapshot_id, snapshot_manifest_sha256=snapshot.validation["manifest_sha256"],
        actual_first_date=frame.loc[frame.has_bars.eq(True), "trade_date"].min() if not frame.empty else None,
        actual_last_date=frame.loc[frame.has_bars.eq(True), "trade_date"].max() if not frame.empty else None,
        target_status="TARGET_WINDOW_NOT_REGISTERED", computed=False, outside_calendar_denominator="UNKNOWN",
        factor_policy="EVENT_FREE_SEGMENT_ONLY", historical_publication_time_qualified=False)
    for purpose in ("warmup", "study", "evaluation"):
        start, end = case[f"{purpose}_start"], case[f"{purpose}_end"]
        covered = bool(start and calendar["start"] <= start <= end <= calendar["end"])
        selected = frame.loc[frame.trade_date.between(start, end)] if start and not frame.empty else pd.DataFrame()
        row.update({f"{purpose}_window_covered": covered, f"{purpose}_known_calendar_sessions": len(selected),
            f"{purpose}_usable_sessions": int(selected.issue.eq("").sum()) if not selected.empty else 0,
            f"{purpose}_missing_dates": "|".join(selected.loc[~selected.issue.isin(["", "SUSPENDED"]), "trade_date"])
                if not selected.empty else ""})
    if case["resolution_status"] != "RESOLVED":
        row["target_status"] = case["resolution_status"]
    elif case["study_start"]:
        row["target_status"] = "TARGET_CASE_WINDOW_COVERED" if row["study_window_covered"] else "TARGET_CASE_WINDOW_UNCOVERED"
    return row
