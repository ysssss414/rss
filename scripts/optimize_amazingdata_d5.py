"""Measured, checkpointed AmazingData D5 batch acquisition for Stage 1."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import csv
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
from research.amazingdata_batch import (batch_groups, cache_complete, extended_sample,
    factor_quality, normalize_factors, save_json)
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

OLD = ROOT / "artifacts" / "stage1_amazingdata_100_benchmark"
ARTIFACTS = ROOT / "artifacts" / "stage1_amazingdata_acquisition_optimization"
PRIVATE = ROOT / ".local_research_data" / "stage1_amazingdata_acquisition_optimization"
MODES = {"serial20": (20, 1), "batch20_100": (100, 20),
         "batch50_100": (100, 50), "batch100_100": (100, 100)}
MAX_RETRIES = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def read_sample(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def session(adapter: AmazingDataAdapter):
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            return adapter._call_with_retry(lambda provider: provider)


def extended_manifest(adapter: AmazingDataAdapter, fixed: list[dict[str, str]]) -> list[dict[str, str]]:
    path = ARTIFACTS / "sample_universe_500.csv"
    if path.exists():
        sample = read_sample(path)
        if sample[:100] != fixed or len(sample) != 500:
            raise ValueError("Existing extended sample differs from fixed 100")
        return sample
    provider = session(adapter)
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            universe = provider.base.get_hist_code_list(security_type="EXTRA_STOCK_A",
                start_date=20260923, end_date=20260923,
                local_path=str(PRIVATE / "sdk" / "universe") + os.sep)
    market = [code for code in universe if code.endswith((".SH", ".SZ"))]
    expected_market = json.loads((OLD / "benchmark_summary.json").read_text(
        encoding="utf-8"))["full_market_security_count_assumption"]
    if len(set(market)) != expected_market:
        raise ValueError("Historical universe size changed since the fixed benchmark")
    sample = extended_sample(fixed, market)
    for group in batch_groups([row["security_id"] for row in sample[100:]], 80):
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                basic = provider.ad.InfoData().get_stock_basic(group)
        metadata = pd.DataFrame(basic).set_index("MARKET_CODE")
        if set(group) - set(metadata.index):
            raise ValueError("Extended sample listing dates incomplete")
        for row in sample[100:]:
            if row["security_id"] in group:
                row["listing_date"] = str(metadata.loc[row["security_id"], "LISTDATE"])
    if any(not row["listing_date"] for row in sample):
        raise ValueError("Extended sample has missing listing dates")
    write_csv(path, sample, list(sample[0]))
    return sample


def memory_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1048576, 2)
    except ImportError:
        return None


def mode_config(mode: str, batch_size: int | None) -> tuple[int, int]:
    if mode in MODES:
        count, fixed_size = MODES[mode]
        if batch_size is not None and batch_size != fixed_size:
            raise ValueError("Controlled mode has a fixed batch size")
        return count, fixed_size
    if mode in {"sustained500", "resume200"} and batch_size in {20, 50, 100}:
        return (500 if mode == "sustained500" else 200), batch_size
    raise ValueError("Selected mode needs --batch-size 20, 50, or 100")


def acquire(mode: str, batch_size: int | None, resume: bool,
            stop_after_batches: int | None, manifest: Path | None = None) -> None:
    if mode == "materialize":
        if manifest is None or batch_size not in {20, 50, 100}:
            raise ValueError("Materialization needs --manifest and --batch-size 20, 50, or 100")
        sample = read_sample(manifest)
        count, size = len(sample), batch_size
        if not count or len({row["security_id"] for row in sample}) != count or any(
            not row["security_id"].endswith((".SH", ".SZ")) or not row["listing_date"]
            for row in sample):
            raise ValueError("Materialization manifest needs unique SH/SZ IDs and listing dates")
    else:
        count, size = mode_config(mode, batch_size)
    fixed_path = OLD / "sample_universe.csv"
    fixed = read_sample(fixed_path)
    if len(fixed) != 100:
        raise ValueError("Expected original 100-security sample")
    run_dir = PRIVATE / mode
    checkpoint = run_dir / "checkpoint.json"
    if checkpoint.exists() and not resume:
        raise FileExistsError(f"{mode} already has a checkpoint; use --resume")
    if resume and not checkpoint.exists():
        raise FileNotFoundError("No checkpoint to resume")
    run_dir.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
        cache_dir=run_dir / "sdk", retry_count=1, use_numba_compat=True)
    if mode != "materialize":
        sample = extended_manifest(adapter, fixed) if count > 100 else fixed
    selected = sample[:count]
    codes = [row["security_id"] for row in selected]
    listing = {row["security_id"]: row["listing_date"] for row in selected}
    groups = batch_groups(codes, size)
    sample_hash = (digest(manifest) if mode == "materialize" else
                   digest(ARTIFACTS / "sample_universe_500.csv") if count > 100 else digest(fixed_path))
    config = {"mode": mode, "batch_size": size, "security_count": count,
              "sample_sha256": sample_hash, "date_start": "2024-01-01",
              "date_end": "2026-09-23", "source": "AmazingData", "force_remote_fetch": True}
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if resume else {
        "schema": "stage1-amazingdata-d5-batch-checkpoint/1", "config": config,
        "created_at": now(), "records": {}, "segments": []}
    if state["config"] != config:
        raise ValueError("Checkpoint configuration/sample changed")
    if resume and all(cache_complete(state["records"].get(f"batch_{i:03d}", {}), run_dir)
                      for i in range(1, len(groups) + 1)):
        print(json.dumps({"mode": mode, "complete": True, "already_complete": True}), flush=True)
        return
    provider = session(adapter)
    segment = {"started_at": now(), "elapsed_seconds": 0.0, "skipped_completed_batches": 0,
               "remote_request_attempts": 0, "new_completed_batches": 0}
    state["segments"].append(segment)
    segment_start = time.perf_counter()
    save_json(checkpoint, state)
    failures_in_row = 0
    for batch_id, group in enumerate(groups, 1):
        key = f"batch_{batch_id:03d}"
        old = state["records"].get(key, {})
        if cache_complete(old, run_dir):
            segment["skipped_completed_batches"] += 1
            continue
        record = {"batch_id": batch_id, "security_ids": group, "status": "RUNNING",
                  "started_at": old.get("started_at", now()), "attempts": old.get("attempts", []),
                  "session_restart_count": old.get("session_restart_count", 0)}
        state["records"][key] = record
        batch_start = time.perf_counter()
        save_json(checkpoint, state)
        wide = None
        for retry in range(MAX_RETRIES + 1):
            request_start = time.perf_counter()
            try:
                # is_local=False is the SDK's explicit remote-fetch path. Each
                # controlled mode also has its own SDK local_path and session.
                with open(os.devnull, "w", encoding="utf-8") as sink:
                    with redirect_stdout(sink), redirect_stderr(sink):
                        wide = provider.base.get_backward_factor(group,
                            local_path=str(run_dir / "sdk" / "factor") + os.sep,
                            is_local=False)
                record["attempts"].append({"at": now(), "status": "SUCCESS",
                    "elapsed_seconds": round(time.perf_counter() - request_start, 6)})
                segment["remote_request_attempts"] += 1
                break
            except Exception as exc:
                kind = type(exc).__name__
                detail = str(exc).lower()
                record["attempts"].append({"at": now(), "status": "FAILED",
                    "error_type": kind, "timeout": "timeout" in kind.lower() or "timed out" in detail,
                    "rate_limited": ("rate limit" in detail or "too many requests" in detail or
                                     "throttle" in detail or "rate" in kind.lower()),
                    "elapsed_seconds": round(time.perf_counter() - request_start, 6)})
                segment["remote_request_attempts"] += 1
                transient = (isinstance(exc, (ConnectionError, TimeoutError, TypeError)) or
                             record["attempts"][-1]["rate_limited"])
                if not transient or retry == MAX_RETRIES or (kind == "TypeError" and retry > 0):
                    break
                # The prior serial run recovered one SDK-calendar TypeError after
                # a fresh login. Keep normal calls on the existing session.
                if isinstance(exc, (ConnectionError, TimeoutError, TypeError)):
                    adapter._session_provider = None
                    provider = session(adapter)
                    record["session_restart_count"] += 1
                time.sleep(30 * (retry + 1) if record["attempts"][-1]["rate_limited"]
                           else min(2 ** retry, 4))
        if wide is None:
            record["status"] = "FAILED"
            failures_in_row += 1
        else:
            frame = normalize_factors(wide, group, listing)
            quality = factor_quality(frame)
            if quality["duplicate_security_date"] or quality["missing_factor"] or quality["non_positive_factor"]:
                record["status"] = "FAILED"
                record["validation_error"] = quality
                failures_in_row += 1
            else:
                cache = run_dir / f"{key}.csv.gz"
                temporary = run_dir / f"{key}.tmp.gz"
                frame.to_csv(temporary, index=False, compression="gzip")
                temporary.replace(cache)
                record.update(status="COMPLETED", rows=len(frame), quality=quality,
                              cache_file=cache.name, sha256=digest(cache))
                segment["new_completed_batches"] += 1
                failures_in_row = 0
        record["finished_at"] = now()
        record["memory_rss_mb"] = memory_mb()
        record["elapsed_seconds"] = round(old.get("elapsed_seconds", 0) +
                                          time.perf_counter() - batch_start, 6)
        save_json(checkpoint, state)
        print(f"{mode} {key}: {record['status']} {record.get('rows', 0)} rows "
              f"{record['elapsed_seconds']:.2f}s", flush=True)
        if record["status"] == "FAILED" or failures_in_row:
            break
        if stop_after_batches and segment["new_completed_batches"] >= stop_after_batches:
            break
    segment["elapsed_seconds"] = round(time.perf_counter() - segment_start, 6)
    segment["finished_at"] = now()
    save_json(checkpoint, state)
    complete = all(cache_complete(state["records"].get(f"batch_{i:03d}", {}), run_dir)
                   for i in range(1, len(groups) + 1))
    print(json.dumps({"mode": mode, "complete": complete, "segment": segment},
                     ensure_ascii=False), flush=True)
    if not complete and not stop_after_batches:
        raise RuntimeError(f"{mode} incomplete; inspect checkpoint")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True,
        choices=(*MODES, "sustained500", "resume200", "materialize", "finalize"))
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after-batches", type=int)
    args = parser.parse_args()
    if args.mode == "finalize":
        from scripts.summarize_amazingdata_d5 import summarize
        summarize()
    else:
        acquire(args.mode, args.batch_size, args.resume, args.stop_after_batches, args.manifest)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)  # SDK 1.1.6 logout has crashed in this Windows runtime.
    sys.stdout.flush()
    os._exit(0)
