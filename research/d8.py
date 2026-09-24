"""D8 outcome-only corporate-action normalization and qualification.

D8 is deliberately unavailable to signal construction.  It maps realized
post-entry prices back to the entry-date price scale and excludes cash income.
Vendor rows whose implementation or revision history is not safe are retained
in acquisition cache/audit output but are not admitted as canonical actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from .factor_pit import backward_ratio_bounds


D8_CONTRACT = "ENTRY_COMPARABLE_FORWARD_PATH_V1"
D8_SCHEMA_VERSION = 1
D8_CANONICALIZER_VERSION = 1
D8_DATASET = "outcome_adjustment"
D8_EXCLUSION_DATASET = "outcome_adjustment_exclusions"

CANONICAL_COLUMNS = (
    "security_id", "effective_date", "single_factor", "event_kind",
    "known_date", "source_record_count", "source_event_hash",
)
EXCLUSION_COLUMNS = (
    "security_id", "effective_date", "single_factor", "event_kind", "reason",
)


@dataclass(frozen=True)
class D8Canonicalization:
    canonical: pd.DataFrame
    exclusions: pd.DataFrame
    quality: dict[str, object]


def _date_value(value: object) -> date | None:
    if value is None or pd.isna(value) or str(value).strip() in {"", "0", "None"}:
        return None
    text = str(value).strip().split(".")[0]
    try:
        return (date.fromisoformat(text) if "-" in text
                else date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}"))
    except (ValueError, TypeError):
        return None


def _stable_hash(rows: Sequence[dict[str, object]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str,
                         separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def normalize_single_event_factors(
    frame: pd.DataFrame,
    codes: Sequence[str],
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Convert the SDK trading-date x security matrix to sparse D8 events."""
    if not isinstance(frame, pd.DataFrame) or list(frame.columns) != list(codes):
        raise ValueError("D8 factor response identities or order differ from request")
    dates = pd.to_datetime(frame.index, errors="coerce")
    if dates.isna().any() or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError("D8 factor dates must be unique and ordered")
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("D8 factor response contains missing or non-finite values")
    if numeric.le(0).any().any():
        raise ValueError("D8 factor response contains non-positive values")
    numeric.index = dates
    bounded = numeric.loc[(numeric.index.date >= start) & (numeric.index.date <= end)]
    long = bounded.rename_axis("effective_date").stack().rename("single_factor").reset_index()
    long = long.rename(columns={"level_1": "security_id"})
    long = long.loc[~np.isclose(long.single_factor, 1.0, rtol=0, atol=5e-12)].copy()
    long["effective_date"] = long.effective_date.dt.date
    return long.sort_values(["effective_date", "security_id"], kind="stable").reset_index(drop=True)


def _implemented_actions(
    dividends: Iterable[dict[str, object]],
    rights: Iterable[dict[str, object]],
    *,
    start: date | None = None,
    cutoff: date,
) -> dict[tuple[str, date], list[dict[str, object]]]:
    result: dict[tuple[str, date], list[dict[str, object]]] = {}
    for raw in dividends:
        effective = _date_value(raw.get("DATE_EX"))
        code = raw.get("MARKET_CODE")
        if (not code or effective is None or effective > cutoff
                or start is not None and effective < start
                or str(raw.get("DIV_PROGRESS")) != "3"):
            continue
        row = dict(raw)
        row["_kind"] = "DIVIDEND"
        row["_known"] = _date_value(raw.get("DATE_DVD_ANN"))
        row["_changed"] = str(raw.get("IS_CHANGED")) in {"1", "1.0", "True", "true"}
        result.setdefault((str(code), effective), []).append(row)
    for raw in rights:
        effective = _date_value(raw.get("EX_DIVIDEND_DATE"))
        code = raw.get("MARKET_CODE")
        if (not code or effective is None or effective > cutoff
                or start is not None and effective < start
                or str(raw.get("PROGRESS")) != "3"):
            continue
        row = dict(raw)
        row["_kind"] = "RIGHTS"
        row["_known"] = _date_value(raw.get("EXECUTE_DATE"))
        row["_result"] = _date_value(raw.get("RESULT_DATE"))
        result.setdefault((str(code), effective), []).append(row)
    return result


def canonicalize_d8(
    factors: pd.DataFrame,
    dividends: Iterable[dict[str, object]],
    rights: Iterable[dict[str, object]],
    *,
    start: date | None = None,
    cutoff: date,
) -> D8Canonicalization:
    """Admit only implemented, pre-effective, revision-resolved D8 events."""
    required = {"security_id", "effective_date", "single_factor"}
    if not required <= set(factors):
        raise ValueError("D8 sparse factor schema is incomplete")
    work = factors.copy()
    work["effective_date"] = work.effective_date.map(_date_value)
    work["single_factor"] = pd.to_numeric(work.single_factor, errors="coerce")
    null_keys = int(work.security_id.isna().sum() + work.effective_date.isna().sum())
    invalid_factors = int((work.single_factor.isna() | ~np.isfinite(work.single_factor)
                           | work.single_factor.le(0)).sum())
    duplicate_keys = int(work.duplicated(["security_id", "effective_date"], keep=False).sum())
    if null_keys or invalid_factors or duplicate_keys:
        raise ValueError("D8 factor keys or measurements are invalid")
    actions = _implemented_actions(dividends, rights, start=start, cutoff=cutoff)
    canonical: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    matched = set()
    for row in work.sort_values(["effective_date", "security_id"]).to_dict("records"):
        key = (str(row["security_id"]), row["effective_date"])
        records = actions.get(key, [])
        kinds = sorted({str(item["_kind"]) for item in records})
        kind = "+".join(kinds) if kinds else "UNMATCHED"
        reason = None
        known_dates = [item.get("_known") for item in records]
        if not records:
            reason = "NO_IMPLEMENTED_ACTION_RECORD"
        elif any(value is None or value >= row["effective_date"] for value in known_dates):
            reason = "LATE_OR_UNKNOWN_IMPLEMENTATION_ANNOUNCEMENT"
        elif any(item["_kind"] == "DIVIDEND" and item.get("_changed") for item in records):
            reason = "DIVIDEND_REVISION_HISTORY_UNRESOLVED"
        elif any(item["_kind"] == "RIGHTS" and (
                item.get("_result") is None or item["_result"] > cutoff) for item in records):
            reason = "RIGHTS_RESULT_UNRESOLVED"
        if reason:
            excluded.append({"security_id": key[0], "effective_date": key[1],
                             "single_factor": row["single_factor"], "event_kind": kind,
                             "reason": reason})
            continue
        matched.add(key)
        sanitized = [{str(k): v for k, v in item.items() if not str(k).startswith("_")}
                     for item in records]
        canonical.append({"security_id": key[0], "effective_date": key[1],
                          "single_factor": row["single_factor"], "event_kind": kind,
                          "known_date": max(known_dates),
                          "source_record_count": len(records),
                          "source_event_hash": _stable_hash(sanitized)})
    action_without_factor = sorted(set(actions) - set(zip(work.security_id, work.effective_date)))
    for code, effective in action_without_factor:
        records = actions[(code, effective)]
        excluded.append({"security_id": code, "effective_date": effective,
                         "single_factor": None,
                         "event_kind": "+".join(sorted({x["_kind"] for x in records})),
                         "reason": "IMPLEMENTED_ACTION_WITHOUT_FACTOR_EVENT"})
    canonical_frame = pd.DataFrame(canonical, columns=CANONICAL_COLUMNS)
    exclusion_frame = pd.DataFrame(excluded, columns=EXCLUSION_COLUMNS)
    reasons = (exclusion_frame.reason.value_counts().sort_index().to_dict()
               if not exclusion_frame.empty else {})
    quality = {
        "raw_factor_events": len(work),
        "implemented_action_dates": len(actions),
        "canonical_rows": len(canonical_frame),
        "excluded_rows": len(exclusion_frame),
        "exclusion_reasons": {str(key): int(value) for key, value in reasons.items()},
        "null_key_rows": null_keys,
        "duplicate_keys": duplicate_keys,
        "invalid_factor_rows": invalid_factors,
        "canonical_duplicate_keys": int(canonical_frame.duplicated(
            ["security_id", "effective_date"]).sum()) if not canonical_frame.empty else 0,
        "canonical_status": "PASS",
    }
    return D8Canonicalization(canonical_frame, exclusion_frame, quality)


def derive_d5_events(factors: pd.DataFrame) -> pd.DataFrame:
    """Derive sparse single-event ratios from the frozen D5 backward factors."""
    required = {"security_id", "trade_date", "factor"}
    if not required <= set(factors):
        raise ValueError("D5 schema is incomplete")
    work = factors.copy()
    work["trade_date"] = pd.to_datetime(work.trade_date, errors="coerce")
    work["factor"] = pd.to_numeric(work.factor, errors="coerce")
    if (work[["security_id", "trade_date", "factor"]].isna().any().any()
            or work.duplicated(["security_id", "trade_date"]).any()
            or work.factor.le(0).any()):
        raise ValueError("D5 rows are invalid")
    work = work.sort_values(["security_id", "trade_date"], kind="stable")
    work["previous_factor"] = work.groupby("security_id", sort=False).factor.shift()
    work["single_factor"] = work.factor / work.previous_factor
    events = work.loc[work.previous_factor.notna() & ~np.isclose(
        work.single_factor, 1.0, rtol=0, atol=5e-12)].copy()
    return events.rename(columns={"trade_date": "effective_date"})[
        ["security_id", "effective_date", "previous_factor", "factor", "single_factor"]
    ].reset_index(drop=True)


def reconcile_d8_with_d5(
    d8_events: pd.DataFrame,
    d5_factors: pd.DataFrame,
    *,
    qualified_boundary_keys: set[tuple[str, date]] | None = None,
) -> dict[str, object]:
    """Require the D8 event set and rounded D5 cumulative changes to agree."""
    d5 = derive_d5_events(d5_factors)
    left = d8_events.copy()
    left["effective_date"] = pd.to_datetime(left.effective_date, errors="coerce")
    merged = left.merge(d5, on=["security_id", "effective_date"], how="outer",
                        suffixes=("_d8", "_d5"), indicator=True)
    first_d5_date = pd.to_datetime(d5_factors.trade_date).min().date()
    qualified_boundary_keys = qualified_boundary_keys or set()
    left_only = merged.loc[merged._merge.eq("left_only")]
    boundary = (left_only.apply(
        lambda row: (row.security_id, row.effective_date.date()) in qualified_boundary_keys
        and row.effective_date.date() == first_d5_date, axis=1)
        if len(left_only) else pd.Series(dtype=bool))
    mismatch = []
    for row in merged.loc[merged._merge.eq("both")].itertuples():
        lower, upper = backward_ratio_bounds(
            Decimal(str(row.previous_factor)), Decimal(str(row.factor)),
        )
        inverse = 1 / Decimal(str(row.single_factor_d8))
        if not lower <= inverse <= upper:
            mismatch.append({"security_id": row.security_id,
                             "effective_date": row.effective_date.date().isoformat()})
    result = {
        "d8_event_rows": len(left),
        "d5_event_rows": len(d5),
        "matched_event_rows": int(merged._merge.eq("both").sum()),
        "d8_without_d5": int(merged._merge.eq("left_only").sum()),
        "qualified_window_boundary_events": int(boundary.sum()),
        "unqualified_d8_without_d5": int((~boundary).sum()),
        "d5_without_d8": int(merged._merge.eq("right_only").sum()),
        "ratio_mismatch_rows": len(mismatch),
        "ratio_mismatch_examples": mismatch[:20],
        "d8_without_d5_examples": [
            {"security_id": row.security_id,
             "effective_date": row.effective_date.date().isoformat(),
             "single_factor": float(row.single_factor_d8)}
            for row in merged.loc[merged._merge.eq("left_only")].head(20).itertuples()],
        "d5_without_d8_examples": [
            {"security_id": row.security_id,
             "effective_date": row.effective_date.date().isoformat(),
             "single_factor": float(row.single_factor_d5)}
            for row in merged.loc[merged._merge.eq("right_only")].head(20).itertuples()],
    }
    result["status"] = "PASS" if not any(result[key] for key in (
        "unqualified_d8_without_d5", "d5_without_d8", "ratio_mismatch_rows")) else "FAIL"
    return result


def planned_factor_refresh_codes(
    dividends: Iterable[dict[str, object]], rights: Iterable[dict[str, object]],
) -> tuple[str, ...]:
    """Refresh the all-history factor endpoint only for newly returned action names."""
    return tuple(sorted({str(row["MARKET_CODE"]) for row in (*tuple(dividends), *tuple(rights))
                         if row.get("MARKET_CODE")}))
