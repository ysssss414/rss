"""Reduce two ignored D5 probes to publication-safe identity and PIT-ratio evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.factor_pit import BACKWARD_QUANTUM, ratio_matches


PRIVATE = ROOT / ".local_research_data"
HIGHLIGHTS = {"600519.SH": date(2024, 6, 19), "600030.SH": date(2022, 1, 27),
              "300059.SZ": date(2023, 4, 18), "001331.SZ": date(2025, 6, 5),
              "688115.SH": date(2026, 7, 15)}


def _load(directory: Path):
    if not directory.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Vendor rows must remain in the ignored private directory")
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    for name, item in receipt["calls"].items():
        suffix = "h5" if name.endswith("factor") else "json"
        path = directory / f"{name}.{suffix}"
        if item["status"] != "RETURNED" or sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Incomplete or changed private probe")
    return (receipt,
            pd.read_hdf(directory / "backward_factor.h5", "backward_factor"),
            pd.read_hdf(directory / "single_event_factor.h5", "single_event_factor"),
            json.loads((directory / "dividend.json").read_text(encoding="utf-8"))["rows"],
            json.loads((directory / "right_issue.json").read_text(encoding="utf-8"))["rows"])


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:4] + "-" + value[4:6] + "-" + value[6:8]) if value else None


def validate(directories: tuple[Path, ...]) -> dict:
    captures = [_load(directory) for directory in directories]
    backward = pd.concat([capture[1] for capture in captures], axis=1)
    single = pd.concat([capture[2] for capture in captures], axis=1)
    if (not backward.index.equals(single.index) or not backward.columns.equals(single.columns)
            or not backward.index.is_unique or backward.columns.duplicated().any()):
        raise ValueError("Factor tables disagree on date or security grain")
    dividends = [row for capture in captures for row in capture[3]]
    rights = [row for capture in captures for row in capture[4]]
    cutoff = backward.index[-1].date()
    dividend_dates = {(row["MARKET_CODE"], _day(row["DATE_EX"])) for row in dividends
                      if row["DIV_PROGRESS"] == "3" and row["DATE_EX"]}
    rights_dates = {(row["MARKET_CODE"], _day(row["EX_DIVIDEND_DATE"])) for row in rights
                    if row["PROGRESS"] == "3" and row["EX_DIVIDEND_DATE"]}
    event_types = Counter()
    changed_implemented = []
    late_known = []
    dividend_invalid = []
    for row in dividends:
        if row["DIV_PROGRESS"] != "3" or not row["DATE_EX"]:
            continue
        effective = _day(row["DATE_EX"])
        if effective > cutoff:
            continue
        known = _day(row["DATE_DVD_ANN"])
        record = _day(row["DATE_EQY_RECORD"])
        if known is None or known >= effective:
            late_known.append({"security": row["MARKET_CODE"], "effective": str(effective)})
        if record is None or record >= effective:
            dividend_invalid.append({"security": row["MARKET_CODE"], "effective": str(effective)})
        cash = Decimal(str(row["DVD_PER_SHARE_PRE_TAX_CASH"] or 0)) > 0
        bonus = Decimal(str(row["DIV_BONUSRATE"] or 0)) > 0
        transfer = Decimal(str(row["DIV_CONVERSEDRATE"] or 0)) > 0
        if Decimal(str(row["DVD_PER_SHARE_STK"] or 0)) != (Decimal(str(row["DIV_BONUSRATE"] or 0))
                                                            + Decimal(str(row["DIV_CONVERSEDRATE"] or 0))):
            dividend_invalid.append({"security": row["MARKET_CODE"], "effective": str(effective),
                                     "reason": "stock_term_mismatch"})
        event_types["+".join(name for name, present in (("cash", cash), ("bonus", bonus),
                                                           ("transfer", transfer)) if present)] += 1
        if row["IS_CHANGED"] == 1:
            changed_implemented.append({"security": row["MARKET_CODE"],
                                        "effective": str(effective), "known": str(known)})
    identity_count = 0
    identity_failures = []
    no_event_changes = []
    unmatched_dates = []
    factor_dates = {}
    later_event_pairs = 0
    multiple_future_pairs = 0
    pair_failures = []
    representative = []
    for code in backward.columns:
        b = backward[code]
        a = single[code]
        events = []
        for i in range(1, len(b)):
            when = b.index[i].date()
            if when < date(2014, 1, 1):
                continue
            previous, current, factor = (Decimal(str(b.iloc[i - 1])).quantize(BACKWARD_QUANTUM),
                                         Decimal(str(b.iloc[i])).quantize(BACKWARD_QUANTUM),
                                         Decimal(str(a.iloc[i])))
            changed = current != previous
            signaled = factor != 1
            if changed != signaled:
                no_event_changes.append({"security": code, "effective": str(when)})
            if signaled:
                events.append((when, b.index[i - 1].date(), factor))
                identity_count += 1
                if not ratio_matches(Decimal(1) / factor, previous, current):
                    identity_failures.append({"security": code, "effective": str(when)})
                if (code, when) not in dividend_dates | rights_dates:
                    unmatched_dates.append({"security": code, "effective": str(when)})
        factor_dates[code] = len(events)
        for first in range(len(events)):
            for last in range(first, len(events) - 1):  # require at least one action after T
                d, t = events[first][1], events[last][0]
                if d >= t:
                    continue
                product = Decimal(1)
                for effective, _, factor in events[first:last + 1]:
                    if d < effective <= t:
                        product *= factor
                reconstructed = Decimal(1) / product
                provider_d = Decimal(str(b.loc[str(d)])).quantize(BACKWARD_QUANTUM)
                provider_t = Decimal(str(b.loc[str(t)])).quantize(BACKWARD_QUANTUM)
                later_event_pairs += 1
                if len(events) - last - 1 >= 2:
                    multiple_future_pairs += 1
                if not ratio_matches(reconstructed, provider_d, provider_t):
                    pair_failures.append({"security": code, "d": str(d), "T": str(t)})
                if first == last and t == HIGHLIGHTS.get(code):
                    representative.append({"security": code, "d": str(d), "T": str(t),
                                           "future_event_count": len(events) - last - 1,
                                           "provider_ratio": str(provider_d / provider_t),
                                           "reconstructed_ratio": str(reconstructed)})
    rights_semantics = []
    for row in rights:
        if row["PROGRESS"] != "3":
            continue
        effective = _day(row["EX_DIVIDEND_DATE"])
        execute = _day(row["EXECUTE_DATE"])
        result = _day(row["RESULT_DATE"])
        updated = _day(row["ANN_DATE"])
        rights_semantics.append({"security": row["MARKET_CODE"], "effective": str(effective),
                                 "execution_announcement": str(execute), "result": str(result),
                                 "record_updated": str(updated),
                                 "execution_known_before_effective": bool(execute and execute <= effective),
                                 "result_no_later_than_effective": bool(result and result <= effective),
                                 "record_update_after_effective": bool(updated and updated > effective)})
    return {
        "schema": "stage1-factor-pit-provider-validation/1",
        "source": "AmazingData 1.1.6, is_local=False, two bounded captures",
        "retrieved_at_utc": [capture[0]["retrieved_at"] for capture in captures],
        "private_input_sha256": {f"{i}_{name}": item["sha256"] for i, capture in enumerate(captures)
                                  for name, item in capture[0]["calls"].items()},
        "factor_calendar_dates": len(backward), "factor_last_date": str(cutoff),
        "security_count": len(backward.columns), "security_ids": list(backward.columns),
        "factor_change_count_since_2014": identity_count, "factor_change_count_by_security": factor_dates,
        "composition": "B(t) = B(previous trading date) * A(t)",
        "ratio": "B(d)/B(T) = 1 / product(A(e) for d < e <= T)",
        "backward_factor_empirical_decimal_places": 6,
        "identity_failures": identity_failures, "unexplained_factor_changes": no_event_changes,
        "factor_dates_without_implemented_event": unmatched_dates,
        "dividend_progress_counts": dict(Counter(str(row["DIV_PROGRESS"]) for row in dividends)),
        "dividend_event_types_implemented_through_cutoff": dict(event_types),
        "bonus_only": "NOT_PRESENT_IN_QUALIFICATION_RANGE" if event_types["bonus"] == 0 else "PRESENT",
        "changed_implemented_cases": changed_implemented,
        "late_implementation_announcements": late_known,
        "invalid_dividend_record_or_stock_terms": dividend_invalid,
        "rights_semantics": rights_semantics,
        "future_event_pair_count": later_event_pairs,
        "multiple_future_event_pair_count": multiple_future_pairs,
        "future_event_pair_failures": pair_failures,
        "representative_future_pairs": representative,
        "direct_pre_post_action_snapshot": "NOT_AVAILABLE",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(tuple(args.captures))
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "artifacts" / "stage1_factor_pit").resolve()):
        raise SystemExit("Only the D5 derived-evidence directory is public")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"security_count": result["security_count"],
                      "factor_change_count": result["factor_change_count_since_2014"],
                      "future_event_pairs": result["future_event_pair_count"],
                      "failures": sum(len(result[key]) for key in (
                          "identity_failures", "unexplained_factor_changes",
                          "factor_dates_without_implemented_event", "future_event_pair_failures",
                          "late_implementation_announcements", "invalid_dividend_record_or_stock_terms"))}))


if __name__ == "__main__":
    main()
