"""Serial, resumable AmazingData 100-security throughput baseline."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import date, datetime, timezone
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

from research.amazingdata_benchmark import (SEED, completed, endpoint_metrics,
    full_market_eta, save_checkpoint, select_sample)
from research.data.amazingdata import checked_sdk_bars
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

START, END = date(2024, 1, 1), date(2026, 9, 23)
ARTIFACTS = ROOT / "artifacts" / "stage1_amazingdata_100_benchmark"
PRIVATE = ROOT / ".local_research_data" / "stage1_amazingdata_benchmark"
STATUS_FIELDS = ("HIGH_LIMITED", "LOW_LIMITED", "IS_ST_SEC", "IS_SUSP_SEC")
PHASES = (("A", 0, 5), ("B", 5, 20), ("C", 20, 100))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def quality(frame: pd.DataFrame, endpoint: str) -> dict:
    q = {"duplicate_rows": int((frame.reset_index() if endpoint == "D5_factor" else frame)
                               .duplicated().sum()), "null_key_rows": 0}
    if endpoint == "D1_basic":
        q["null_key_rows"] = int(frame.MARKET_CODE.isna().sum())
        q["duplicate_security"] = int(frame.MARKET_CODE.duplicated().sum())
    elif endpoint == "D3_bars":
        q["null_key_rows"] = int(frame[["code", "date"]].isna().any(axis=1).sum())
        q["duplicate_security_date"] = int(frame.duplicated(["code", "date"]).sum())
        p = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        q["invalid_ohlc"] = int((p.isna().any(axis=1) | (p.le(0)).any(axis=1) |
            (p.high < p.low) | (p.high < p[["open", "close"]].max(axis=1)) |
            (p.low > p[["open", "close"]].min(axis=1))).sum())
        q["negative_volume"] = int(pd.to_numeric(frame.volume, errors="coerce").lt(0).sum())
        q["negative_amount"] = int(pd.to_numeric(frame.amount, errors="coerce").lt(0).sum())
    elif endpoint == "D4_D6_status":
        dates = frame.TRADE_DATE if "TRADE_DATE" in frame else pd.Series(dtype="object")
        q["null_key_rows"] = int(frame[["MARKET_CODE", "TRADE_DATE"]].isna().any(axis=1).sum())
        q["duplicate_security_date"] = int(dates.duplicated().sum())
        for field in STATUS_FIELDS:
            q[f"missing_{field}"] = int(frame[field].isna().sum()) if field in frame else len(frame)
    elif endpoint == "D5_factor":
        q["null_key_rows"] = int(pd.Index(frame.index).isna().sum())
        q["duplicate_factor_date"] = int(frame.index.duplicated().sum())
        values = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
        q["missing_factor"] = int(values.isna().sum())
        q["non_positive_factor"] = int(values.le(0).sum())
    return q


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through-phase", choices=("A", "B", "C"), default="C")
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    state_path = PRIVATE / "checkpoint.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "schema": "amazingdata-benchmark-checkpoint/1", "source": "AmazingData",
        "benchmark_version": "1", "date_start": START.isoformat(), "date_end": END.isoformat(),
        "benchmark_started_at": now(), "records": {}, "segments": []}
    sample_path = ARTIFACTS / "sample_universe.csv"
    if sample_path.exists():
        with sample_path.open(encoding="utf-8", newline="") as stream:
            existing_sample = list(csv.DictReader(stream))
        all_cached = (len(existing_sample) == 100 and
            all(completed(state, f"D1_basic:phase_{phase}", PRIVATE) for phase, _, _ in PHASES) and
            all(completed(state, f"{endpoint}:{row['security_id']}", PRIVATE)
                for row in existing_sample for endpoint in ("D3_bars", "D4_D6_status", "D5_factor")))
    else:
        all_cached = False
    segment = {"started_at": now(), "elapsed_seconds": 0.0}
    if not all_cached:
        state["segments"].append(segment)
    segment_start = time.perf_counter()

    def checkpoint() -> None:
        segment["elapsed_seconds"] = round(time.perf_counter() - segment_start, 6)
        state["last_checkpoint_at"] = now()
        save_checkpoint(state_path, state)

    if all_cached:
        provider, info = None, None
    else:
        logging.disable(logging.CRITICAL)
        _install_numba_compat()
        import AmazingData as ad

        adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
            cache_dir=PRIVATE / "sdk", retry_count=1, use_numba_compat=True)
        with open(os.devnull, "w", encoding="utf-8") as null_stream:
            with redirect_stdout(null_stream), redirect_stderr(null_stream):
                provider = adapter._call_with_retry(lambda session: session)
        info = ad.InfoData()

    def call(key: str, endpoint: str, codes: list[str], operation) -> pd.DataFrame | None:
        if completed(state, key, PRIVATE):
            cached = pd.read_csv(PRIVATE / state["records"][key]["cache_file"], compression="gzip",
                index_col=0 if endpoint == "D5_factor" else None)
            if endpoint == "D5_factor":
                listed = pd.Timestamp(sample_by_code[codes[0]]["listing_date"])
                if len(cached) and pd.to_datetime(cached.index).min() < listed:
                    cached = cached.loc[pd.to_datetime(cached.index) >= listed]
                    path = PRIVATE / state["records"][key]["cache_file"]
                    temporary = path.with_suffix(".tmp")
                    cached.to_csv(temporary, compression="gzip", index=True)
                    temporary.replace(path)
                    state["records"][key].update(rows=len(cached), sha256=digest(path),
                        quality=quality(cached, endpoint))
                    checkpoint()
            current_quality = quality(cached, endpoint)
            if state["records"][key].get("quality") != current_quality:
                state["records"][key]["quality"] = current_quality
                checkpoint()
            return cached
        old = state["records"].get(key, {})
        failures = old.get("attempt_failures", [])
        attempts = old.get("attempts", 0)
        started_at = now()
        elapsed = old.get("elapsed_seconds", 0.0)
        result = None
        for attempt in range(1, 4):
            t0 = time.perf_counter()
            try:
                with open(os.devnull, "w", encoding="utf-8") as null_stream:
                    with redirect_stdout(null_stream), redirect_stderr(null_stream):
                        result = operation()
                elapsed += time.perf_counter() - t0
                attempts += 1
                break
            except Exception as exc:
                elapsed += time.perf_counter() - t0
                attempts += 1
                failures.append({"type": type(exc).__name__, "attempt": attempts, "at": now(),
                    "endpoint": endpoint, "security_id": codes[0] if len(codes) == 1 else "BATCH"})
                transient = isinstance(exc, (ConnectionError, TimeoutError)) or "rate" in type(exc).__name__.lower()
                if not transient or attempt == 3:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
        record = {"endpoint": endpoint, "security_ids": codes, "security_count": len(codes),
            "status": "SUCCESS" if result is not None else "FAILED", "started_at": started_at,
            "finished_at": now(), "elapsed_seconds": round(elapsed, 6), "attempts": attempts,
            "attempt_failures": failures, "rows": 0, "source": "AmazingData",
            "benchmark_version": "1", "date_start": START.isoformat(), "date_end": END.isoformat()}
        if result is not None:
            if endpoint == "D4_D6_status":
                if not isinstance(result, dict) or set(result) != set(codes):
                    raise ValueError("Unexpected status mapping")
                frame = result[codes[0]]
            elif endpoint == "D5_factor":
                if not isinstance(result, pd.DataFrame) or codes[0] not in result:
                    raise ValueError("Unexpected factor shape")
                frame = result[[codes[0]]].copy()
                dates = pd.to_datetime(frame.index, errors="coerce")
                listed = pd.Timestamp(sample_by_code[codes[0]]["listing_date"])
                frame = frame.loc[(dates >= max(pd.Timestamp(START), listed)) & (dates <= pd.Timestamp(END))]
            else:
                frame = pd.DataFrame(result).copy()
            if endpoint == "D3_bars" and not {"code", "date", "open", "high", "low", "close", "volume", "amount"}.issubset(frame):
                raise ValueError("Missing bar fields")
            if endpoint == "D4_D6_status" and not {"TRADE_DATE", "PRECLOSE", "HIGH_LIMITED", "LOW_LIMITED",
                "PRICE_HIGH_LMT_RATE", "PRICE_LOW_LMT_RATE", "IS_ST_SEC", "IS_SUSP_SEC",
                "IS_WD_SEC", "IS_XR_SEC"}.issubset(frame):
                raise ValueError("Missing historical status fields")
            if endpoint == "D1_basic" and not {"MARKET_CODE", "LISTDATE", "LISTPLATE_NAME"}.issubset(frame):
                raise ValueError("Missing basic fields")
            if frame.empty:
                raise ValueError(f"Empty {endpoint} result")
            if endpoint == "D1_basic" and set(frame.MARKET_CODE) != set(codes):
                raise ValueError("Basic metadata identity mismatch")
            if endpoint == "D4_D6_status" and not frame.MARKET_CODE.eq(codes[0]).all():
                raise ValueError("Historical status identity mismatch")
            record["rows"] = len(frame)
            record["columns"] = [str(column) for column in frame.columns]
            record["quality"] = quality(frame, endpoint)
            name = key.replace(":", "_").replace(".", "_") + ".csv.gz"
            path = PRIVATE / "raw" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, compression="gzip", index=endpoint == "D5_factor")
            record["cache_file"] = str(path.relative_to(PRIVATE))
            record["sha256"] = digest(path)
        state["records"][key] = record
        checkpoint()
        print(f"{key}: {record['status']} {record['rows']} rows {record['elapsed_seconds']:.2f}s", flush=True)
        return frame if result is not None else None

    if sample_path.exists():
        with sample_path.open(encoding="utf-8", newline="") as stream:
            sample = list(csv.DictReader(stream))
        if len(sample) != 100:
            raise ValueError("Existing sample is not 100 securities")
    else:
        t0 = time.perf_counter()
        with open(os.devnull, "w", encoding="utf-8") as null_stream:
            with redirect_stdout(null_stream), redirect_stderr(null_stream):
                universe = provider.base.get_hist_code_list(security_type="EXTRA_STOCK_A",
                    start_date=20260923, end_date=20260923,
                    local_path=str(PRIVATE / "sdk" / "universe") + os.sep)
        state["universe_lookup_seconds"] = round(time.perf_counter() - t0, 6)
        if not isinstance(universe, list):
            raise ValueError("Unexpected historical universe shape")
        state["historical_universe_sdk_returned_size"] = len(set(universe))
        market = [code for code in universe if code.endswith((".SH", ".SZ"))]
        state["historical_universe_size"] = len(set(market))
        state["historical_universe_retrieved_at"] = now()
        sample = select_sample(market)
        write_csv(sample_path, sample, list(sample[0]))
        checkpoint()
    sample_codes = [row["security_id"] for row in sample]
    sample_by_code = {row["security_id"]: row for row in sample}

    for phase, first, last in PHASES:
        if phase > args.through_phase:
            break
        codes = sample_codes[first:last]
        basic = call(f"D1_basic:phase_{phase}", "D1_basic", codes,
            lambda codes=codes: info.get_stock_basic(codes))
        if basic is None:
            raise RuntimeError(f"D1 failed in phase {phase}")
        basic_by_code = basic.set_index("MARKET_CODE")
        for row in sample[first:last]:
            code = row["security_id"]
            if code in basic_by_code.index:
                row["listing_date"] = str(basic_by_code.loc[code, "LISTDATE"])
                if row["sample_reason"] == "recent listing candidate" and row["listing_date"] >= "20240101":
                    row["sample_reason"] = "2024-2026 new listing"
        write_csv(sample_path, sample, list(sample[0]))
        consecutive_failures = 0
        for code in codes:
            operations = (
                ("D3_bars", lambda code=code: checked_sdk_bars(provider, code, START, END)),
                ("D4_D6_status", lambda code=code: info.get_history_stock_status([code],
                    local_path=str(PRIVATE / "sdk" / "status") + os.sep,
                    is_local=False, begin_date=20240101, end_date=20260923)),
                ("D5_factor", lambda code=code: provider.base.get_backward_factor([code],
                    local_path=str(PRIVATE / "sdk" / "factor") + os.sep, is_local=False)),
            )
            for endpoint, operation in operations:
                result = call(f"{endpoint}:{code}", endpoint, [code], operation)
                consecutive_failures = consecutive_failures + 1 if result is None else 0
                if consecutive_failures >= 3:
                    raise RuntimeError("Three consecutive endpoint failures; stop systemic API error")
        if phase not in state.setdefault("phases_completed", []):
            state["phases_completed"].append(phase)
        checkpoint()
        print(f"PHASE {phase} complete", flush=True)

    if args.through_phase != "C":
        return
    records = state["records"]
    if not all(completed(state, f"{endpoint}:{code}", PRIVATE)
        for code in sample_codes for endpoint in ("D3_bars", "D4_D6_status", "D5_factor")):
        raise RuntimeError("Some security endpoints did not complete")
    cross_quality = {"bar_gaps_vs_status": 0, "suspension_explained_bar_gaps": 0,
                     "unexplained_bar_gaps": 0, "factor_gaps_vs_status": 0,
                     "sample_board_metadata_mismatches": 0,
                     "unreadable_listing_plate_rows": 0,
                     "status_null_identity_rows": 0,
                     "status_null_date_rows": 0,
                     "observed_st_status_rows": 0, "observed_susp_status_rows": 0}
    expected_plate = {"SSE Main": "主板", "SZSE Main": "主板", "STAR": "科创板", "ChiNext": "创业板"}
    for phase, first, last in PHASES:
        basic = pd.read_csv(PRIVATE / records[f"D1_basic:phase_{phase}"]["cache_file"], compression="gzip")
        if set(basic.MARKET_CODE) != set(sample_codes[first:last]) or basic.MARKET_CODE.duplicated().any():
            raise ValueError(f"Cached D1 identity mismatch in phase {phase}")
        for row in basic.itertuples(index=False):
            plate = str(row.LISTPLATE_NAME)
            if "\ufffd" in plate:
                cross_quality["unreadable_listing_plate_rows"] += 1
            elif plate != expected_plate[sample_by_code[row.MARKET_CODE]["board"]]:
                cross_quality["sample_board_metadata_mismatches"] += 1
    for code in sample_codes:
        bars = pd.read_csv(PRIVATE / records[f"D3_bars:{code}"]["cache_file"], compression="gzip")
        status = pd.read_csv(PRIVATE / records[f"D4_D6_status:{code}"]["cache_file"], compression="gzip")
        factor = pd.read_csv(PRIVATE / records[f"D5_factor:{code}"]["cache_file"], compression="gzip")
        if (bars.empty or status.empty or factor.empty or not bars.code.eq(code).all() or
            status.MARKET_CODE.dropna().ne(code).any()):
            raise ValueError(f"Empty or mismatched cached identity: {code}")
        cross_quality["status_null_identity_rows"] += int(status.MARKET_CODE.isna().sum())
        bar_dates = set(pd.to_datetime(bars.date).dt.strftime("%Y-%m-%d"))
        date_text = status.TRADE_DATE.astype(str).str.replace(r"\.0$", "", regex=True)
        parsed_status_dates = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
        cross_quality["status_null_date_rows"] += int(parsed_status_dates.isna().sum())
        status_dates = parsed_status_dates.dt.strftime("%Y-%m-%d")
        status_set = set(status_dates.dropna())
        factor_dates = set(pd.to_datetime(factor.iloc[:, 0]).dt.strftime("%Y-%m-%d"))
        listed = pd.Timestamp(sample_by_code[code]["listing_date"]).strftime("%Y-%m-%d")
        if any(not max(START.isoformat(), listed) <= day <= END.isoformat()
               for day in bar_dates | status_set | factor_dates):
            raise ValueError(f"Out-of-window or pre-listing cached row: {code}")
        missing_bars = status_set - bar_dates
        suspended = set(status_dates[status.IS_SUSP_SEC.astype(str).isin(("1", "1.0"))].dropna())
        cross_quality["observed_st_status_rows"] += int(status.IS_ST_SEC.astype(str).isin(("1", "1.0")).sum())
        cross_quality["observed_susp_status_rows"] += int(status.IS_SUSP_SEC.astype(str).isin(("1", "1.0")).sum())
        cross_quality["bar_gaps_vs_status"] += len(missing_bars)
        cross_quality["suspension_explained_bar_gaps"] += len(missing_bars & suspended)
        cross_quality["unexplained_bar_gaps"] += len(missing_bars - suspended)
        cross_quality["factor_gaps_vs_status"] += len(status_set - factor_dates)
    metrics = [endpoint_metrics(records, endpoint, 100) for endpoint in
        ("D1_basic", "D3_bars", "D4_D6_status", "D5_factor")]
    total = round(sum(item["elapsed_seconds"] for item in state["segments"]), 6)
    eta = full_market_eta(metrics, state["historical_universe_size"], total)
    bottleneck = max(metrics[1:], key=lambda row: row["wall_clock_seconds"])["endpoint_name"]
    action = ("STAGE1_AMAZINGDATA_FULL_MARKET_MATERIALIZATION" if eta["eta_buffered_seconds"] < 4 * 3600
        else "STAGE1_AMAZINGDATA_ACQUISITION_OPTIMIZATION")
    performance_band = ("FAST" if eta["eta_linear_seconds"] < 3600 else
        "ACCEPTABLE" if eta["eta_linear_seconds"] < 4 * 3600 else
        "SLOW" if eta["eta_linear_seconds"] < 8 * 3600 else "VERY_SLOW")
    prior_summary = ARTIFACTS / "benchmark_summary.json"
    finished_at = (json.loads(prior_summary.read_text(encoding="utf-8"))["benchmark_finished_at"]
        if all_cached and prior_summary.exists() else now())
    summary = {"sdk_version": "1.1.6", "date_start": START.isoformat(), "date_end": END.isoformat(),
        "sample_size": 100, "random_seed": SEED, "benchmark_started_at": state["benchmark_started_at"],
        "benchmark_finished_at": finished_at, "total_wall_clock_seconds": total,
        "d1_seconds": metrics[0]["wall_clock_seconds"], "d3_seconds": metrics[1]["wall_clock_seconds"],
        "d4_d6_seconds": metrics[2]["wall_clock_seconds"], "d5_seconds": metrics[3]["wall_clock_seconds"],
        "total_requests": sum(row["request_count"] for row in metrics) + int("universe_lookup_seconds" in state),
        "core_endpoint_requests": sum(row["request_count"] for row in metrics),
        "universe_lookup_requests": int("universe_lookup_seconds" in state),
        "universe_lookup_seconds": state.get("universe_lookup_seconds"),
        "successful_requests": sum(row["successful_requests"] for row in metrics) + int("universe_lookup_seconds" in state),
        "failed_requests": sum(row["failed_requests"] for row in metrics),
        "retries": sum(row["retry_count"] for row in metrics),
        "total_rows": sum(row["row_count"] for row in metrics),
        "universe_security_rows_separate": state["historical_universe_size"],
        "full_market_security_count_assumption": state["historical_universe_size"],
        "eta_linear_seconds": eta["eta_linear_seconds"], "eta_buffered_seconds": eta["eta_buffered_seconds"],
        "dominant_bottleneck": bottleneck, "recommended_next_action": action,
        "performance_band": performance_band,
        "d8_benchmark_status": "DEFERRED"}
    write_csv(ARTIFACTS / "endpoint_metrics.csv", metrics, list(metrics[0]))
    failures = [failure for record in records.values() for failure in record.get("attempt_failures", [])]
    write_csv(ARTIFACTS / "request_failures.csv", failures, ["type", "attempt", "at", "endpoint", "security_id"])
    for name, payload in (("benchmark_summary.json", summary), ("full_market_eta.json", eta),
        ("benchmark_receipt.json", {"schema": "stage1-amazingdata-benchmark-receipt/1",
            "sample_sha256": digest(sample_path), "source": "AmazingData", "sdk_version": "1.1.6",
            "benchmark_version": "1", "date_start": START.isoformat(), "date_end": END.isoformat(),
            "call_mode": {"D1_basic": "BATCH_SECURITIES (5,15,80)",
                "D3_bars": "PER_SECURITY", "D4_D6_status": "PER_SECURITY", "D5_factor": "PER_SECURITY"},
            "universe_lookup": {"call_mode": "BATCH_DATE_RANGE", "date": END.isoformat(),
                "security_type": "EXTRA_STOCK_A",
                "sdk_returned_total_with_bj": state.get("historical_universe_sdk_returned_size"),
                "sh_sz_count": state["historical_universe_size"],
                "elapsed_seconds": state.get("universe_lookup_seconds")},
            "endpoint_columns": {endpoint: next(record.get("columns", []) for record in records.values()
                if record["endpoint"] == endpoint)
                for endpoint in ("D1_basic", "D3_bars", "D4_D6_status", "D5_factor")},
            "cache_manifest": {key: {"sha256": record.get("sha256"), "rows": record["rows"],
                "retrieved_at": record["finished_at"], "cache_file": record.get("cache_file")}
                for key, record in records.items()},
            "quality_totals": {endpoint: {field: sum(record.get("quality", {}).get(field, 0)
                for record in records.values() if record["endpoint"] == endpoint)
                for field in set().union(*(record.get("quality", {}) for record in records.values()
                    if record["endpoint"] == endpoint))}
                for endpoint in ("D1_basic", "D3_bars", "D4_D6_status", "D5_factor")},
            "cross_endpoint_quality": cross_quality,
            "checkpoint_resumed": len(state["segments"]) > 1,
            "measurement_segments": state["segments"],
            "measurement_note": "SDK-level internal network calls/retries are not exposed; request counts are SDK method attempts.",
            "factor_scope_note": "get_backward_factor has no date parameters; SDK call retrieves full history, while row_count is retained 2024-01-01..2026-09-23 post-listing rows."})):
        with (ARTIFACTS / name).open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    checkpoint()
    print(json.dumps({"total_wall_clock_seconds": total, "metrics": metrics, "eta": eta}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        run()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)  # SDK 1.1.6 logout has crashed on this Windows runtime.
    sys.stdout.flush()
    os._exit(0)
