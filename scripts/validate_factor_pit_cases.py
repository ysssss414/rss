"""Reconcile bounded SDK factors/bars to four issuer implementation notices."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.factor_pit import BACKWARD_QUANTUM, ratio_matches
from research.real_data_qualification import reconstructed_ex_reference, official_ratio_in_vendor_bounds


PRIVATE = ROOT / ".local_research_data"
OUTPUT = ROOT / "artifacts" / "stage1_factor_pit" / "corporate_action_cases.json"
CASES = (
    ("rights_600030", "600030.SH", "2022-01-18", "2022-01-27", "0", "0", "0.15", "14.43",
     "https://www.citics.com/newsite/tzzgx/ggyth/gg/aggg/202201/P020220113696056948802.pdf",
     "https://www.citics.com/newsite/news/202201/t20220127_1164678.html"),
    ("transfer_001331", "001331.SZ", "2025-06-04", "2025-06-05", "0", "0.4", "0", "0",
     "https://static.cninfo.com.cn/finalpage/2025-05-28/1223695872.PDF", None),
    ("bonus_688115", "688115.SH", "2026-07-14", "2026-07-15", "0.067", "0.4", "0", "0",
     "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-07-08/688115_20260708_3QD6.pdf",
     "https://cniis.aastocks.com/CNSESH_STOCK/2026/2026-6/2026-06-13/12391849.pdf"),
    ("cash_transfer_300059", "300059.SZ", "2023-04-17", "2023-04-18", "0.07", "0.2", "0", "0",
     "https://static.cninfo.com.cn/finalpage/2024-03-15/1219307793.PDF", None),
)


def main() -> None:
    probe = PRIVATE / "probes" / "factor_pit_case_bars_probe_20260923.json"
    main_capture = PRIVATE / "factor_pit_probe_20260923_retry"
    extra_capture = PRIVATE / "factor_pit_probe_20260923_stratified"
    for path in (probe, main_capture, extra_capture):
        if not path.resolve().is_relative_to(PRIVATE.resolve()):
            raise ValueError("Private source escaped local directory")
    bars = json.loads(probe.read_text(encoding="utf-8"))
    calls = {call["id"]: call for call in bars["calls"]}
    b = pd.concat([pd.read_hdf(directory / "backward_factor.h5", "backward_factor")
                   for directory in (main_capture, extra_capture)], axis=1)
    a = pd.concat([pd.read_hdf(directory / "single_event_factor.h5", "single_event_factor")
                   for directory in (main_capture, extra_capture)], axis=1)
    result = []
    for name, code, before, effective, cash, stock, rights, rights_price, notice, revision in CASES:
        rows = calls[name]["response"]["sample"]
        if (calls[name]["result"] != "RETURNED" or len(rows) != 2
                or [(row["code"], row["date"]) for row in rows]
                != [(code, before), (code, effective)]):
            raise ValueError("Case bar identity mismatch")
        previous_close = Decimal(str(rows[0]["close"]))
        reference = reconstructed_ex_reference(
            previous_close=previous_close, cash_per_share=Decimal(cash),
            bonus_per_share=Decimal(stock), rights_per_share=Decimal(rights),
            rights_price=Decimal(rights_price))
        prior = Decimal(str(b.loc[before, code])).quantize(BACKWARD_QUANTUM)
        after = Decimal(str(b.loc[effective, code])).quantize(BACKWARD_QUANTUM)
        single = Decimal(str(a.loc[effective, code]))
        result.append({"id": name, "security": code, "previous_trading_date": before,
                       "effective_date": effective, "official_terms": {
                           "cash_per_share": cash, "new_shares_per_share": stock,
                           "rights_per_share": rights, "rights_price": rights_price},
                       "official_issuer_or_exchange_notice": notice,
                       "revision_notice": revision,
                       "previous_raw_close": str(previous_close),
                       "official_ex_reference": str(reference),
                       "expected_backward_ratio": str(reference / previous_close),
                       "provider_backward_ratio": str(prior / after),
                       "provider_single_event_factor": str(single),
                       "official_ratio_in_six_decimal_backward_bounds": official_ratio_in_vendor_bounds(
                           previous_close=previous_close, reference=reference,
                           factor_pre=prior, factor_post=after, factor_precision=BACKWARD_QUANTUM),
                       "single_event_matches_backward": ratio_matches(Decimal(1) / single,
                                                                      prior, after)})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"schema": "stage1-factor-pit-cases/1",
                                  "private_bar_probe_sha256": sha256(probe.read_bytes()).hexdigest(),
                                  "prior_600519_supporting_cases":
                                      "artifacts/stage1_real_data_event_study/factor_qualification.json",
                                  "cases": result}, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"case_count": len(result), "all_reconcile": all(
        case["official_ratio_in_six_decimal_backward_bounds"]
        and case["single_event_matches_backward"] for case in result)}))


if __name__ == "__main__":
    main()
