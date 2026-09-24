"""Small, deterministic accounting helpers for the Stage 1 SDK benchmark."""

from __future__ import annotations

import json
from hashlib import sha256
from math import ceil
import random
from pathlib import Path


SEED = 20260924
BOARD_QUOTAS = {"SSE Main": 25, "SZSE Main": 25, "STAR": 25, "ChiNext": 25}
SPECIAL = {
    "600518.SH": "historical ST and suspension case",
    "600187.SH": "historical ST/rule-boundary case",
    "603194.SH": "2024 new listing",
    "600519.SH": "long-history control",
    "001331.SZ": "factor case",
    "001391.SZ": "recent listing candidate",
    "301607.SZ": "recent listing candidate",
    "301669.SZ": "recent listing candidate",
    "300750.SZ": "long-history control",
    "688115.SH": "factor case",
    "688981.SH": "STAR control",
    "688750.SH": "recent listing candidate",
}


def board_of(code: str) -> str | None:
    if code.endswith(".SH"):
        if code.startswith(("688", "689")):
            return "STAR"
        if code.startswith(("600", "601", "603", "605")):
            return "SSE Main"
    if code.endswith(".SZ"):
        if code.startswith(("300", "301")):
            return "ChiNext"
        if code.startswith(("000", "001", "002", "003")):
            return "SZSE Main"
    return None


def select_sample(codes: list[str], seed: int = SEED) -> list[dict[str, str]]:
    universe = sorted(set(codes))
    rng = random.Random(seed)
    sample: list[dict[str, str]] = []
    for board, quota in BOARD_QUOTAS.items():
        pool = [code for code in universe if board_of(code) == board]
        forced = [code for code in pool if code in SPECIAL]
        random_codes = rng.sample([code for code in pool if code not in SPECIAL], quota - len(forced))
        for code in sorted(forced + random_codes):
            sample.append({"security_id": code, "exchange": code[-2:], "board": board,
                           "listing_date": "", "sample_reason": SPECIAL.get(code, "board-stratified random")})
    assert len(sample) == 100 and len({row["security_id"] for row in sample}) == 100
    return sample


def save_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def completed(state: dict, key: str, cache_dir: Path) -> bool:
    record = state.get("records", {}).get(key, {})
    path = cache_dir / record.get("cache_file", "missing")
    return (record.get("status") == "SUCCESS" and path.is_file() and
            sha256(path.read_bytes()).hexdigest() == record.get("sha256"))


def endpoint_metrics(records: dict[str, dict], endpoint: str, security_count: int) -> dict:
    subset = [record for record in records.values() if record["endpoint"] == endpoint]
    seconds = sum(record["elapsed_seconds"] for record in subset)
    rows = sum(record.get("rows", 0) for record in subset)
    requests = sum(record.get("attempts", 0) for record in subset)
    failures = sum(len(record.get("attempt_failures", [])) for record in subset)
    return {
        "endpoint_name": endpoint, "security_count": security_count,
        "request_count": requests, "row_count": rows, "wall_clock_seconds": round(seconds, 6),
        "call_mode": "BATCH_SECURITIES" if endpoint == "D1_basic" else "PER_SECURITY",
        "securities_per_request": "5|15|80" if endpoint == "D1_basic" else "1",
        "rows_per_request": round(rows / requests, 3) if requests else None,
        "batch_80_seconds": next((record["elapsed_seconds"] for record in subset
            if record["security_count"] == 80), None) if endpoint == "D1_basic" else None,
        "successful_requests": sum(record["status"] == "SUCCESS" for record in subset),
        "failed_requests": failures, "retry_count": sum(max(0, record.get("attempts", 0) - 1) for record in subset),
        "timeout_count": sum("timeout" in f["type"].lower() for record in subset for f in record.get("attempt_failures", [])),
        "rate_limit_count": sum("rate" in f["type"].lower() or "throttle" in f["type"].lower()
                                for record in subset for f in record.get("attempt_failures", [])),
        "seconds_per_security": round(seconds / security_count, 6) if security_count else None,
        "rows_per_second": round(rows / seconds, 3) if seconds else None,
        "failure_rate": round(failures / requests, 6) if requests else None,
        "retry_rate": round(sum(max(0, record.get("attempts", 0) - 1) for record in subset) / requests, 6) if requests else None,
    }


def full_market_eta(metrics: list[dict], market_count: int,
                    total_wall_seconds: float | None = None) -> dict:
    endpoints = {}
    for row in metrics:
        name = row["endpoint_name"]
        n = row["security_count"]
        if name == "D1_basic":
            # Use the largest measured batch, not a per-security slope for a batched endpoint.
            endpoints[name] = {"linear_seconds": round(ceil(market_count / 80) * row["batch_80_seconds"], 2),
                               "model": "ESTIMATION_ONLY: ceil(market/80) × observed 80-security batch seconds",
                               "batch_size": 80, "batch_count": ceil(market_count / 80)}
        else:
            endpoints[name] = {"linear_seconds": round(row["wall_clock_seconds"] / n * market_count, 2),
                               "model": "PER_SECURITY_LINEAR"}
    endpoint_total = sum(item["linear_seconds"] for item in endpoints.values())
    total = (total_wall_seconds / 100 * market_count
             if total_wall_seconds is not None else endpoint_total)
    return {"full_market_security_count": market_count, "scope": "SH/SZ historical A shares as of 2026-09-23",
            "endpoint_eta": endpoints, "endpoint_sum_seconds": round(endpoint_total, 2),
            "eta_model": ("MEASURED_TOTAL_WALL_CLOCK_LINEAR" if total_wall_seconds is not None
                          else "ENDPOINT_SUM"), "eta_linear_seconds": round(total, 2),
            "eta_buffered_seconds": round(total * 1.2, 2), "buffer_assumption": "20% engineering allowance, not measured"}
