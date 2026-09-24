"""Checkpointed AmazingData D8 acquisition into ignored raw cache."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sys
import time

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.amazingdata_batch import batch_groups, save_json
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat


PRIVATE = ROOT / ".local_research_data" / "d8"
STORE = ROOT / "data" / "market_store"
START, CUTOFF = "2024-01-01", "2026-09-23"
ANNOUNCEMENT_START, ANNOUNCEMENT_END = 20130101, 20260923
ENDPOINTS = ("single_event_factor", "dividend", "right_issue")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def record_ok(record: dict[str, object]) -> bool:
    path = Path(str(record.get("cache_file", "")))
    return (record.get("status") == "COMPLETED" and path.is_file()
            and digest(path) == record.get("sha256"))


def load_universe(store: Path = STORE) -> list[str]:
    con = duckdb.connect()
    try:
        rows = con.execute("SELECT security_id FROM read_parquet(?) ORDER BY security_id",
                           [str(store / "security_master" / "part.parquet")]).fetchall()
    finally:
        con.close()
    codes = [row[0] for row in rows]
    if len(codes) != 5222 or len(codes) != len(set(codes)):
        raise ValueError("D8 requires the frozen 5,222-security D1 universe")
    return codes


def expected_keys(codes: list[str], batch_size: int) -> list[tuple[str, list[str], str]]:
    result = []
    for batch_id, group in enumerate(batch_groups(codes, batch_size), 1):
        for endpoint in ENDPOINTS:
            result.append((f"{endpoint}:batch_{batch_id:03d}", group, endpoint))
    return result


def _provider(adapter):
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            return adapter._call_with_retry(lambda session: session)


def _call(operation, record: dict[str, object]):
    for attempt in range(3):
        began = time.perf_counter()
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with redirect_stdout(sink), redirect_stderr(sink):
                    value = operation()
            record["attempts"].append({"at": now(), "status": "SUCCESS",
                                       "elapsed_seconds": round(time.perf_counter() - began, 6)})
            return value
        except Exception as exc:
            record["attempts"].append({"at": now(), "status": "FAILED",
                                       "error_type": type(exc).__name__,
                                       "elapsed_seconds": round(time.perf_counter() - began, 6)})
            if attempt == 2:
                raise
            time.sleep(min(2 ** attempt, 4))
    raise AssertionError("unreachable")


def _action_payload(value: object, codes: list[str]) -> dict[str, object]:
    frames = []
    if isinstance(value, pd.DataFrame):
        frames = [value]
    elif isinstance(value, dict):
        frames = [frame for frame in value.values() if isinstance(frame, pd.DataFrame)]
    else:
        raise TypeError("Unexpected D8 action response")
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not frame.empty and ("MARKET_CODE" not in frame or
                            not set(frame.MARKET_CODE.dropna().astype(str)) <= set(codes)):
        raise ValueError("D8 action response identity differs from request")
    return {"columns": [str(column) for column in frame.columns],
            "rows": json.loads(frame.to_json(orient="records", date_format="iso"))}


def _save_result(endpoint: str, value: object, path: Path, codes: list[str]) -> tuple[int, dict[str, object]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if endpoint == "single_event_factor":
        if not isinstance(value, pd.DataFrame) or list(value.columns) != codes:
            raise ValueError("D8 factor response identity/order differs from request")
        dates = pd.to_datetime(value.index, errors="coerce")
        if dates.isna().any() or not dates.is_unique or not dates.is_monotonic_increasing:
            raise ValueError("D8 factor response dates invalid")
        value.to_hdf(temporary, key=endpoint, mode="w")
        metadata = {"returned_date_start": str(dates[0].date()),
                    "returned_date_end": str(dates[-1].date()),
                    "returned_columns": len(value.columns)}
        rows = len(value)
    else:
        payload = _action_payload(value, codes)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        rows = len(payload["rows"])
        metadata = {"returned_columns": payload["columns"]}
    temporary.replace(path)
    return rows, metadata


def acquire(*, batch_size: int = 100, stop_after: int | None = None,
            private: Path = PRIVATE, store: Path = STORE) -> dict[str, object]:
    codes = load_universe(store)
    private.mkdir(parents=True, exist_ok=True)
    checkpoint = private / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {
        "schema": "stage1-d8-acquisition/1", "source": "AmazingData", "sdk_version": "1.1.6",
        "cutoff_date": CUTOFF, "announcement_start": str(ANNOUNCEMENT_START),
        "announcement_end": str(ANNOUNCEMENT_END), "batch_size": batch_size,
        "security_count": len(codes), "created_at": now(), "records": {}, "segments": [],
    }
    if (state["cutoff_date"] != CUTOFF or state["batch_size"] != batch_size
            or state["security_count"] != len(codes)):
        raise ValueError("D8 checkpoint configuration changed")
    planned = expected_keys(codes, batch_size)
    pending = [(key, group, endpoint) for key, group, endpoint in planned
               if not record_ok(state["records"].get(key, {}))]
    segment = {"started_at": now(), "planned_records": len(planned),
               "skipped_completed": len(planned) - len(pending), "new_completed": 0,
               "remote_request_attempts": 0, "elapsed_seconds": 0.0}
    state["segments"].append(segment)
    save_json(checkpoint, state)
    if not pending:
        segment.update({"finished_at": now(), "status": "NO_OP_NO_SESSION"})
        save_json(checkpoint, state)
        return segment
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
        cache_dir=private / "sdk", retry_count=1, use_numba_compat=True)
    provider = _provider(adapter)
    info = provider.ad.InfoData()
    began = time.perf_counter()
    for key, group, endpoint in pending:
        record = state["records"].get(key, {"endpoint": endpoint, "security_ids": group,
            "batch_id": key.rsplit(":", 1)[-1], "cutoff_date": CUTOFF,
            "attempts": [], "started_at": now()})
        record["status"] = "RUNNING"
        state["records"][key] = record
        save_json(checkpoint, state)
        try:
            local = str(private / "sdk" / endpoint) + os.sep
            if endpoint == "single_event_factor":
                value = _call(lambda: provider.base.get_adj_factor(
                    group, local_path=local, is_local=False), record)
                suffix = ".h5"
            elif endpoint == "dividend":
                value = _call(lambda: info.get_dividend(
                    group, local_path=local, is_local=False,
                    begin_date=ANNOUNCEMENT_START, end_date=ANNOUNCEMENT_END), record)
                suffix = ".json"
            else:
                value = _call(lambda: info.get_right_issue(
                    group, local_path=local, is_local=False,
                    begin_date=ANNOUNCEMENT_START, end_date=ANNOUNCEMENT_END), record)
                suffix = ".json"
            path = private / "raw" / f"{key.replace(':', '_')}{suffix}"
            rows, metadata = _save_result(endpoint, value, path, group)
            record.update({"status": "COMPLETED", "finished_at": now(), "rows": rows,
                           "cache_file": str(path), "sha256": digest(path), **metadata})
            segment["new_completed"] += 1
            segment["remote_request_attempts"] += len(record["attempts"])
            segment["elapsed_seconds"] = round(time.perf_counter() - began, 6)
            save_json(checkpoint, state)
            if stop_after is not None and segment["new_completed"] >= stop_after:
                segment.update({"finished_at": now(), "status": "INTENTIONAL_STOP"})
                save_json(checkpoint, state)
                return segment
        except BaseException as exc:
            record.update({"status": "FAILED", "finished_at": now(),
                           "error_type": type(exc).__name__})
            segment.update({"finished_at": now(), "status": "FAILED",
                            "elapsed_seconds": round(time.perf_counter() - began, 6)})
            save_json(checkpoint, state)
            raise
    segment.update({"finished_at": now(), "status": "COMPLETED",
                    "elapsed_seconds": round(time.perf_counter() - began, 6)})
    save_json(checkpoint, state)
    return segment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--stop-after", type=int)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 200 or args.stop_after is not None and args.stop_after < 1:
        raise SystemExit("Invalid batch or stop limit")
    print(json.dumps(acquire(batch_size=args.batch_size, stop_after=args.stop_after),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
