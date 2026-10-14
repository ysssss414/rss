"""Bounded raw evidence contracts and immutable local captures; no live SDK backend."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path

import pandas as pd

from research.data.cache import SnapshotStore
from research.data.contracts import DataContractError, DataRequest
from research.data.validation import validate_calendar, validate_price_values, validate_timestamps
from research.runs.manifest import canonical_bytes, digest, frame_records


AUTHORIZED = {"300418.SZ": ("2022-07-01", "2023-12-31"),
    "000858.SZ": ("2019-07-01", "2022-03-31"), "603259.SH": ("2026-09-24", "2026-10-13")}


def bounded_request(code, start, end, dataset, stage="full"):
    request = DataRequest((code,), date.fromisoformat(start), date.fromisoformat(end))
    limits = AUTHORIZED.get(code)
    if dataset not in {"bars", "status"} or limits is None or not limits[0] <= start <= end <= limits[1]:
        raise ValueError("UNAUTHORIZED_REQUEST_SCOPE")
    if stage not in {"smoke", "full"} or stage == "smoke" and (code != "603259.SH" or (request.end-request.start).days > 2):
        raise ValueError("INVALID_SMOKE_SCOPE")
    return request


def require_network_scope(audit):
    if not (audit.get("native_initialization_scope_verified") is True and
            audit.get("native_query_scope_verified") is True and audit.get("native_scope_evidence") and
            type(audit.get("native_request_upper_bound")) is int):
        raise ValueError("STOP_NETWORK_SCOPE_UNQUALIFIED")
    # Current V0.4 delivers no live backend. Future evidence needs a separately reviewed implementation.
    raise ValueError("NO_QUALIFIED_LIVE_BACKEND_V0_4")


def validate_response(frame, request):
    required = {"security_id", "trade_date"}
    if not required <= set(frame):
        raise ValueError("MISSING_RESPONSE_IDENTITY")
    result = frame.copy()
    if not result.security_id.eq(request.codes[0]).all():
        raise ValueError("RESPONSE_SECURITY_OUT_OF_SCOPE")
    dates = pd.to_datetime(result.trade_date, format="%Y-%m-%d", errors="raise")
    if dates.isna().any() or not dates.dt.date.between(request.start, request.end).all():
        raise ValueError("RESPONSE_DATE_OUT_OF_SCOPE")
    result["trade_date"] = dates.dt.strftime("%Y-%m-%d")
    if result.duplicated(["security_id", "trade_date"]).any():
        raise ValueError("DUPLICATE_SECURITY_DAY")
    return result.sort_values(["security_id", "trade_date"]).reset_index(drop=True)


class OfflineCaptureProbe:
    """Exercise the authorized Python attempt budget with an explicitly offline fake SDK.

    This cannot establish native network scope or authorize a real backend.
    """
    def __init__(self, fake_sdk):
        if getattr(fake_sdk, "offline_probe_only", None) is not True:
            raise ValueError("OFFLINE_FAKE_SDK_REQUIRED")
        self.sdk, self.log, self.completed, self.smoke_pass = fake_sdk, [], set(), set()

    def call(self, stage, code, start, end, dataset):
        request = bounded_request(code, start, end, dataset, stage)
        key = (stage, code, dataset)
        if stage == "full" and self.smoke_pass != {"bars", "status"}:
            raise ValueError("SMOKE_NOT_PASSED")
        if key in self.completed:
            raise ValueError("INTERFACE_ALREADY_FINISHED")
        self.completed.add(key)
        for attempt in (1, 2):
            if len(self.log) >= 16:
                raise ValueError("PYTHON_ATTEMPT_BUDGET_EXHAUSTED")
            row = {"stage": stage, "code": code, "dataset": dataset, "attempt": attempt,
                   "start": start, "end": end}
            self.log.append(row)
            try:
                response = self.sdk.query(dataset, request)
                result = validate_response(response, request)
            except (ConnectionError, TimeoutError) as exc:
                row["status"] = type(exc).__name__
                if attempt == 2:
                    raise
            else:
                row["status"] = "RESPONSE_SCOPE_PASS_ONLY"
                if stage == "smoke":
                    self.smoke_pass.add(dataset)
                return result


def raw_evidence(bars, status, request, calendar, *, units_verified):
    """Grade raw fields, without declaring continuous adjusted prices or PIT qualification."""
    if type(units_verified) is not bool:
        raise ValueError("EXPLICIT_BOOLEAN_UNIT_QUALIFICATION_REQUIRED")
    days = validate_calendar(calendar, request.start, request.end)
    bars, status = [validate_response(f, request) for f in (bars, status)]
    if not (set(bars.trade_date) | set(status.trade_date)) <= {d.isoformat() for d in days}:
        raise ValueError("RESPONSE_NOT_IN_CALENDAR")
    if not status.trading_status.isin({"TRADING", "SUSPENDED", "UNKNOWN"}).all():
        raise ValueError("INVALID_TRADING_STATUS")
    grid = pd.DataFrame({"trade_date": [d.isoformat() for d in days], "security_id": request.codes[0]})
    grid = grid.merge(status, how="left", validate="one_to_one").merge(bars, how="left", validate="one_to_one")
    grid["trading_status"] = grid.trading_status.fillna("UNKNOWN")
    grid["issue"] = ""
    grid.loc[grid.trading_status.eq("UNKNOWN"), "issue"] = "MISSING_OR_UNKNOWN_STATUS"
    grid.loc[grid.trading_status.eq("SUSPENDED"), "issue"] = "SUSPENDED"
    prices = ["raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]
    missing = grid[prices].isna().any(axis=1) & grid.trading_status.eq("TRADING")
    grid.loc[missing, "issue"] = "MISSING_BAR"
    validate_price_values(grid.copy(), grid.issue.eq(""))
    exploratory = bool(days) and not bars.empty and units_verified and grid.issue.isin(["", "SUSPENDED"]).all()
    return grid, {"exploratory": "EXPLORATORY_DATA_QUALIFIED" if exploratory else "UNQUALIFIED",
        "strict_pit_execution": "UNQUALIFIED", "price_basis": "RAW_ONLY_NO_CONTINUOUS_ADJUSTED_PRICE",
        "valid_trading_bars": int(grid.issue.eq("").sum()), "issues": grid.issue.value_counts().to_dict(),
        "missing_evidence": ["PIT_FACTOR_ACTION_IMPLEMENTATION_AND_REVISION", "STATUS_EFFECTIVE_TIMING",
            "HISTORICAL_MORNING_CONSTRAINTS", "EXECUTION_PROOF"] + ([] if units_verified else ["RAW_UNITS"])}


def freeze_capture(root, *, code, start, end, bars, status, calendar, metadata, evidence=None):
    """One content-addressed manifest embeds raw datasets; identity excludes no evidence."""
    request = bounded_request(code, start, end, "bars")
    retrieved = metadata["retrieved_at"]
    validate_timestamps(pd.Series([retrieved]), "retrieved_at")
    if not all(metadata.get(k) for k in ("source", "api_version", "code_sha256", "config_sha256", "origin")):
        raise ValueError("MISSING_CAPTURE_PROVENANCE")
    if metadata["units_verified"] is True and not metadata.get("unit_evidence"):
        raise ValueError("MISSING_UNIT_EVIDENCE")
    grid, grade = raw_evidence(bars, status, request, calendar, units_verified=metadata["units_verified"])
    data = {"bars": frame_records(validate_response(bars, request)),
        "status": frame_records(validate_response(status, request)), "calendar": [d.isoformat() for d in calendar],
        "available_factor_event_evidence": evidence or []}
    bounds = {k: [min(r["trade_date"] for r in rows), max(r["trade_date"] for r in rows)] if rows else [None, None]
              for k, rows in data.items() if k in {"bars", "status"}}
    payload = {"schema": "trend-exit-raw-capture/0.4", "security_id": code,
        "requested_range": [start, end], "actual_returned_ranges": bounds, "metadata": dict(metadata),
        "qualification": grade, "datasets": data, "dataset_rows": {k: len(v) for k, v in data.items()},
        "fingerprints": {k: digest(v) for k, v in data.items()}, "daily_quality": frame_records(grid[["security_id", "trade_date", "trading_status", "issue"]])}
    if code == "603259.SH":
        payload.update(observation_id="WUXI_FORWARD_20261013_V0_1",
            capture_label="LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE" if pd.Timestamp(retrieved).tz_convert("Asia/Shanghai").date() > date(2026, 10, 13) else "CAPTURE_TIMESTAMP_REQUIRES_OBSERVATION_REVIEW",
            same_day_prospective=False, post_inception_validation=False)
    content = canonical_bytes(payload)
    identity = sha256(content).hexdigest()
    path = Path(root) / code / identity / "manifest.json"
    SnapshotStore(root)._atomic_write(path, content, immutable=True)
    return {"snapshot_id": identity, "manifest_sha256": identity, "path": str(path), "qualification": grade}


def load_capture(path, expected_hash):
    import json
    content = Path(path).read_bytes()
    if sha256(content).hexdigest() != expected_hash:
        raise DataContractError("SNAPSHOT_CORRUPT", "Capture manifest checksum mismatch")
    value = json.loads(content)
    if value["schema"] != "trend-exit-raw-capture/0.4":
        raise ValueError("UNSUPPORTED_CAPTURE_SCHEMA")
    for name, rows in value["datasets"].items():
        if digest(rows) != value["fingerprints"][name] or len(rows) != value["dataset_rows"][name]:
            raise DataContractError("SNAPSHOT_CORRUPT", "Dataset identity mismatch")
    return value
