"""Offline reduction and cross-checks for the live D5 acquisition runs."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.amazingdata_batch import batch_groups, cache_complete, factor_quality, save_json, stage1_eta

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "artifacts" / "stage1_amazingdata_100_benchmark"
OLD_PRIVATE = ROOT / ".local_research_data" / "stage1_amazingdata_benchmark"
ARTIFACTS = ROOT / "artifacts" / "stage1_amazingdata_acquisition_optimization"
PRIVATE = ROOT / ".local_research_data" / "stage1_amazingdata_acquisition_optimization"
MODES = ("serial20", "batch20_100", "batch50_100", "batch100_100", "sustained500", "resume200")


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_state(mode: str) -> dict:
    path = PRIVATE / mode / "checkpoint.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    config = state["config"]
    sample_path = (ARTIFACTS / "sample_universe_500.csv" if config["security_count"] > 100
                   else OLD / "sample_universe.csv")
    if digest(sample_path) != config["sample_sha256"]:
        raise ValueError(f"{mode} sample changed after acquisition")
    groups = batch_groups(list(pd.read_csv(sample_path).security_id[:config["security_count"]]),
                          config["batch_size"])
    if len(state["records"]) != len(groups):
        raise ValueError(f"Incomplete {mode} checkpoint")
    for number, codes in enumerate(groups, 1):
        record = state["records"][f"batch_{number:03d}"]
        if record["security_ids"] != codes or not cache_complete(record, path.parent):
            raise ValueError(f"Incomplete/invalid {mode} batch {number}")
    return state


def mode_metrics(state: dict) -> dict:
    records = list(state["records"].values())
    config = state["config"]
    seconds = sum(segment["elapsed_seconds"] for segment in state["segments"])
    attempts = [attempt for record in records for attempt in record["attempts"]]
    failures = [attempt for attempt in attempts if attempt["status"] == "FAILED"]
    rows = sum(record["rows"] for record in records)
    return {"mode": config["mode"], "batch_size": config["batch_size"],
            "security_count": config["security_count"], "batch_count": len(records),
            "request_attempts": len(attempts), "wall_clock_seconds": round(seconds, 6),
            "sec_per_security": round(seconds / config["security_count"], 6),
            "rows": rows, "rows_per_sec": round(rows / seconds, 3),
            "successful_batches": sum(record["status"] == "COMPLETED" for record in records),
            "failed_batches": sum(record["status"] == "FAILED" for record in records),
            "failures": len(failures), "retries": sum(max(0, len(record["attempts"]) - 1)
                                         for record in records),
            "timeout_count": sum(attempt.get("timeout", "timeout" in
                                 attempt.get("error_type", "").lower()) for attempt in failures),
            "rate_limit_count": sum(attempt.get("rate_limited", "rate" in
                                    attempt.get("error_type", "").lower()) for attempt in failures),
            "session_restart_count": sum(record["session_restart_count"] for record in records)}


def read_mode(mode: str, state: dict) -> pd.DataFrame:
    directory = PRIVATE / mode
    return pd.concat([pd.read_csv(directory / record["cache_file"], float_precision="round_trip")
                      for record in state["records"].values()], ignore_index=True)


def read_old_serial(codes: list[str]) -> pd.DataFrame:
    manifest = json.loads((OLD / "benchmark_receipt.json").read_text(encoding="utf-8"))["cache_manifest"]
    parts = []
    for code in codes:
        receipt = manifest[f"D5_factor:{code}"]
        path = OLD_PRIVATE / receipt["cache_file"]
        if digest(path) != receipt["sha256"]:
            raise ValueError("Old serial factor cache hash changed")
        frame = pd.read_csv(path, index_col=0, float_precision="round_trip")
        parts.append(pd.DataFrame({"security_id": code,
            "trade_date": pd.to_datetime(frame.index).strftime("%Y-%m-%d"),
            "factor": pd.to_numeric(frame[code]).to_numpy()}))
    return pd.concat(parts, ignore_index=True)


def compare_factor(left: pd.DataFrame, right: pd.DataFrame, name: str) -> dict:
    keys = ["security_id", "trade_date"]
    if left.duplicated(keys).any() or right.duplicated(keys).any():
        raise ValueError("Duplicate factor keys prevent comparison")
    merged = left.merge(right, on=keys, how="outer", indicator=True,
                        suffixes=("_serial", "_batch"), validate="one_to_one")
    matched = merged.loc[merged._merge.eq("both")]
    differences = np.abs(matched.factor_serial.to_numpy() - matched.factor_batch.to_numpy())
    values_equal = np.isclose(matched.factor_serial, matched.factor_batch,
                              atol=0, rtol=0, equal_nan=True)
    return {"comparison": name, "rows_serial": len(left), "rows_batch": len(right),
            "matching_keys": len(matched),
            "missing_in_batch": int(merged._merge.eq("left_only").sum()),
            "extra_in_batch": int(merged._merge.eq("right_only").sum()),
            "value_mismatch": int((~values_equal).sum()),
            "maximum_absolute_difference": float(differences.max()) if len(differences) else 0.0,
            "numeric_comparison": "exact float64 after round-trip CSV parsing; row order ignored"}


def summarize() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    states = {mode: load_state(mode) for mode in MODES}
    metrics = {mode: mode_metrics(state) for mode, state in states.items()}
    chosen = metrics["sustained500"]["batch_size"]
    if metrics["resume200"]["batch_size"] != chosen:
        raise ValueError("Resume demonstration must use selected sustained batch size")
    old_summary = json.loads((OLD / "benchmark_summary.json").read_text(encoding="utf-8"))
    old_eta = json.loads((OLD / "full_market_eta.json").read_text(encoding="utf-8"))
    if old_summary["full_market_security_count_assumption"] != 5222:
        raise ValueError("Historical universe denominator changed")
    serial = metrics["serial20"]["sec_per_security"]
    historical_serial100 = old_summary["d5_seconds"] / 100
    batch20 = metrics["batch20_100"]["sec_per_security"]
    comparison = [{"mode": "historical_serial100", "batch_size": 1, "security_count": 100,
                   "batch_count": 100, "request_attempts": 101,
                   "wall_clock_seconds": old_summary["d5_seconds"],
                   "sec_per_security": round(old_summary["d5_seconds"] / 100, 6),
                   "rows": 62239, "rows_per_sec": round(62239 / old_summary["d5_seconds"], 3),
                   "successful_batches": 100, "failed_batches": 0,
                   "failures": 1, "retries": 1, "timeout_count": 0,
                   "rate_limit_count": 0, "session_restart_count": 1,
                   "speedup_vs_serial": 1.0, "speedup_vs_batch20": None,
                   "serial_reference": "historical_serial100_same_100"}]
    for mode in MODES:
        item = dict(metrics[mode])
        if mode == "serial20":
            item["speedup_vs_serial"] = 1.0
            item["serial_reference"] = "fresh_serial20_self"
        elif item["security_count"] == 100:
            item["speedup_vs_serial"] = round(historical_serial100 / item["sec_per_security"], 3)
            item["serial_reference"] = "historical_serial100_same_100"
        else:
            item["speedup_vs_serial"] = None
            item["serial_reference"] = "not_same_sample"
        item["speedup_vs_batch20"] = (round(batch20 / item["sec_per_security"], 3)
                                      if item["security_count"] == 100 else None)
        comparison.append(item)
    fields = list(comparison[0])
    write_csv(ARTIFACTS / "batch_size_comparison.csv", comparison, fields)

    all_batches = []
    for mode, state in states.items():
        for record in state["records"].values():
            all_batches.append({"mode": mode, "batch_id": record["batch_id"],
                "batch_size": len(record["security_ids"]), "started_at": record["started_at"],
                "finished_at": record["finished_at"], "elapsed_seconds": record["elapsed_seconds"],
                "rows": record["rows"], "attempts": len(record["attempts"]),
                "status": record["status"],
                "error_type": next((a["error_type"] for a in record["attempts"][::-1]
                                    if a["status"] == "FAILED"), ""),
                "memory_rss_mb": record.get("memory_rss_mb")})
    write_csv(ARTIFACTS / "batch_latency.csv", all_batches, list(all_batches[0]))

    sustained_records = list(states["sustained500"]["records"].values())
    first20_batch_seconds = states["batch20_100"]["records"]["batch_001"]["elapsed_seconds"]
    blocks = []
    batches_per_block = 100 // chosen
    for block in range(5):
        subset = sustained_records[block * batches_per_block:(block + 1) * batches_per_block]
        seconds = sum(record["elapsed_seconds"] for record in subset)
        rows = sum(record["rows"] for record in subset)
        failures = sum(a["status"] == "FAILED" for record in subset for a in record["attempts"])
        blocks.append({"segment": f"{block * 100 + 1}-{(block + 1) * 100}",
            "wall_clock_seconds": round(seconds, 6), "sec_per_security": round(seconds / 100, 6),
            "rows": rows, "rows_per_sec": round(rows / seconds, 3),
            "retries": sum(len(record["attempts"]) - 1 for record in subset),
            "errors": failures,
            "memory_rss_end_mb": subset[-1].get("memory_rss_mb")})
    write_csv(ARTIFACTS / "sustained_500_metrics.csv", blocks, list(blocks[0]))
    degradation = round((1 - blocks[0]["sec_per_security"] /
                         blocks[-1]["sec_per_security"]) * 100, 3)

    fixed_codes = list(pd.read_csv(OLD / "sample_universe.csv").security_id)
    frames = {mode: read_mode(mode, states[mode]) for mode in MODES}
    old_serial = read_old_serial(fixed_codes)
    equivalence = [compare_factor(frames["serial20"],
        frames["batch20_100"].loc[frames["batch20_100"].security_id.isin(fixed_codes[:20])],
        "fresh_serial20_vs_batch20_first20")]
    for mode in ("batch20_100", "batch50_100", "batch100_100"):
        equivalence.append(compare_factor(old_serial, frames[mode],
                                          f"historical_serial100_vs_{mode}"))
    for mode in ("batch50_100", "batch100_100"):
        equivalence.append(compare_factor(frames["batch20_100"], frames[mode],
                                          f"batch20_100_vs_{mode}"))
    equivalence.append(compare_factor(
        frames["sustained500"].loc[frames["sustained500"].security_id.isin(
            set(pd.read_csv(ARTIFACTS / "sample_universe_500.csv").security_id[:200]))],
        frames["resume200"], "sustained_first200_vs_resumed200"))
    save_json(ARTIFACTS / "data_equivalence_summary.json", {"comparisons": equivalence})

    resume_state = states["resume200"]
    resume_frame = frames["resume200"]
    extended_codes = list(pd.read_csv(ARTIFACTS / "sample_universe_500.csv").security_id)
    if len(resume_state["segments"]) != 2:
        raise ValueError("Resume demonstration needs stop and restart segments")
    first_segment = resume_state["segments"][0]
    initial_completed = first_segment["new_completed_batches"]
    previously_completed_attempts = sum(len(resume_state["records"][f"batch_{i:03d}"]["attempts"])
                                        for i in range(1, initial_completed + 1))
    resume_receipt = {"batch_size": chosen, "completed_security_count": resume_frame.security_id.nunique(),
        "missing_securities": sorted(set(extended_codes[:200]) - set(resume_frame.security_id)),
        "duplicate_output_rows": int(resume_frame.duplicated(["security_id", "trade_date"]).sum()),
        "first_segment_new_completed_batches": initial_completed,
        "resumed_skipped_completed_batches": resume_state["segments"][1]["skipped_completed_batches"],
        "resumed_remote_request_attempts": resume_state["segments"][1]["remote_request_attempts"],
        "completed_batch_duplicate_remote_acquisitions":
            previously_completed_attempts - first_segment["remote_request_attempts"],
        "segment_wall_clock_seconds": [s["elapsed_seconds"] for s in resume_state["segments"]]}
    if resume_receipt["completed_security_count"] != 200 or resume_receipt["missing_securities"] or \
       resume_receipt["duplicate_output_rows"] or \
       resume_receipt["completed_batch_duplicate_remote_acquisitions"] != 0 or \
       resume_receipt["resumed_skipped_completed_batches"] != initial_completed:
        raise ValueError("Resume demonstration incomplete")
    save_json(ARTIFACTS / "resume_test_receipt.json", resume_receipt)

    other = {name: old_eta["endpoint_eta"][name]["linear_seconds"] for name in
             ("D1_basic", "D3_bars", "D4_D6_status")}
    eta = stage1_eta(metrics["sustained500"]["wall_clock_seconds"],
        old_summary["d5_seconds"], other)
    eta["recommended_batch_size"] = chosen
    eta["model"] = "500-security active D5 wall-clock linear, plus prior measured D1/D3/D4_D6 endpoint models"
    save_json(ARTIFACTS / "full_market_eta.json", eta)
    quality = factor_quality(frames["sustained500"])
    latencies = pd.Series([record["elapsed_seconds"] for record in sustained_records])
    receipt = {"source": "AmazingData", "sdk_version": "1.1.6", "date_start": "2024-01-01",
        "date_end": "2026-09-23", "sample_100_sha256": digest(OLD / "sample_universe.csv"),
        "sample_500_sha256": digest(ARTIFACTS / "sample_universe_500.csv"),
        "selected_batch_size": chosen, "mode_metrics": metrics,
        "same20_serial_vs_batch20_speedup": round(metrics["serial20"]["wall_clock_seconds"] /
            first20_batch_seconds, 3),
        "same20_batch20_wall_clock_seconds": first20_batch_seconds,
        "same100_historical_serial_vs_batch100_speedup": round(
            old_summary["d5_seconds"] / metrics["batch100_100"]["wall_clock_seconds"], 3),
        "cross_sample_normalized_serial20_vs_sustained500_indicative": round(
            serial / metrics["sustained500"]["sec_per_security"], 3),
        "sustained_quality": quality, "throughput_degradation_pct": degradation,
        "sustained_latency_seconds": {quantile: round(float(latencies.quantile(p)), 6)
            for quantile, p in (("median", .5), ("p75", .75), ("p90", .9), ("p95", .95))},
        "sustained_latency_max_seconds": round(float(latencies.max()), 6),
        "memory_rss_first_batch_mb": sustained_records[0].get("memory_rss_mb"),
        "memory_rss_last_batch_mb": sustained_records[-1].get("memory_rss_mb"),
        "data_equivalence": equivalence,
        "cache_manifest": {mode: {key: {"sha256": record["sha256"],
            "cache_file": record["cache_file"], "rows": record["rows"],
            "completed_at": record["finished_at"]}
            for key, record in state["records"].items()} for mode, state in states.items()}}
    save_json(ARTIFACTS / "optimization_receipt.json", receipt)
    save_json(ARTIFACTS / "benchmark_plan.json", {"fixed_seed": 20260924,
        "fixed_sample_count": 100, "extended_sample_count": 500,
        "board_quota_fixed": 25, "board_quota_extended": 125,
        "date_start": "2024-01-01", "date_end": "2026-09-23",
        "modes": list(MODES), "selected_sustained_batch_size": chosen,
        "max_retries_per_batch": 3, "force_remote_fetch": True,
        "timing_scope": "SDK request, response/parsing, normalization, cache and checkpoint writes; excludes login, sample setup, manual idle time, tests and Git",
        "comparison_scope": "100-name batches versus same-100 historical serial; fresh serial20 versus first 20-name batch; cross-sample 500-name speedup is indicative only"})
    print(json.dumps({"metrics": metrics, "blocks": blocks, "eta": eta,
                      "quality": quality, "equivalence": equivalence,
                      "resume": resume_receipt}, ensure_ascii=False), flush=True)
