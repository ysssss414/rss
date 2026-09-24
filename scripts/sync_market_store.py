"""Checkpointed AmazingData acquisition for the ignored local market store.

Raw cache and checkpoint live outside Git. Canonical Parquet materialization is
separate, so a network interruption cannot alter research data.
"""

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

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.amazingdata_batch import batch_groups, factor_quality, normalize_factors, save_json
from research.data.amazingdata import checked_sdk_bars
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

PRIVATE = ROOT / ".local_research_data" / "market_store"
OLD_100 = ROOT / ".local_research_data" / "stage1_amazingdata_benchmark"
OLD_500 = ROOT / ".local_research_data" / "stage1_amazingdata_acquisition_optimization" / "sustained500"
SAMPLE_100 = ROOT / "artifacts" / "stage1_amazingdata_100_benchmark" / "sample_universe.csv"
SAMPLE_500 = ROOT / "artifacts" / "stage1_amazingdata_acquisition_optimization" / "sample_universe_500.csv"
START, END = "2024-01-01", "2026-09-23"
END_INT = 20260923


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def record_ok(record: dict) -> bool:
    path = Path(record.get("cache_file", ""))
    return (record.get("status") == "COMPLETED" and path.is_file()
            and digest(path) == record.get("sha256"))


def session(adapter):
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            return adapter._call_with_retry(lambda provider: provider)


def call_with_retry(adapter, provider, operation, record):
    for attempt in range(3):
        began = time.perf_counter()
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with redirect_stdout(sink), redirect_stderr(sink):
                    value = operation(provider)
            record["attempts"].append({"at": now(), "status": "SUCCESS",
                "elapsed_seconds": round(time.perf_counter() - began, 6)})
            return value, provider
        except Exception as exc:
            record["attempts"].append({"at": now(), "status": "FAILED",
                "error_type": type(exc).__name__, "elapsed_seconds": round(time.perf_counter() - began, 6)})
            transient = isinstance(exc, (ConnectionError, TimeoutError, TypeError)) or "rate" in str(exc).lower()
            if not transient or attempt == 2:
                raise
            adapter._session_provider = None
            provider = session(adapter)
            record["session_restart_count"] += 1
            time.sleep(min(2 ** attempt, 4))
    raise AssertionError("unreachable")


def validate_cache(path: Path, sha: str, columns: set[str], *, code: str | None = None) -> int:
    if not path.is_file() or digest(path) != sha:
        raise ValueError(f"Benchmark cache hash mismatch: {path.name}")
    frame = pd.read_csv(path, compression="gzip")
    if not columns <= set(frame) or frame.empty:
        raise ValueError(f"Benchmark cache schema/rows invalid: {path.name}")
    if code is not None:
        key = "code" if "code" in frame else "MARKET_CODE"
        if frame[key].dropna().ne(code).any():
            raise ValueError(f"Benchmark cache identity mismatch: {path.name}")
    if "factor" in frame:
        dates = pd.to_datetime(frame.trade_date, errors="coerce")
        if (frame.duplicated(["security_id", "trade_date"]).any() or dates.isna().any()
                or pd.to_numeric(frame.factor, errors="coerce").le(0).any()):
            raise ValueError(f"Benchmark factor cache quality invalid: {path.name}")
    elif "date" in frame:
        dates = pd.to_datetime(frame.date, errors="coerce")
        if frame.duplicated(["code", "date"]).any() or dates.isna().any():
            raise ValueError(f"Benchmark bars cache quality invalid: {path.name}")
    elif "TRADE_DATE" in frame:
        dates = pd.to_datetime(pd.to_numeric(frame.TRADE_DATE, errors="coerce").astype("Int64").astype(str),
                               format="%Y%m%d", errors="coerce")
        keyed = frame.loc[frame.MARKET_CODE.notna() & dates.notna()]
        if keyed.duplicated(["MARKET_CODE", "TRADE_DATE"]).any():
            raise ValueError(f"Benchmark status cache duplicate keys: {path.name}")
    else:
        raise ValueError(f"Unknown benchmark cache schema: {path.name}")
    if dates.dropna().lt(pd.Timestamp(START)).any() or dates.dropna().gt(pd.Timestamp(END)).any():
        raise ValueError(f"Benchmark cache outside initial window: {path.name}")
    return len(frame)


def initialize_cache_reuse(state: dict, codes: list[str]) -> None:
    if state.get("cache_reuse_initialized"):
        return
    prior = json.loads((OLD_100 / "checkpoint.json").read_text(encoding="utf-8"))
    factor = json.loads((OLD_500 / "checkpoint.json").read_text(encoding="utf-8"))
    if (prior["date_start"], prior["date_end"]) != (START, END) or (
        factor["config"]["date_start"], factor["config"]["date_end"], factor["config"]["batch_size"]
    ) != (START, END, 100):
        raise ValueError("Benchmark window/batch differs from initial sync")
    import csv
    with SAMPLE_100.open(encoding="utf-8", newline="") as stream:
        fixed = [row["security_id"] for row in csv.DictReader(stream)]
    with SAMPLE_500.open(encoding="utf-8", newline="") as stream:
        extended = [row["security_id"] for row in csv.DictReader(stream)]
    if len(fixed) != 100 or len(extended) != 500 or extended[:100] != fixed or not set(extended) <= set(codes):
        raise ValueError("Benchmark sample is not a subset of live universe")
    reuse = {"D3_bars": {"securities": 0, "rows": 0, "remote_calls_avoided": 0},
             "D4_D6_status": {"securities": 0, "rows": 0, "remote_calls_avoided": 0},
             "D5_factor": {"securities": 0, "rows": 0, "remote_calls_avoided": 0}}
    for code in fixed:
        for name, columns in (("D3_bars", {"code", "date", "open", "high", "low", "close", "volume", "amount"}),
                              ("D4_D6_status", {"MARKET_CODE", "TRADE_DATE", "PRECLOSE", "HIGH_LIMITED", "LOW_LIMITED",
                                                "PRICE_HIGH_LMT_RATE", "PRICE_LOW_LMT_RATE", "IS_ST_SEC",
                                                "IS_SUSP_SEC", "IS_WD_SEC", "IS_XR_SEC"})):
            old = prior["records"][f"{name}:{code}"]
            path = OLD_100 / old["cache_file"]
            rows = validate_cache(path, old["sha256"], columns, code=code)
            key = f"{name}:{code}"
            state["records"][key] = {"dataset": name, "batch_id": code, "security_ids": [code],
                "date_start": START, "date_end": END, "status": "COMPLETED", "rows": rows,
                "attempts": [], "started_at": old["started_at"], "finished_at": old["finished_at"],
                "cache_file": str(path), "sha256": old["sha256"], "reused": True}
            reuse[name]["securities"] += 1
            reuse[name]["rows"] += rows
            reuse[name]["remote_calls_avoided"] += 1
    for batch_id, group in enumerate(batch_groups(extended, 100), 1):
        old = factor["records"][f"batch_{batch_id:03d}"]
        path = OLD_500 / old["cache_file"]
        rows = validate_cache(path, old["sha256"], {"security_id", "trade_date", "factor"})
        if old["security_ids"] != group:
            raise ValueError("Factor batch identities differ from benchmark")
        key = f"D5_factor:batch_{batch_id:03d}"
        state["records"][key] = {"dataset": "D5_factor", "batch_id": batch_id, "security_ids": group,
            "date_start": START, "date_end": END, "status": "COMPLETED", "rows": rows,
            "attempts": [], "started_at": old["started_at"], "finished_at": old["finished_at"],
            "cache_file": str(path), "sha256": old["sha256"], "reused": True}
        reuse["D5_factor"]["securities"] += 100
        reuse["D5_factor"]["rows"] += rows
        reuse["D5_factor"]["remote_calls_avoided"] += 1
    state["cache_reuse"] = reuse
    state["cache_reuse_initialized"] = True


def acquire(phase: str, stop_after: int | None) -> None:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    checkpoint = PRIVATE / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {
        "schema": "stage1-market-acquisition/1", "date_start": START, "date_end": END,
        "created_at": now(), "records": {}, "segments": [], "source": "AmazingData"}
    if (state["date_start"], state["date_end"]) != (START, END):
        raise ValueError("Checkpoint window changed")
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
        cache_dir=PRIVATE / "sdk", retry_count=1, use_numba_compat=True)
    provider = session(adapter)
    universe_path = PRIVATE / "universe.json"
    if universe_path.exists():
        codes = json.loads(universe_path.read_text(encoding="utf-8"))["security_ids"]
    else:
        probe = {"attempts": [], "session_restart_count": 0}
        raw, provider = call_with_retry(adapter, provider, lambda p: p.base.get_hist_code_list(
            security_type="EXTRA_STOCK_A", start_date=END_INT, end_date=END_INT,
            local_path=str(PRIVATE / "sdk" / "universe") + os.sep), probe)
        codes = sorted({code for code in raw if code.endswith((".SH", ".SZ"))})
        if len(codes) != 5222:
            raise ValueError(f"Expected 5,222 SH/SZ A shares, got {len(codes)}")
        save_json(universe_path, {"security_ids": codes, "retrieved_at": now(), "source": "AmazingData"})
    if len(codes) != 5222 or len(codes) != len(set(codes)):
        raise ValueError("Invalid cached universe")
    initialize_cache_reuse(state, codes)
    save_json(checkpoint, state)
    import csv
    with SAMPLE_500.open(encoding="utf-8", newline="") as stream:
        first_500 = [row["security_id"] for row in csv.DictReader(stream)]
    ordered = list(dict.fromkeys(first_500 + codes))
    if phase == "D1_basic":
        groups = [(f"batch_{i:03d}", group) for i, group in enumerate(batch_groups(ordered, 80), 1)]
    elif phase == "D5_factor":
        groups = [(f"batch_{i:03d}", group) for i, group in enumerate(batch_groups(ordered, 100), 1)]
    else:
        groups = [(code, [code]) for code in ordered]
    if phase != "D1_basic":
        master = PRIVATE / "security_master.csv.gz"
        if not master.exists():
            raise ValueError("D1 must complete before security-day endpoints")
        basics = pd.read_csv(master, compression="gzip")
        listing = dict(zip(basics.security_id, basics.listing_date, strict=True))
    segment = {"phase": phase, "started_at": now(), "new_completed": 0,
               "skipped_completed": 0, "remote_request_attempts": 0, "elapsed_seconds": 0.0}
    state["segments"].append(segment)
    began = time.perf_counter()
    save_json(checkpoint, state)
    for batch_id, group in groups:
        key = f"{phase}:{batch_id}"
        if record_ok(state["records"].get(key, {})):
            segment["skipped_completed"] += 1
            continue
        record = state["records"].get(key, {"dataset": phase, "batch_id": batch_id,
            "security_ids": group, "date_start": START, "date_end": END,
            "attempts": [], "session_restart_count": 0, "started_at": now()})
        record["status"] = "RUNNING"
        state["records"][key] = record
        save_json(checkpoint, state)
        try:
            if phase == "D1_basic":
                value, provider = call_with_retry(adapter, provider,
                    lambda p: p.ad.InfoData().get_stock_basic(group), record)
                frame = pd.DataFrame(value)
                if set(frame.MARKET_CODE) != set(group) or frame.MARKET_CODE.duplicated().any():
                    raise ValueError("D1 identity/uniqueness mismatch")
            elif phase == "D3_bars":
                code = group[0]
                frame, provider = call_with_retry(adapter, provider,
                    lambda p: checked_sdk_bars(p, code, pd.Timestamp(START).date(), pd.Timestamp(END).date()), record)
                required = {"code", "date", "open", "high", "low", "close", "volume", "amount"}
                if not required <= set(frame) or not frame.code.eq(code).all():
                    raise ValueError("D3 schema/identity response")
            elif phase == "D4_D6_status":
                code = group[0]
                value, provider = call_with_retry(adapter, provider,
                    lambda p: p.ad.InfoData().get_history_stock_status([code],
                        local_path=str(PRIVATE / "sdk" / "status") + os.sep,
                        is_local=False, begin_date=20240101, end_date=END_INT), record)
                if not isinstance(value, dict) or set(value) != {code}:
                    raise ValueError("D4 mapping mismatch")
                frame = pd.DataFrame(value[code])
                required = {"MARKET_CODE", "TRADE_DATE", "PRECLOSE", "HIGH_LIMITED", "LOW_LIMITED",
                    "PRICE_HIGH_LMT_RATE", "PRICE_LOW_LMT_RATE", "IS_ST_SEC", "IS_SUSP_SEC",
                    "IS_WD_SEC", "IS_XR_SEC"}
                if frame.empty:
                    frame = pd.DataFrame(columns=sorted(required))
                if not required <= set(frame) or frame.MARKET_CODE.dropna().ne(code).any():
                    raise ValueError("D4 schema/identity response")
            else:
                value, provider = call_with_retry(adapter, provider,
                    lambda p: p.base.get_backward_factor(group,
                        local_path=str(PRIVATE / "sdk" / "factor") + os.sep, is_local=False), record)
                frame = normalize_factors(value, group, listing)
                q = factor_quality(frame)
                if q["duplicate_security_date"] or q["missing_factor"] or q["non_positive_factor"]:
                    raise ValueError(f"D5 factor quality: {q}")
            path = PRIVATE / "staging" / phase / f"{batch_id.replace('.', '_')}.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp.gz")
            frame.to_csv(temporary, index=False, compression="gzip")
            temporary.replace(path)
            record.update(status="COMPLETED", rows=len(frame), cache_file=str(path),
                          sha256=digest(path), finished_at=now(), reused=False,
                          empty_response=frame.empty)
            if frame.empty and sum(row.get("empty_response", False) for row in state["records"].values()
                                   if row["dataset"] == phase) > 52:
                raise ValueError(f"Systemic empty {phase} responses")
            segment["new_completed"] += 1
            segment["remote_request_attempts"] += len(record["attempts"])
        except Exception as exc:
            record.update(status="FAILED", finished_at=now(), error_type=type(exc).__name__, error=str(exc)[:300])
            segment["remote_request_attempts"] += len(record["attempts"])
            save_json(checkpoint, state)
            raise
        segment["elapsed_seconds"] = round(time.perf_counter() - began, 6)
        save_json(checkpoint, state)
        if phase in {"D1_basic", "D5_factor"} or segment["new_completed"] % 100 == 0:
            print(f"{phase} {batch_id}: {record['rows']} rows ({segment['new_completed']} new)", flush=True)
        if stop_after and segment["new_completed"] >= stop_after:
            break
    segment["elapsed_seconds"] = round(time.perf_counter() - began, 6)
    segment["finished_at"] = now()
    save_json(checkpoint, state)
    if phase == "D1_basic" and segment["skipped_completed"] + segment["new_completed"] == len(groups):
        frames = [pd.read_csv(state["records"][f"D1_basic:{key}"]["cache_file"], compression="gzip")
                  for key, _ in groups]
        frame = pd.concat(frames, ignore_index=True)
        plate = frame.LISTPLATE_NAME
        board = plate.map({"科创板": "STAR", "创业板": "ChiNext"})
        board.loc[plate.eq("主板") & frame.MARKET_CODE.str.endswith(".SH")] = "SSE Main"
        board.loc[plate.eq("主板") & frame.MARKET_CODE.str.endswith(".SZ")] = "SZSE Main"
        frame = pd.DataFrame({"security_id": frame.MARKET_CODE, "exchange": frame.MARKET_CODE.str[-2:],
            "board": board, "listing_date": pd.to_datetime(
                frame.LISTDATE.astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d"),
            "delisting_date": pd.to_datetime(frame.DELISTDATE.astype("Int64").astype(str),
                format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d"),
            "security_name": frame.SECURITY_NAME})
        if (len(frame) != 5222 or frame.security_id.duplicated().any()
                or frame.security_name.str.contains("�").any() or frame.board.isna().any()):
            raise ValueError("D1 canonical master count, key, name, or board invalid")
        path = PRIVATE / "security_master.csv.gz"
        temporary = path.with_suffix(".tmp.gz")
        frame.to_csv(temporary, index=False, compression="gzip")
        temporary.replace(path)
    print(json.dumps({"phase": phase, "segment": segment,
        "complete": segment["skipped_completed"] + segment["new_completed"] == len(groups)}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True,
        choices=("D1_basic", "D3_bars", "D4_D6_status", "D5_factor"))
    parser.add_argument("--stop-after", type=int, help="Bounded interruption/resume demonstration")
    args = parser.parse_args()
    acquire(args.phase, args.stop_after)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)  # SDK 1.1.6 logout is unstable on this Windows runtime.
    sys.stdout.flush()
    os._exit(0)
