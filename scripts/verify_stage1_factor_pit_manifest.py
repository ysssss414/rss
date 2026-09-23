"""Verify the public D5 qualification receipt without reading private vendor rows."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "stage1_factor_pit"
FILES = (
    "artifacts/stage1_factor_pit/baseline_receipt.json",
    "artifacts/stage1_factor_pit/factor_api_contract.json",
    "artifacts/stage1_factor_pit/provider_validation.json",
    "artifacts/stage1_factor_pit/corporate_action_cases.json",
    "artifacts/stage1_factor_pit/future_invariance_cases.json",
    "artifacts/stage1_factor_pit/revision_semantics.json",
    "artifacts/stage1_factor_pit/rsi_real_adapter_smoke.json",
    "docs/stage1_factor_pit_qualification.md",
    "research/factor_pit.py",
    "scripts/capture_factor_pit_probe.py",
    "scripts/capture_factor_pit_rsi_smoke.py",
    "scripts/validate_factor_pit_probe.py",
    "scripts/validate_factor_pit_cases.py",
    "scripts/validate_factor_pit_rsi_smoke.py",
    "scripts/verify_stage1_factor_pit_manifest.py",
    "tests/test_factor_pit.py",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def text_digest(path: Path) -> str:
    # Git may check out these public text files as CRLF on Windows.
    return sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def checked_manifest() -> dict:
    provider = load("provider_validation.json")
    cases = load("corporate_action_cases.json")
    future = load("future_invariance_cases.json")
    revisions = load("revision_semantics.json")
    rsi = load("rsi_real_adapter_smoke.json")
    previous = json.loads((ROOT / "artifacts" / "stage1_real_data_event_study"
                           / "factor_qualification.json").read_text(encoding="utf-8"))
    changed = {(item["security"], item["effective"])
               for item in provider["changed_implemented_cases"]}
    registered = {(item["security"], item["effective_date"])
                  for item in revisions["other_vendor_changed_implemented_dates"]}
    verified = revisions["verified_changed_plan_case"]
    registered.add((verified["security"], verified["effective_date"]))
    if (provider["security_count"] != 8 or provider["factor_change_count_since_2014"] != 100
            or changed != registered or len(changed) != 8
            or sum(provider["dividend_event_types_implemented_through_cutoff"].values()) != 99
            or len(provider["rights_semantics"]) != 1
            or any(provider[key] for key in ("identity_failures", "unexplained_factor_changes",
                                             "factor_dates_without_implemented_event",
                                             "future_event_pair_failures",
                                             "late_implementation_announcements",
                                             "invalid_dividend_record_or_stock_terms"))
            or provider["future_event_pair_count"] != future["provider_internal_pairs_with_future_action"]
            or provider["multiple_future_event_pair_count"] != future["pairs_with_two_or_more_future_actions"]
            or len(cases["cases"]) != 4
            or not all(case["official_ratio_in_six_decimal_backward_bounds"]
                       and case["single_event_matches_backward"] for case in cases["cases"])
            or len(previous["reconstruction_cases"]) != 2
            or not all(case["within_six_decimal_factor_rounding_bounds"]
                       for case in previous["reconstruction_cases"])
            or revisions["verified_changed_plan_case"]["status"] != "RESOLVED_FINAL_IMPLEMENTATION"
            or not all(item["status"] == "UNRESOLVED_FOR_FUTURE_MATERIALIZATION"
                       for item in revisions["other_vendor_changed_implemented_dates"])
            or rsi["status"] != "PASS_ONE_SECURITY_DIAGNOSTIC" or rsi["bars"] != 660
            or any(item["primitive_status"] != "READY" for item in rsi["rsi"].values())):
        raise ValueError("D5 qualification evidence fails closed")
    protected = load("baseline_receipt.json")["protected_excel_before_sha256"]
    after = {name: digest(ROOT / name) for name in protected}
    if after != protected:
        raise ValueError("Protected Excel SHA changed")
    return {
        "schema": "stage1-factor-pit-qualification-manifest/1",
        "FACTOR_SOURCE_STATUS": "QUALIFIED",
        "qualified_contract": "HISTORICAL_FACTOR_RATIO_V1",
        "price_basis": "PIT_ADJUSTED_CLOSE_V1",
        "cache_contract": "PIT_FACTOR_RATIO_SNAPSHOT_V1",
        "scope": "event-derived, independently vetted security/date prefixes only",
        "unresolved_revision_dates_fail_closed": len(revisions["other_vendor_changed_implemented_dates"]),
        "real_entry_or_event_study_admitted": False,
        "protected_excel_after_sha256": after,
        "public_file_sha256": {name: text_digest(ROOT / name) for name in FILES},
        "next_action": "After HUMAN_USER review only: bounded D1/D3/D4/D6/D7/D8 materialization",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = checked_manifest()
    path = ARTIFACTS / "qualification_manifest.json"
    if args.write:
        path.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8")
    elif json.loads(path.read_text(encoding="utf-8")) != expected:
        raise ValueError("D5 manifest is stale or altered")
    print(json.dumps({"status": expected["FACTOR_SOURCE_STATUS"],
                      "public_files": len(expected["public_file_sha256"]),
                      "protected_excel_unchanged": True}))


if __name__ == "__main__":
    main()
