"""Bounded, opt-in live diagnostics. Never run automatically during pytest.

Run from the repository root with --plan and --output. SDK output is discarded
in a subprocess; only allowlisted market metadata reaches the evidence file.
No credentials, exception messages, full universe lists, or raw SDK dumps are saved.
"""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.runs.manifest import canonical_bytes, digest

FIELDS = {
    "code", "ts_code", "date", "kline_time", "open", "high", "low", "close", "volume", "amount",
    "pre_close", "high_limited", "low_limited", "price_tick", "symbol",
    "MARKET_CODE", "SECURITY_NAME", "LISTDATE", "DELISTDATE", "LISTPLATE_NAME", "IS_LISTED",
    "TRADE_DATE", "PRECLOSE", "HIGH_LIMITED", "LOW_LIMITED", "PRICE_HIGH_LMT_RATE", "PRICE_LOW_LMT_RATE",
    "IS_ST_SEC", "IS_SUSP_SEC", "IS_WD_SEC", "IS_XR_SEC", "EXCHANGE_CODE", "SECURITY_TYPE",
}
SYMBOL = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
APIS = {"calendar", "current_codes", "historical_codes", "current_info", "stock_basic",
        "history_status", "bars", "backward_factor", "checked_bars", "malformed_bars"}


def validate_plan(plan):
    if not isinstance(plan, list) or not 1 <= len(plan) <= 40:
        raise ValueError("A plan requires 1..40 explicitly bounded calls")
    if len({item["id"] for item in plan}) != len(plan):
        raise ValueError("Diagnostic call IDs must be unique")
    for item in plan:
        if set(item) - {"id", "api", "codes", "start", "end", "market", "as_of", "security_type"}:
            raise ValueError("Only declared, non-secret request fields are allowed")
        if item["api"] not in APIS or not re.fullmatch(r"[a-z0-9_]+", item["id"]):
            raise ValueError("Unsupported diagnostic operation")
        codes = item.get("codes", [])
        if len(codes) > 8 or any(not SYMBOL.fullmatch(c) for c in codes):
            raise ValueError("At most eight exchange-qualified symbols per call")
        if "start" in item:
            first = pd.Timestamp(str(item["start"]))
            last = pd.Timestamp(str(item["end"]))
            if not 0 <= (last - first).days <= 21:
                raise ValueError("Date interval must be 0..21 days")
        if item["api"] in {"bars", "checked_bars", "history_status", "stock_basic", "backward_factor", "malformed_bars"} and not codes:
            raise ValueError("Explicit symbols required")
    if len({c for item in plan for c in item.get("codes", [])}) > 10:
        raise ValueError("At most ten symbols across the diagnostic plan")


def summarize_frame(frame, *, codes=(), sample_limit=12):
    frame = pd.DataFrame(frame).copy()
    result = {"type": "DataFrame", "rows": len(frame), "columns": [str(c) for c in frame.columns],
              "dtypes": {str(c): str(t) for c, t in frame.dtypes.items()},
              "index_type": type(frame.index).__name__, "index_unique": bool(frame.index.is_unique),
              "index_sorted": bool(frame.index.is_monotonic_increasing)}
    # Do not serialize unrecognized provider fields, even in hashes.
    keep = [c for c in frame.columns if c in FIELDS or (isinstance(c, str) and SYMBOL.fullmatch(c))]
    selected = frame[keep].copy()
    index_text = frame.index.astype(str)
    safe_index = [s if SYMBOL.fullmatch(s) or re.fullmatch(r"\d+|\d{4}-\d{2}-\d{2}(?:[ T][\d:.+-]+)?", s) else "<omitted>" for s in index_text]
    selected.insert(0, "observed_index", safe_index)
    result["selected_fields_hash"] = digest(selected.to_dict("records"))
    result["null_counts"] = {str(c): int(frame[c].isna().sum()) for c in keep}
    if codes:
        identity = next((c for c in ("MARKET_CODE", "ts_code", "code") if c in selected), None)
        if identity:
            selected = selected.loc[selected[identity].isin(codes)]
        elif all(SYMBOL.fullmatch(str(i)) for i in frame.index):
            selected = selected.loc[frame.index.isin(codes)]
    if len(selected) <= sample_limit:
        sample = selected
    else:
        sample = pd.concat([selected.head(sample_limit // 2), selected.tail(sample_limit // 2)])
    result["sample"] = sample.to_dict("records")
    return result


def summarize(value, *, codes=()):
    if isinstance(value, pd.DataFrame):
        return summarize_frame(value, codes=codes)
    if isinstance(value, dict):
        # Dict market responses must expose security identity. Never serialize an error body.
        keys = [k for k in value if isinstance(k, str) and SYMBOL.fullmatch(k)]
        return {"type": "dict", "key_count": len(value), "security_keys": keys,
                "unrecognized_key_count": len(value) - len(keys),
                "tables": {k: summarize_frame(value[k]) for k in keys},
                "rows": sum(len(value[k]) for k in keys)}
    if isinstance(value, (list, tuple)):
        securities = [v for v in value if isinstance(v, str) and SYMBOL.fullmatch(v)]
        if len(securities) == len(value):
            return {"type": "security_list", "rows": len(value), "unique": len(set(value)) == len(value),
                    "list_hash": digest(sorted(securities)), "sample_membership": {c: c in value for c in codes}}
        return {"type": type(value).__name__, "rows": len(value)}
    return {"type": type(value).__name__, "rows": 0 if value is None else None}


def sdk_metadata(ad):
    result = {}
    for clsname in ("BaseData", "InfoData", "MarketData"):
        cls = getattr(ad, clsname)
        result[clsname] = {}
        for name in dir(cls):
            if name.startswith("_"):
                continue
            method = getattr(cls, name)
            try:
                sig = str(inspect.signature(method))
                # Default SDK storage locations are not included in artifacts.
                sig = re.sub(r"local_path='[^']*'", "local_path=<sdk-default-omitted>", sig)
            except (ValueError, TypeError):
                sig = "unavailable"
            result[clsname][name] = {"signature": sig}
    return result


def worker(plan, output):
    from importlib.metadata import version
    from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat
    logging.disable(logging.CRITICAL)
    try:
        from loguru import logger
        logger.remove()
    except ImportError:
        pass
    _install_numba_compat()  # Explicit compatibility mode, only in this SDK worker.
    import AmazingData as ad
    from_env = all(os.getenv(k) for k in ("AMAZINGDATA_USERNAME", "AMAZINGDATA_PASSWORD", "AMAZINGDATA_IP", "AMAZINGDATA_PORT"))
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh", cache_dir=Path.cwd(),
                                retry_count=1, retry_delay_seconds=0, use_numba_compat=True)
    evidence = {"schema": "gate-a-evidence/1", "stage0_commit": "d856f6f84911e55e9ed6208d3610272cc323ee4d",
                "created_at": datetime.now(timezone.utc).isoformat(), "sdk_version": version("AmazingData"),
                "credentials_source_type": "environment" if from_env else "external config",
                "compatibility_mode": "explicit_numba_shim", "sdk_metadata": sdk_metadata(ad),
                "calls": [], "logout": "UNVERIFIED_NOT_INVOKED_NATIVE_RISK"}

    def save():
        output.write_bytes(canonical_bytes(evidence))

    def record(item, operation):
        entry = {"id": item["id"], "api": item["api"], "request": item, "retry_count": 0,
                 "started_at": datetime.now(timezone.utc).isoformat()}
        evidence["in_flight"] = entry
        save()
        started = time.monotonic()
        result = None
        try:
            result = operation()
            entry["response"] = summarize(result, codes=item.get("codes", []))
            entry["result"] = "RETURNED" if entry["response"].get("rows") != 0 else "EMPTY_UNEXPLAINED"
        except Exception as exc:
            entry.update(result="ERROR", error_type=type(exc).__name__)
            # Neither str(exc), repr(exc), traceback nor exception arguments are saved.
        entry["elapsed_seconds"] = round(time.monotonic() - started, 6)
        evidence["calls"].append(entry)
        evidence.pop("in_flight", None)
        save()
        return result

    provider = record({"id": "login", "api": "login"}, lambda: adapter._call_with_retry(lambda p: p))
    if provider is None:
        evidence["completed"] = False
        save()
        return
    evidence["calls"][-1]["result"] = "SESSION_ESTABLISHED"
    info = ad.InfoData()
    calendar = None
    for item in plan:
        api, codes = item["api"], item.get("codes", [])
        first, last = item.get("start"), item.get("end")
        local = str(Path.cwd() / item["id"]) + os.sep
        def operation():
            nonlocal calendar
            if api == "calendar":
                values = provider.base.get_calendar(market=item.get("market", "SH"), date=item.get("as_of", 20260921))
                dates = [pd.Timestamp(str(v)).strftime("%Y-%m-%d") for v in values]
                if item.get("market", "SH") == "SH":
                    calendar = values
                    provider.calendar = [int(d.replace("-", "")) for d in dates]
                evidence.setdefault("calendars", {})[item["id"]] = {
                    "total_dates_returned": len(dates), "unique": len(set(dates)) == len(dates),
                    "sorted": dates == sorted(dates), "calendar_hash": digest(dates),
                    "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                    "windows": {f"{a}:{b}": [d for d in dates if a <= d <= b] for a, b in (
                        ("2016-09-05", "2016-09-11"), ("2024-01-08", "2024-01-14"),
                        ("2024-02-08", "2024-02-20"), ("2024-09-27", "2024-10-09"))}}
                return values
            if api == "current_codes":
                return provider.base.get_code_list(security_type=item.get("security_type", "EXTRA_STOCK_A"))
            if api == "historical_codes":
                return provider.base.get_hist_code_list(security_type=item.get("security_type", "EXTRA_STOCK_A"),
                    start_date=first, end_date=last, local_path=local)
            if api == "current_info":
                return provider.base.get_code_info(security_type="EXTRA_STOCK_A")
            if api == "stock_basic":
                return info.get_stock_basic(codes)
            if api == "history_status":
                return info.get_history_stock_status(codes, local_path=local, is_local=False, begin_date=first, end_date=last)
            if api == "backward_factor":
                frame = provider.base.get_backward_factor(codes, local_path=local, is_local=False)
                if isinstance(frame, pd.DataFrame):
                    idx = pd.to_datetime(frame.index)
                    evidence.setdefault("factor_windows", {})[item["id"]] = summarize_frame(frame.loc[(idx >= "2024-06-14") & (idx <= "2024-06-24")])
                    evidence.setdefault("factor_windows", {})[item["id"]]["full_history_hash"] = digest(frame.reset_index().astype(str).to_dict("records"))
                return frame
            if api == "checked_bars":
                from research.data.amazingdata import checked_sdk_bars
                return checked_sdk_bars(provider, codes[0], pd.Timestamp(str(first)).date(), pd.Timestamp(str(last)).date())
            market = provider._ensure_market()
            return market.query_kline(codes, begin_date=last if api == "malformed_bars" else first,
                                      end_date=first if api == "malformed_bars" else last, period=ad.constant.Period.day.value)
        record(item, operation)
    evidence["completed"] = True
    evidence["sdk_call_count"] = len(evidence["calls"])
    evidence["session_reused"] = adapter._session_provider is provider
    evidence["request_accounting"] = "SDK calls including login; internal SDK network request/retry counts not exposed"
    save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_bytes())
    validate_plan(plan)
    output = args.output.resolve()
    if args.worker:
        try:
            worker(plan, output)
        except BaseException as exc:
            prior = json.loads(output.read_bytes()) if output.exists() else {}
            prior.update(completed=False, worker_error_type=type(exc).__name__)
            output.write_bytes(canonical_bytes(prior))
        # Avoid unverified native SDK shutdown/destructor behavior in this worker.
        os._exit(0)
    if output.exists():
        raise SystemExit("Refusing to overwrite qualification evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = ROOT / ".test_tmp" / output.stem
    work.mkdir(parents=True, exist_ok=False)
    process_status = {}
    try:
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
            "--plan", str(args.plan.resolve()), "--output", str(output)], cwd=work,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        process_status = {"worker_exit_code": result.returncode, "evidence_exists": output.exists()}
    except subprocess.TimeoutExpired:
        process_status = {"worker_state": "TIMED_OUT", "evidence_exists": output.exists()}
    output.with_suffix(".process.json").write_bytes(canonical_bytes(process_status))
    print(json.dumps(process_status))


if __name__ == "__main__":
    main()
