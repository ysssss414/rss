"""Local-only factor replay against published company-action terms.

Never promotes same-day repeated retrieval to PIT ratio-invariance proof.
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.real_data_qualification import (
    official_ratio_in_vendor_bounds, reconstructed_ex_reference,
)


PRIVATE = ROOT / ".local_research_data"
PRECISION = Decimal("0.000001")


def validate(*, cases_path: Path, older_path: Path, newer_path: Path) -> dict[str, object]:
    paths = (cases_path, older_path, newer_path)
    if any(not path.resolve().is_relative_to(PRIVATE.resolve()) for path in paths):
        raise ValueError("Raw case inputs and factor tables must remain private")
    cases = json.loads(cases_path.read_bytes())
    if cases["schema"] != "private-official-factor-cases/1":
        raise ValueError("Unexpected case schema")
    older = pd.read_hdf(older_path, "backward_factor")
    newer = pd.read_hdf(newer_path, "backward_factor")
    common = older.index.intersection(newer.index)
    shared = older.loc[common, "600519.SH"] == newer.loc[common, "600519.SH"]
    result_cases = []
    for case in cases["cases"]:
        if case["security_id"] != "600519.SH" or date.fromisoformat(case["pre_date"]) >= date.fromisoformat(case["post_date"]):
            raise ValueError("Invalid bounded company-action case")
        reference = reconstructed_ex_reference(
            previous_close=Decimal(case["previous_raw_close"]),
            cash_per_share=Decimal(case["cash_per_share"]),
            bonus_per_share=Decimal(case["bonus_per_share"]),
            rights_per_share=Decimal(case["rights_per_share"]),
            rights_price=Decimal(case["rights_price"]),
        )
        pre = Decimal(str(newer.loc[case["pre_date"], case["security_id"]]))
        post = Decimal(str(newer.loc[case["post_date"], case["security_id"]]))
        vendor_ratio = pre / post
        reconstructed_ratio = reference / Decimal(case["previous_raw_close"])
        result_cases.append({
            "id": case["id"], "pre_date": case["pre_date"], "post_date": case["post_date"],
            "issuer_evidence": case["issuer_evidence"],
            "reconstructed_ex_reference_ratio": str(reconstructed_ratio),
            "vendor_factor_ratio": str(vendor_ratio),
            "absolute_ratio_difference": str(abs(vendor_ratio - reconstructed_ratio)),
            "matches_declared_six_decimal_factor_precision": official_ratio_in_vendor_bounds(
                previous_close=Decimal(case["previous_raw_close"]), reference=reference,
                factor_pre=pre, factor_post=post, factor_precision=PRECISION),
        })
    return {
        "schema": "stage1-real-factor-qualification/1",
        "source": "AmazingData 1.1.6 backward_factor",
        "private_case_inputs_sha256": sha256(cases_path.read_bytes()).hexdigest(),
        "private_older_factor_sha256": sha256(older_path.read_bytes()).hexdigest(),
        "private_newer_factor_sha256": sha256(newer_path.read_bytes()).hexdigest(),
        "older_dates": len(older), "newer_dates": len(newer),
        "common_dates": len(common), "absolute_factor_changed_common_dates": int((~shared).sum()),
        "cases": result_cases,
        "historical_ratio_invariance_across_later_action": "NOT_TESTED_NO_PRE_ACTION_RETRIEVAL",
        "rights_or_multiple_action_reconstruction": "NOT_TESTED",
        "qualification_status": "FACTOR_SOURCE_PARTIALLY_QUALIFIED",
        "signal_generation_admitted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--older", type=Path, required=True)
    parser.add_argument("--newer", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(cases_path=args.cases, older_path=args.older,
                              newer_path=args.newer), sort_keys=True))


if __name__ == "__main__":
    main()
