"""Offline checks of captured Gate A evidence; never infers a trading universe."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.runs.manifest import canonical_bytes, file_hash

ARTIFACTS = ROOT / "artifacts/stage1_gate_a"


def load_evidence():
    core = json.loads((ARTIFACTS / "live_core_01.json").read_bytes())
    followup = json.loads((ARTIFACTS / "live_followup_01.json").read_bytes())
    return core, followup


def call_map(evidence):
    return {call["id"]: call for call in evidence["calls"]}


def rows(call, code=None):
    response = call["response"]
    return response["tables"][code]["sample"] if code else response["sample"]


def price(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def analyze(core, followup):
    calls = call_map(core)
    more = call_map(followup)
    checks = {}
    expected_holiday = ["2024-02-08", "2024-02-19", "2024-02-20"]
    for market in ("sh", "sz", "bj"):
        cal = core["calendars"][f"calendar_{market}"]
        checks[f"calendar_{market}_unique_sorted"] = cal["unique"] and cal["sorted"]
        checks[f"calendar_{market}_spring_festival"] = cal["windows"]["2024-02-08:2024-02-20"] == expected_holiday
        checks[f"calendar_{market}_national_day"] = cal["windows"]["2024-09-27:2024-10-09"] == ["2024-09-27", "2024-09-30", "2024-10-08", "2024-10-09"]
    checks["historical_delisting_membership"] = (
        calls["history_2016"]["response"]["sample_membership"]["600005.SH"] is True
        and calls["history_2017"]["response"]["sample_membership"]["600005.SH"] is False)
    checks["historical_ipo_membership"] = (
        calls["history_pre_ipo"]["response"]["sample_membership"]["603194.SH"] is False
        and calls["history_ipo"]["response"]["sample_membership"]["603194.SH"] is True)
    checks["repeated_bars_equal"] = calls["bars_normal"]["response"] == calls["bars_repeat"]["response"]

    bar_quality = []
    for call in core["calls"]:
        if call["api"] != "bars" or call.get("result") != "RETURNED":
            continue
        for code, table in call["response"]["tables"].items():
            frame = pd.DataFrame(table["sample"])
            dates = pd.to_datetime(frame.kline_time)
            valid = (code in call["request"]["codes"] and frame.code.eq(code).all()
                     and np.isfinite(frame[["open", "high", "low", "close", "volume", "amount"]]).all().all()
                     and dates.between(pd.Timestamp(str(call["request"]["start"])), pd.Timestamp(str(call["request"]["end"]))).all()
                     and not frame.duplicated(["code", "kline_time"]).any()
                     and frame[["open", "high", "low", "close"]].gt(0).all().all()
                     and frame.high.ge(frame[["open", "close", "low"]].max(axis=1)).all()
                     and frame.low.le(frame[["open", "close", "high"]].min(axis=1)).all()
                     and frame[["volume", "amount"]].ge(0).all().all())
            bar_quality.append({"call": call["id"], "code": code, "passed": bool(valid), "rows_checked": len(frame)})
    checks["all_captured_bar_rows_valid"] = all(item["passed"] for item in bar_quality)
    status = {r["TRADE_DATE"]: r for r in rows(calls["status_st_transition"], "600518.SH")}
    checks["explicit_suspension_matches_announcement"] = status["20240703"]["IS_SUSP_SEC"] == "1"
    checks["suspension_has_no_fabricated_bar"] = not any(r["kline_time"].startswith("2024-07-03") for r in rows(calls["bars_st_transition"], "600518.SH"))
    # The effective date is fixed by the issuer's 2024-07-03 announcement.
    checks["st_effective_date_matches_announcement"] = status["20240704"]["IS_ST_SEC"] == "0"
    checks["st_single_day_recheck_matches_initial"] = rows(more["status_st_effective_day_repeat"], "600518.SH")[0]["IS_ST_SEC"] == status["20240704"]["IS_ST_SEC"]

    mismatches = []
    for call in core["calls"]:
        if call["api"] != "history_status" or call.get("result") != "RETURNED":
            continue
        for code, table in call["response"]["tables"].items():
            for row in table["sample"]:
                rate = Decimal(str(row["PRICE_HIGH_LMT_RATE"]))
                if rate >= 1:  # Sentinels are reported separately; never treated as a real limit.
                    continue
                reference = Decimal(str(row["PRECLOSE"]))
                expected_up = price(reference * (1 + rate))
                expected_down = price(reference * (1 - Decimal(str(row["PRICE_LOW_LMT_RATE"]))))
                if expected_up != price(row["HIGH_LIMITED"]) or expected_down != price(row["LOW_LIMITED"]):
                    mismatches.append({"code": code, "date": row["TRADE_DATE"], "reported_rate": float(rate),
                                       "reference": float(reference), "upper": row["HIGH_LIMITED"], "lower": row["LOW_LIMITED"]})
    checks["limit_rate_price_consistency"] = not mismatches
    ipo = rows(calls["status_ipo"], "603194.SH")
    zero_limit_dates = sorted(r["TRADE_DATE"] for r in ipo if r["HIGH_LIMITED"] == 0 and r["LOW_LIMITED"] == 0 and r["PRICE_HIGH_LMT_RATE"] == 999)
    maotai_status = {r["TRADE_DATE"]: r for r in rows(calls["status_ex_dividend"], "600519.SH")}
    maotai_bars = {r["kline_time"][:10].replace("-", ""): r for r in rows(calls["bars_ex_dividend"], "600519.SH")}
    checks["ex_dividend_reference_differs_from_yesterday_close"] = maotai_status["20240619"]["PRECLOSE"] != maotai_bars["20240618"]["close"]
    checks["factor_refresh_same_observed_hash"] = followup["factor_windows"]["factors_two_symbols"]["full_history_hash"] == followup["factor_windows"]["factors_repeat"]["full_history_hash"]
    completed_calls = core["calls"] + followup["calls"]
    return {"schema": "gate-a-checks/1", "checks": checks, "bar_quality": bar_quality,
            "limit_rate_price_mismatches": mismatches, "ipo_zero_limit_dates": zero_limit_dates,
            "failed_checks_are_qualification_findings": True,
            "sdk_completed_calls": len(completed_calls), "sdk_in_flight_timeout_calls": int("in_flight" in core),
            "completed_call_seconds": sum(c["elapsed_seconds"] for c in completed_calls),
            "rows_returned_by_api": {api: sum(c.get("response", {}).get("rows", 0) or 0 for c in completed_calls if c["api"] == api)
                                     for api in sorted({c["api"] for c in completed_calls})},
            "qualification_retries": 0, "sdk_internal_retries": "UNVERIFIED",
            "raw_factor_end_date_parameter": "UNSUPPORTED_BY_INSTALLED_SIGNATURE",
            "factor_revision_pit": "UNVERIFIED", "historical_availability_pit": "UNVERIFIED",
            "source_hashes": {name: file_hash(ARTIFACTS / name) for name in ("live_core_01.json", "live_followup_01.json")}}


if __name__ == "__main__":
    result = analyze(*load_evidence())
    (ARTIFACTS / "offline_checks.json").write_bytes(canonical_bytes(result))
    print(json.dumps({"checks": result["checks"], "sdk_completed_calls": result["sdk_completed_calls"]}))
