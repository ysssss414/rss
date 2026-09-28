"""Offline broad candidate search. Output is never a formal Observation."""

from __future__ import annotations

import csv
import argparse
import hashlib
import json
from bisect import bisect_left
from collections import Counter, deque
from datetime import date
from pathlib import Path

import duckdb

from research.provisional_candidates import (
    CANDIDATE_VERSION, DETECTOR_VERSION, V2_CANDIDATE_VERSION, V3_CANDIDATE_VERSION,
    V2_DETECTOR_VERSION, V3_DETECTOR_VERSION, RsiPrefix,
    candidate_id, candidate_id_v2, candidate_id_v3,
    limit_up_like, limit_up_like_v2, provisional_window,
)
from research.close_limit_up_v2 import evaluate_close_limit_up_v2
from research.close_limit_up_v3 import evaluate_close_limit_up_v3


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
OUT = ROOT / "artifacts/stage1_targeted_regime"
MANIFEST_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"


def _parquet(dataset: str) -> str:
    suffix = "**/*.parquet" if dataset in {"daily_bars", "daily_status", "adjustment_factor"} else "part.parquet"
    return str(V1 / dataset / suffix).replace("\\", "/")


def _phase(listed: date, day: date, exchange: str, sessions: tuple[date, ...],
           prefix: dict[str, tuple[date, ...]]) -> str:
    if listed > day:
        raise ValueError("Pre-listing daily observation")
    if listed < sessions[0]:
        earlier = prefix[exchange]
        if listed < earlier[0]:
            return "NORMAL_LISTED"
        if listed not in earlier:
            raise ValueError("Missing qualified pre-window listing session")
        number = sum(listed <= value <= day for value in earlier) + bisect_left(sessions, day) + 1
    else:
        if listed not in sessions:
            raise ValueError("Listing date absent from V1 calendar")
        number = bisect_left(sessions, day) - bisect_left(sessions, listed) + 1
    return f"IPO_DAY_{number}" if number <= 5 else "NORMAL_LISTED"


def main(*, v2: bool = False, v3: bool = False) -> None:
    if v2 and v3:
        raise ValueError("Select only one provisional version")
    manifest_hash = hashlib.sha256((V1 / "metadata/manifest.json").read_bytes()).hexdigest()
    if manifest_hash != MANIFEST_SHA:
        raise ValueError("REAL_RESEARCH_SNAPSHOT_V1 changed")
    db = duckdb.connect()
    db.execute("PRAGMA disable_progress_bar")
    master, status = _parquet("security_master"), _parquet("daily_status")
    bars, factor = _parquet("daily_bars"), _parquet("adjustment_factor")
    calendar = tuple(row[0] for row in db.execute(
        f"SELECT trade_date FROM read_parquet('{_parquet('trading_calendar')}') "
        "WHERE is_trading_day ORDER BY trade_date"
    ).fetchall())
    pre_path = ROOT / "artifacts/stage1_limit_regime/pre_window_calendar_context_v1.parquet"
    pre_receipt = json.loads((pre_path.parent / "pre_window_calendar_context_receipt.json").read_text())
    if hashlib.sha256(pre_path.read_bytes()).hexdigest() != pre_receipt["artifact_sha256"]:
        raise ValueError("Qualified pre-window calendar context changed")
    prefix = {exchange: tuple(row[0] for row in db.execute(
        f"SELECT trade_date FROM read_parquet('{str(pre_path).replace(chr(92), '/')}') "
        "WHERE exchange = ? AND is_trading_day ORDER BY trade_date", [exchange]
    ).fetchall()) for exchange in ("SH", "SZ")}
    if calendar[0] != date(2024, 1, 2) or not all(prefix.values()):
        raise ValueError("Unexpected exchange calendar boundary")

    checks = {}
    for label, path in (("master", master), ("status", status), ("bars", bars), ("factor", factor)):
        key = "security_id" if label == "master" else "security_id, trade_date"
        checks[f"{label}_duplicate_keys"] = db.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM read_parquet('{path}') "
            f"GROUP BY {key} HAVING count(*) > 1)"
        ).fetchone()[0]
    if any(checks.values()):
        raise ValueError(f"V1 duplicate grain: {checks}")

    cursor = db.execute(f"""
        SELECT m.security_id, m.exchange, m.board, m.listing_date,
               s.trade_date, s.preclose, s.high_limited, s.is_susp_sec,
               b.close, f.factor, b.high, s.is_xr_sec, s.is_wd_sec
        FROM read_parquet('{status}') s
        JOIN read_parquet('{master}') m USING (security_id)
        LEFT JOIN read_parquet('{bars}') b USING (security_id, trade_date)
        LEFT JOIN read_parquet('{factor}') f USING (security_id, trade_date)
        WHERE m.board IN ('SSE Main', 'SZSE Main')
        ORDER BY m.security_id, s.trade_date
    """)
    candidates: list[tuple] = []
    dependencies: dict[tuple[str, str], set[str]] = {}
    code = None
    previous_close = None
    previous_factor = None
    rsi = RsiPrefix()
    window: deque[tuple[str, bool, str, str]] = deque(maxlen=5)
    quality = {"scoped_status_rows": 0, "tradable_missing_bar": 0,
               "tradable_missing_factor": 0, "suspended_rows": 0,
               "four_of_five": 0, "five_of_five": 0,
               "rsi_warmup_unverified": 0}
    if v2:
        quality["v2_ordinary_hit_not_detected"] = 0
    if v3:
        quality["v3_ordinary_hit_not_detected"] = 0
    v3_daily = Counter()
    v3_daily_fingerprint = hashlib.sha256()
    observation_count = 0
    for batch in iter(lambda: cursor.fetchmany(10000), []):
        for (security_id, exchange, board, listed, day, preclose, vendor_upper,
             suspended, close, adjustment, high, is_xr, is_wd) in batch:
            quality["scoped_status_rows"] += 1
            if security_id != code:
                code, previous_close, previous_factor, rsi = security_id, None, None, RsiPrefix()
                window, observation_count = deque(maxlen=5), 0
            if suspended is True:
                quality["suspended_rows"] += 1
                continue
            if close is None:
                quality["tradable_missing_bar"] += 1
                rsi.invalid = True
                window.clear()
                continue
            if adjustment is None:
                quality["tradable_missing_factor"] += 1
            observation_count += 1
            phase = _phase(listed, day, exchange, calendar, prefix)
            if v2 or v3:
                special = (adjustment is None or previous_factor is None
                           or adjustment != previous_factor or is_xr is True or is_wd is True
                           or (v3 and (is_xr is None or is_wd is None)))
                hit, methods = limit_up_like_v2(
                    close=close, previous_raw_close=previous_close,
                    reference_special=special,
                )
                # CLEARED is a counterfactual detector-recall proof only; it
                # never supplies actual exception evidence to formal events.
                if v3:
                    ordinary = evaluate_close_limit_up_v3(
                        board=board, listing_phase=phase, close=close, high=high,
                        previous_raw_close=previous_close, factor=adjustment,
                        previous_factor=previous_factor, is_xr_sec=is_xr,
                        is_wd_sec=is_wd, special_exception="CLEARED",
                    )
                    v3_daily[ordinary.reference_day.classification] += 1
                    v3_daily[f"EVENT_{ordinary.status}"] += 1
                    v3_daily_fingerprint.update(
                        f"{security_id}|{day.isoformat()}|{ordinary.reference_day.classification}|"
                        f"{ordinary.status}|{ordinary.canonical_limit_up_cents}|{ordinary.reason}\n"
                        .encode("utf-8")
                    )
                    if ordinary.status == "TRUE" and not hit:
                        quality["v3_ordinary_hit_not_detected"] += 1
                else:
                    ordinary = evaluate_close_limit_up_v2(
                        board=board, listing_phase=phase, close=close, high=high,
                        previous_raw_close=previous_close, factor=adjustment,
                        previous_factor=previous_factor, is_xr_sec=is_xr,
                        special_exception="CLEARED",
                    )
                    if ordinary.status == "TRUE" and not hit:
                        quality["v2_ordinary_hit_not_detected"] += 1
            else:
                hit, methods = limit_up_like(
                    close=close, vendor_upper=vendor_upper,
                    vendor_preclose=preclose, previous_raw_close=previous_close,
                )
            previous_close = close
            previous_factor = adjustment
            value = rsi.update(close, adjustment)
            window.append((day.isoformat(), hit, "|".join(methods), phase))
            if len(window) < 5 or any(row[3] != "NORMAL_LISTED" for row in window):
                continue
            flags = [row[1] for row in window]
            if sum(flags) < 4:
                continue
            # Existing warm-up policy requires 120 observations for an old
            # listing whose true pre-2024 prefix is absent from V1. Retain
            # unverified RSI windows for recall, never formal admission.
            warmup_unverified = listed < calendar[0] and observation_count < 120
            if not warmup_unverified and not provisional_window(flags, value):
                continue
            if warmup_unverified:
                quality["rsi_warmup_unverified"] += 1
            count = sum(flags)
            quality["four_of_five" if count == 4 else "five_of_five"] += 1
            trigger = day.isoformat()
            identity = (candidate_id_v3(security_id, trigger) if v3 else
                        candidate_id_v2(security_id, trigger) if v2 else
                        candidate_id(security_id, trigger))
            dates = [row[0] for row in window]
            candidates.append((
                identity, security_id, trigger, json.dumps(dates),
                json.dumps(flags), count, str(value) if value is not None else None,
                "UNVERIFIED_WARMUP" if warmup_unverified else "READY_PROVISIONAL",
                board, "NORMAL_LISTED", "UNVERIFIED", "PENDING", "REAL_RESEARCH_SNAPSHOT_V1",
                V3_CANDIDATE_VERSION if v3 else V2_CANDIDATE_VERSION if v2 else CANDIDATE_VERSION,
                V3_DETECTOR_VERSION if v3 else V2_DETECTOR_VERSION if v2 else DETECTOR_VERSION,
                json.dumps([row[2].split("|") if row[2] else [] for row in window]),
            ))
            for dependency_day in dates:
                dependencies.setdefault((security_id, dependency_day), set()).add(identity)

    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "v3" if v3 else "v2" if v2 else "v1"
    output = OUT / f"provisional_observation_candidate_{suffix}.parquet"
    db.execute("""CREATE TABLE provisional (
        candidate_id VARCHAR, security_id VARCHAR, trigger_date DATE,
        five_observation_dates VARCHAR, limit_up_like_flags VARCHAR,
        limit_up_like_count INTEGER, rsi14_provisional VARCHAR, rsi_filter_status VARCHAR,
        board VARCHAR, listing_phase VARCHAR, regime_status VARCHAR,
        qualification_status VARCHAR, snapshot_id VARCHAR,
        strategy_contract_version VARCHAR, detector_version VARCHAR,
        discovery_methods VARCHAR
    )""")
    if candidates:
        db.executemany("INSERT INTO provisional VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", candidates)
    db.execute(f"COPY (SELECT * FROM provisional ORDER BY security_id, trigger_date) "
               f"TO '{str(output).replace(chr(92), '/')}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
    dependency_path = OUT / (f"targeted_regime_dependency_inventory_{suffix}.csv" if v2 or v3
                             else "targeted_regime_dependency_inventory.csv")
    with dependency_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("security_id", "trade_date", "dependency_kind", "candidate_count", "candidate_ids"))
        for (security_id, day), ids in sorted(dependencies.items()):
            writer.writerow((security_id, day, "OBSERVATION_FIVE_DAY_WINDOW", len(ids), "|".join(sorted(ids))))
    receipt = {
        "schema": f"provisional-observation-candidate-inventory/{3 if v3 else 2 if v2 else 1}",
        "status": "REGIME_UNVERIFIED_NOT_FORMAL_OBSERVATION",
        "v1_manifest_sha256": manifest_hash,
        "pre_window_context_sha256": pre_receipt["artifact_sha256"],
        "candidate_count": len(candidates),
        "unique_securities": len({row[1] for row in candidates}),
        "first_trigger_date": min((row[2] for row in candidates), default=None),
        "last_trigger_date": max((row[2] for row in candidates), default=None),
        "dependency_security_dates": len(dependencies),
        "detector_version": V3_DETECTOR_VERSION if v3 else V2_DETECTOR_VERSION if v2 else DETECTOR_VERSION,
        "detector_contract": ("previous valid raw close move >=5% OR special-reference review; vendor absolute price not used; discovery only"
                              if v2 or v3 else "raw close matches vendor upper OR raw close rises >=5% versus vendor preclose OR previous raw close; all are discovery-only"),
        "rsi_basis": "raw close times V1 PIT-qualified D5 factor; common T anchor cancels in RSI ratio",
        "candidate_parquet_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "dependency_inventory_sha256": hashlib.sha256(dependency_path.read_bytes()).hexdigest(),
        "quality": {**checks, **quality},
    }
    if v3:
        receipt["v3_counterfactual_ordinary_daily_classification"] = dict(sorted(v3_daily.items()))
        receipt["v3_daily_classification_semantic_sha256"] = v3_daily_fingerprint.hexdigest()
    if v2 and quality["v2_ordinary_hit_not_detected"]:
        raise ValueError("Provisional detector missed ordinary V2 events")
    if v3 and quality["v3_ordinary_hit_not_detected"]:
        raise ValueError("Provisional detector missed ordinary V3 events")
    receipt_name = (f"provisional_candidate_inventory_{suffix}.json" if v2 or v3
                    else "provisional_candidate_inventory.json")
    (OUT / receipt_name).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: receipt[key] for key in (
        "candidate_count", "unique_securities", "dependency_security_dates", "quality"
    )}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--v2", action="store_true")
    mode.add_argument("--v3", action="store_true")
    arguments = parser.parse_args()
    main(v2=arguments.v2, v3=arguments.v3)
