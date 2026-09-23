"""One-symbol real-bar D5 adapter smoke, with event-derived daily factors."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.factor_pit import BACKWARD_QUANTUM, CorporateAction, FactorRatioSnapshot
from research.foundation import TradingCalendar
from research.indicator_prices import AdjustmentFactor, RawCloseObservation, build_pit_adjusted_close
from research.primitives import rsi14
from research.rsi_warmup import plan_rsi_replay


PRIVATE = ROOT / ".local_research_data"
OUTPUT = ROOT / "artifacts" / "stage1_factor_pit" / "rsi_real_adapter_smoke.json"
CODE = "001331.SZ"
EVENT_DATES = (date(2023, 6, 7), date(2024, 7, 17), date(2025, 6, 5))
SHANGHAI = timezone(timedelta(hours=8))


def main() -> None:
    captured = PRIVATE / "factor_pit_rsi_smoke_20260923_final"
    factor_capture = PRIVATE / "factor_pit_probe_20260923_stratified"
    if any(not path.resolve().is_relative_to(PRIVATE.resolve()) for path in (captured, factor_capture)):
        raise ValueError("Raw provider input escaped ignored local directory")
    receipt = json.loads((captured / "receipt.json").read_text(encoding="utf-8"))
    for name, item in receipt["calls"].items():
        path = captured / f"{name}.{'json' if name == 'calendar' else 'h5'}"
        if item["status"] != "RETURNED" or sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Incomplete real-bar/status/calendar capture")
    days = tuple(date.fromisoformat(day) for day in json.loads(
        (captured / "calendar.json").read_text(encoding="utf-8")))
    bars = pd.read_hdf(captured / "bars.h5", "bars")
    statuses = pd.read_hdf(captured / "status.h5", "status")
    single = pd.read_hdf(factor_capture / "single_event_factor.h5", "single_event_factor")[CODE]
    backward = pd.read_hdf(factor_capture / "backward_factor.h5", "backward_factor")[CODE]
    dividends = json.loads((factor_capture / "dividend.json").read_text(encoding="utf-8"))["rows"]
    start, cutoff = days[0], days[-1]
    if (start != date(2022, 9, 8) or cutoff != date(2025, 6, 5) or len(days) != 660
            or tuple(sorted(set(days))) != days):
        raise ValueError("Smoke window is not the official listing-prefix interval")
    bar_by_day = {date.fromisoformat(row.date): row for row in bars.itertuples(index=False)}
    status_by_day = {date.fromisoformat(row.TRADE_DATE[:4] + "-" + row.TRADE_DATE[4:6]
                                     + "-" + row.TRADE_DATE[6:8]): row
                     for row in statuses.itertuples(index=False)}
    if (len(bar_by_day) != len(bars) or len(status_by_day) != len(statuses)
            or set(bar_by_day) != set(days) or set(status_by_day) != set(days)
            or any(row.code != CODE for row in bars.itertuples(index=False))
            or any(row.MARKET_CODE != CODE or row.IS_SUSP_SEC != "0"
                   for row in statuses.itertuples(index=False))):
        raise ValueError("Full listing prefix has an unknown security-day state")
    implementation = [row for row in dividends if row["MARKET_CODE"] == CODE
                      and row["DIV_PROGRESS"] == "3" and row["DATE_EX"]
                      and start <= date.fromisoformat(row["DATE_EX"][:4] + "-"
                                                      + row["DATE_EX"][4:6] + "-"
                                                      + row["DATE_EX"][6:8]) <= cutoff]
    if ({date(int(row["DATE_EX"][:4]), int(row["DATE_EX"][4:6]), int(row["DATE_EX"][6:8]))
         for row in implementation} != set(EVENT_DATES)
            or any(row["IS_CHANGED"] != 0 or row["DATE_DVD_ANN"] >= row["DATE_EX"]
                   for row in implementation)):
        raise ValueError("Unresolved or late listing-prefix distribution")
    snapshot = "factor-pit-one-security-diagnostic-20250605"
    single_bytes = (factor_capture / "single_event_factor.h5").read_bytes()
    backward_bytes = (factor_capture / "backward_factor.h5").read_bytes()
    source_hash = sha256(single_bytes + backward_bytes).hexdigest()
    actions = tuple(CorporateAction(
        event_id=f"{CODE}:{row['REPORT_PERIOD']}", kind="DIVIDEND_OR_TRANSFER",
        effective_date=date(int(row["DATE_EX"][:4]), int(row["DATE_EX"][4:6]), int(row["DATE_EX"][6:8])),
        known_date=date(int(row["DATE_DVD_ANN"][:4]), int(row["DATE_DVD_ANN"][4:6]),
                        int(row["DATE_DVD_ANN"][6:8])),
        single_factor=Decimal(str(single.loc[f"{row['DATE_EX'][:4]}-{row['DATE_EX'][4:6]}-{row['DATE_EX'][6:8]}"])),
        state="IMPLEMENTED", version=1, revision_resolved=True)
        for row in implementation)
    ratio_cache = FactorRatioSnapshot(
        CODE, snapshot, source_hash,
        {day: Decimal(str(backward.loc[day.isoformat()])).quantize(BACKWARD_QUANTUM)
         for day in days}, actions)
    raw = []
    factors = []
    cumulative = Decimal(1)
    max_factor_abs_error = Decimal(0)
    for day in days:
        ratio_cache.ratio(day, cutoff)  # Fail closed if any cached daily ratio is not event-derived.
        row = bar_by_day[day]
        at = datetime.combine(day, time(15, 30), SHANGHAI).isoformat()
        raw.append(RawCloseObservation(CODE, day, str(Decimal(str(row.close))), "TRADING", True,
                                       at, snapshot))
        event_factor = Decimal(str(single.loc[day.isoformat()]))
        cumulative *= event_factor
        provider_factor = Decimal(str(backward.loc[day.isoformat()])).quantize(BACKWARD_QUANTUM)
        max_factor_abs_error = max(max_factor_abs_error, abs(cumulative - provider_factor))
        # This value is reconstructed from effective events <= day, not asserted to be an old SDK export.
        factors.append(AdjustmentFactor(CODE, day, str(cumulative), at, snapshot,
                                        source_hash, True))
    if max_factor_abs_error > BACKWARD_QUANTUM / 2:
        raise ValueError("Event-derived daily factor diverges from provider factor")
    calendar = TradingCalendar(days, "amazingdata-sh-2022-09-08-to-2025-06-05")
    decision = datetime.combine(cutoff, time(16), SHANGHAI)
    series = build_pit_adjusted_close(
        security_id=CODE, as_of_date=cutoff, decision_at=decision, reset_date=start,
        source_snapshot_id=snapshot, factor_schema="daily", calendar=calendar,
        raw=raw, factors=factors)
    results = {}
    for label, full, warmup in (("full_prefix", True, None), ("warmup_120", False, 120),
                                ("warmup_150", False, 150)):
        plan = plan_rsi_replay(series, full_prefix_available=full, warmup_observations=warmup)
        outcome = rsi14(plan.series, decision_at=decision)
        results[label] = {"plan_status": plan.status, "primitive_status": outcome.status,
                          "canonical": plan.canonical, "observations": plan.selected_observations,
                          "rsi14": str(outcome.value)}
    full_value = Decimal(results["full_prefix"]["rsi14"])
    for label in ("warmup_120", "warmup_150"):
        results[label]["absolute_difference_from_full"] = str(
            abs(Decimal(results[label]["rsi14"]) - full_value))
    if any(result["primitive_status"] != "READY" for result in results.values()):
        raise ValueError("RSI adapter smoke not ready")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "schema": "stage1-factor-pit-rsi-real-adapter-smoke/1", "security": CODE,
        "official_listing_source": "https://static.cninfo.com.cn/finalpage/2022-09-07/1214549028.PDF",
        "listing_date": str(start), "as_of_date": str(cutoff),
        "factor_price_basis": "PIT_ADJUSTED_CLOSE_V1",
        "factor_source": "event-derived cumulative A(e), e <= trade date; checked against B(d)",
        "factor_available_at": "effective trading date 15:30 Asia/Shanghai; derived-event diagnostic assumption",
        "raw_bar_available_at": "trading date 15:30 Asia/Shanghai; EOD diagnostic assumption",
        "private_capture_sha256": {name: item["sha256"] for name, item in receipt["calls"].items()},
        "factor_capture_sha256": {"single_event": sha256(single_bytes).hexdigest(),
                                  "backward": sha256(backward_bytes).hexdigest(),
                                  "combined_snapshot": source_hash},
        "calendar_sessions": len(days), "bars": len(bars), "status_rows": len(statuses),
        "all_sessions_trading_status_confirmed": True,
        "implemented_event_dates": [str(day) for day in EVENT_DATES],
        "maximum_event_factor_vs_six_decimal_backward_error": str(max_factor_abs_error),
        "rsi": results, "status": "PASS_ONE_SECURITY_DIAGNOSTIC",
        "scope_limit": "D5 adapter smoke only; D1/D3/D4/D6/D7/D8 remain unqualified",
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS_ONE_SECURITY_DIAGNOSTIC", "bars": len(bars),
                      "full_rsi": results["full_prefix"]["rsi14"]}))


if __name__ == "__main__":
    main()
