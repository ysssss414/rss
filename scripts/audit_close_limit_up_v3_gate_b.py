"""Reconcile fixed-point V3 with all frozen Gate B official cases."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import duckdb

from research.close_limit_up_v3 import VERSION, evaluate_close_limit_up_v3


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
GATE_B = ROOT / "artifacts/stage1_gate_b_retry3/historical_validation_cases.json"
V2 = ROOT / "artifacts/stage1_close_limit_up_v2/gate_b_reconciliation.json"
OUT = ROOT / "artifacts/stage1_close_limit_up_v3/gate_b_reconciliation.json"


def _parquet(name: str) -> str:
    return str(V1 / name / "**/*.parquet").replace("\\", "/")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = json.loads(GATE_B.read_text(encoding="utf-8"))["cases"]
    old = json.loads(V2.read_text(encoding="utf-8"))
    if len(cases) != 13 or len(old["cases"]) != 13:
        raise ValueError("Frozen Gate B case count changed")
    previous = {row["case"]: row for row in old["cases"]}
    db = duckdb.connect()
    rows, conflicts = [], []
    for case in cases:
        v2 = previous[case["case"]]
        code, day = case["security_id"], case["trade_date"]
        if case["suspended"]:
            rows.append({"case": case["case"], "security_id": code, "trade_date": day,
                         "official_regime": case["regime"], "v1_result": False,
                         "v2_result": v2["v2_status"], "v3_result": "EXCLUDED",
                         "reference_day_class": "OTHER_SPECIAL", "explanation": "SUSPENSION_NO_BAR"})
            continue
        bars = db.execute(
            f"SELECT trade_date, close, high FROM read_parquet('{_parquet('daily_bars')}') "
            "WHERE security_id = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 2",
            [code, day],
        ).fetchall()
        if not bars or bars[0][0].isoformat() != day:
            raise ValueError(f"Missing Gate B current bar: {case['case']}")
        current, prior = bars[0], bars[1] if len(bars) > 1 else None
        factors = dict(db.execute(
            f"SELECT trade_date, factor FROM read_parquet('{_parquet('adjustment_factor')}') "
            "WHERE security_id = ? AND trade_date IN (?, ?)",
            [code, day, prior[0].isoformat() if prior else day],
        ).fetchall())
        status = db.execute(
            f"SELECT is_xr_sec, is_wd_sec FROM read_parquet('{_parquet('daily_status')}') "
            "WHERE security_id = ? AND trade_date = ?", [code, day],
        ).fetchone()
        board = {"MAIN": "SSE Main" if case["exchange"] == "SSE" else "SZSE Main",
                 "CHINEXT": "ChiNext", "STAR": "STAR"}[case["board"]]
        phase = "IPO_DAY_1" if case["regime"] == "IPO_FIRST_FIVE" else "NORMAL_LISTED"
        event = evaluate_close_limit_up_v3(
            board=board, listing_phase=phase, close=current[1], high=current[2],
            previous_raw_close=prior[1] if prior else None, factor=factors.get(current[0]),
            previous_factor=factors.get(prior[0]) if prior else None,
            is_xr_sec=status[0] if status else None,
            is_wd_sec=status[1] if status else None,
            # The frozen case's dated official rule and issuer/exchange evidence
            # clear the phase here; this is not a market-wide vendor inference.
            special_exception="CONFIRMED" if phase != "NORMAL_LISTED" else "CLEARED",
            price_tick=case["price_tick"],
        )
        expected = ("EXCLUDED" if phase != "NORMAL_LISTED" or board == "ChiNext"
                    else "UNRESOLVED" if case["reference_origin"]["kind"] == "SSE_EX_DIVIDEND"
                    else "TRUE" if (case["close_limit_up"] and
                                   (case["regime"] == "NORMAL" or
                                    case["trade_date"] >= "2026-07-06"))
                    else "FALSE")
        row = {"case": case["case"], "security_id": code, "trade_date": day,
               "official_regime": case["regime"], "official_limit_up_price": case["limit_up_price"],
               "v1_result": case["close_limit_up"], "v2_result": v2["v2_status"],
               "v3_result": event.status, "expected_v3": expected,
               "reference_day_class": event.reference_day.classification,
               "previous_raw_close": str(prior[1]) if prior else None, "raw_close": str(current[1]),
               "raw_high": str(current[2]),
               "v3_calculated_limit_up_price": (str(Decimal(event.canonical_limit_up_cents) / 100)
                                                if event.canonical_limit_up_cents is not None else None),
               "explanation": event.reason}
        rows.append(row)
        if event.status != expected:
            conflicts.append(case["case"])
    result = {
        "schema": "close-limit-up-v3-gate-b-reconciliation/1",
        "status": "GATE_B_RECONCILED" if not conflicts else "BLOCKED_CLOSE_LIMIT_UP_V3_VALIDATION_FAILED",
        "v3_contract": VERSION,
        "snapshot_manifest_sha256": _sha(V1 / "metadata/manifest.json"),
        "gate_b_cases_sha256": _sha(GATE_B),
        "v2_reconciliation_sha256": _sha(V2),
        "case_count": len(rows), "ordinary_case_conflicts": conflicts,
        "cases": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "case_count": len(rows),
                      "conflicts": conflicts, "v3_600518": next(row["v3_result"] for row in rows
                            if row["case"] == "sse_risk_removal_2024_07_04")}, indent=2))


if __name__ == "__main__":
    main()
