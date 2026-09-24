"""Small helpers for the Stage 1 AmazingData D5 batch acquisition test."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import random

import pandas as pd

from research.amazingdata_benchmark import BOARD_QUOTAS, SEED, board_of


START, END = pd.Timestamp("2024-01-01"), pd.Timestamp("2026-09-23")


def extended_sample(fixed: list[dict[str, str]], universe: list[str]) -> list[dict[str, str]]:
    """Keep the original 100 first, then append four balanced 100-name blocks."""
    if len(fixed) != 100 or len({row["security_id"] for row in fixed}) != 100:
        raise ValueError("Expected the fixed 100-security sample")
    rng = random.Random(SEED)
    existing = {row["security_id"] for row in fixed}
    additions = {}
    for board, quota in BOARD_QUOTAS.items():
        if sum(row["board"] == board for row in fixed) != quota:
            raise ValueError("Fixed sample board counts changed")
        candidates = sorted(code for code in set(universe) - existing if board_of(code) == board)
        additions[board] = rng.sample(candidates, 100)
    result = list(fixed)
    for block in range(4):
        for board in BOARD_QUOTAS:
            for code in additions[board][block * 25:(block + 1) * 25]:
                result.append({"security_id": code, "exchange": code[-2:], "board": board,
                               "listing_date": "", "sample_reason": "deterministic 500 extension"})
    if len(result) != 500 or len({row["security_id"] for row in result}) != 500:
        raise ValueError("Extended sample is not 500 unique securities")
    return result


def batch_groups(codes: list[str], batch_size: int) -> list[list[str]]:
    if batch_size < 1 or len(codes) != len(set(codes)):
        raise ValueError("Batch size or security list invalid")
    return [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]


def normalize_factors(wide: pd.DataFrame, codes: list[str], listing: dict[str, str]) -> pd.DataFrame:
    if not isinstance(wide, pd.DataFrame) or set(codes) - set(wide.columns):
        raise ValueError("Factor response is missing requested securities")
    dates = pd.to_datetime(wide.index, errors="raise")
    mask = (dates >= START) & (dates <= END)
    parts = []
    for code in codes:
        listed = pd.Timestamp(str(listing[code]))
        series = pd.to_numeric(wide.loc[mask, code], errors="coerce")
        series.index = dates[mask]
        series = series.loc[series.index >= listed]
        parts.append(pd.DataFrame({"security_id": code,
                                   "trade_date": series.index.strftime("%Y-%m-%d"),
                                   "factor": series.to_numpy()}))
    return pd.concat(parts, ignore_index=True)


def factor_quality(frame: pd.DataFrame) -> dict[str, int]:
    return {"rows": len(frame),
            "duplicate_security_date": int(frame.duplicated(["security_id", "trade_date"]).sum()),
            "missing_factor": int(frame.factor.isna().sum()),
            "non_positive_factor": int(pd.to_numeric(frame.factor, errors="coerce").le(0).sum())}


def cache_complete(record: dict, directory: Path) -> bool:
    cache = directory / record.get("cache_file", "missing")
    return (record.get("status") == "COMPLETED" and cache.is_file() and
            sha256(cache.read_bytes()).hexdigest() == record.get("sha256"))


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
    temporary.replace(path)


def stage1_eta(sustained_seconds: float, baseline_d5_seconds: float,
               other_endpoints: dict[str, float], market_size: int = 5222) -> dict:
    d5 = sustained_seconds / 500 * market_size
    component = {**other_endpoints, "D5_factor": round(d5, 2)}
    total = sum(component.values())
    return {"full_market_size": market_size,
            "baseline_serial_d5_eta_hours": round(baseline_d5_seconds / 100 * market_size / 3600, 3),
            "optimized_d5_eta_hours": round(d5 / 3600, 3),
            "optimized_d5_buffered_eta_hours": round(d5 * 1.2 / 3600, 3),
            "d1_eta": component["D1_basic"], "d3_eta": component["D3_bars"],
            "d4_d6_eta": component["D4_D6_status"], "d5_eta": component["D5_factor"],
            "stage1_total_eta_hours": round(total / 3600, 3),
            "stage1_total_buffered_eta_hours": round(total * 1.2 / 3600, 3),
            "buffer_assumption": "20% engineering allowance, not measured"}
