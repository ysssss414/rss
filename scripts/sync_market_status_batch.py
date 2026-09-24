"""Resume D4/D6 status acquisition in proven 20-security SDK batches."""

from __future__ import annotations

import csv
from contextlib import redirect_stderr, redirect_stdout
import json
import logging
import os
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.amazingdata_batch import batch_groups, save_json
from scripts.sync_market_store import (PRIVATE, SAMPLE_500, END, START, AmazingDataAdapter,
    _install_numba_compat, call_with_retry, digest, now, record_ok, session)

PHASE = "D4_D6_status"
REQUIRED = {"MARKET_CODE", "TRADE_DATE", "PRECLOSE", "HIGH_LIMITED", "LOW_LIMITED",
    "PRICE_HIGH_LMT_RATE", "PRICE_LOW_LMT_RATE", "IS_ST_SEC", "IS_SUSP_SEC",
    "IS_WD_SEC", "IS_XR_SEC"}


def main() -> None:
    checkpoint = PRIVATE / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    codes = json.loads((PRIVATE / "universe.json").read_text(encoding="utf-8"))["security_ids"]
    with SAMPLE_500.open(encoding="utf-8", newline="") as stream:
        first_500 = [row["security_id"] for row in csv.DictReader(stream)]
    ordered = list(dict.fromkeys(first_500 + codes))
    if len(ordered) != 5222 or (state["date_start"], state["date_end"]) != (START, END):
        raise ValueError("D4/D6 checkpoint universe/window changed")
    completed = {code for code in ordered if record_ok(state["records"].get(f"{PHASE}:{code}", {}))}
    missing = [code for code in ordered if code not in completed]
    if not missing:
        print(json.dumps({"phase": PHASE, "complete": True, "already_complete": True}))
        return
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
        cache_dir=PRIVATE / "sdk", retry_count=1, use_numba_compat=True)
    provider = session(adapter)
    segment = {"phase": PHASE, "mode": "batch20", "batch_size": 20,
        "started_at": now(), "skipped_completed": len(completed), "new_completed": 0,
        "remote_request_attempts": 0, "elapsed_seconds": 0.0}
    state["segments"].append(segment)
    began = time.perf_counter()
    save_json(checkpoint, state)
    for batch_no, group in enumerate(batch_groups(missing, 20), 1):
        shared = {"attempts": [], "session_restart_count": 0}
        for code in group:
            key = f"{PHASE}:{code}"
            state["records"][key] = {"dataset": PHASE, "batch_id": code,
                "security_ids": [code], "date_start": START, "date_end": END,
                "status": "RUNNING", "started_at": now(), "attempts": [],
                "session_restart_count": 0, "status_batch": batch_no}
        save_json(checkpoint, state)
        try:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with redirect_stdout(sink), redirect_stderr(sink):
                    values, provider = call_with_retry(adapter, provider,
                        lambda p: p.ad.InfoData().get_history_stock_status(group,
                            local_path=str(PRIVATE / "sdk" / "status") + os.sep,
                            is_local=False, begin_date=20240101, end_date=20260923), shared)
            segment["remote_request_attempts"] += len(shared["attempts"])
            if not isinstance(values, dict) or set(values) != set(group):
                raise ValueError("D4/D6 batch response security mapping mismatch")
            staged = []
            for code in group:
                frame = pd.DataFrame(values[code])
                if frame.empty:
                    frame = pd.DataFrame(columns=sorted(REQUIRED))
                if not REQUIRED <= set(frame) or frame.MARKET_CODE.dropna().ne(code).any():
                    raise ValueError(f"D4/D6 batch schema/identity mismatch for {code}")
                path = PRIVATE / "staging" / PHASE / f"{code.replace('.', '_')}.csv.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp.gz")
                frame.to_csv(temporary, index=False, compression="gzip")
                temporary.replace(path)
                staged.append((code, frame, path))
            empty_count = sum(row.get("empty_response", False) for row in state["records"].values()
                              if row["dataset"] == PHASE and row["status"] == "COMPLETED")
            if empty_count + sum(frame.empty for _, frame, _ in staged) > 52:
                raise ValueError("Systemic empty D4/D6 responses")
            for code, frame, path in staged:
                state["records"][f"{PHASE}:{code}"].update(status="COMPLETED",
                    rows=len(frame), cache_file=str(path), sha256=digest(path),
                    attempts=shared["attempts"], session_restart_count=shared["session_restart_count"],
                    empty_response=frame.empty, finished_at=now(), reused=False)
            segment["new_completed"] += len(group)
            segment["elapsed_seconds"] = round(time.perf_counter() - began, 6)
            save_json(checkpoint, state)
            print(f"D4/D6 batch {batch_no}: {len(group)} securities, "
                  f"{sum(len(frame) for _, frame, _ in staged)} rows, "
                  f"{segment['new_completed']} new", flush=True)
        except Exception as exc:
            for code in group:
                record = state["records"][f"{PHASE}:{code}"]
                if record["status"] != "COMPLETED":
                    record.update(status="FAILED", error_type=type(exc).__name__,
                                  error=str(exc)[:300], finished_at=now(),
                                  attempts=shared["attempts"])
            save_json(checkpoint, state)
            raise
    segment["elapsed_seconds"] = round(time.perf_counter() - began, 6)
    segment["finished_at"] = now()
    save_json(checkpoint, state)
    complete = all(record_ok(state["records"].get(f"{PHASE}:{code}", {})) for code in ordered)
    print(json.dumps({"phase": PHASE, "complete": complete, "segment": segment}), flush=True)
    if not complete:
        raise ValueError("D4/D6 acquisition incomplete")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
