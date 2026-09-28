"""Reconcile operational V2 against the 13 frozen official Gate B cases."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import duckdb

from research.close_limit_up_v2 import VERSION, evaluate_close_limit_up_v2


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
CASES = ROOT / "artifacts/stage1_gate_b_retry3/historical_validation_cases.json"
OUT = ROOT / "artifacts/stage1_close_limit_up_v2/gate_b_reconciliation.json"


def _glob(dataset: str) -> str:
    return str(SNAPSHOT / dataset / "**/*.parquet").replace("\\", "/")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if len(cases) != 13:
        raise ValueError("Gate B case inventory changed")
    db = duckdb.connect()
    audit = []
    blockers = []
    for case in cases:
        code, day = case["security_id"], case["trade_date"]
        bars = db.execute(
            f"SELECT trade_date, close, high FROM read_parquet('{_glob('daily_bars')}') "
            "WHERE security_id = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 2",
            [code, day],
        ).fetchall()
        current = bars[0] if bars and bars[0][0].isoformat() == day else None
        previous = bars[1] if current and len(bars) > 1 else None
        if current is None:
            if not case["suspended"]:
                raise ValueError(f"Missing non-suspended Gate B bar: {case['case']}")
            audit.append({"case": case["case"], "security_id": code, "trade_date": day,
                          "v1_official_close_limit_up": case["close_limit_up"],
                          "v2_status": "EXCLUDED", "v2_reason": "SUSPENSION_NO_BAR"})
            continue
        if Decimal(str(current[1])) != Decimal(str(case["close"])):
            raise ValueError(f"Snapshot/Gate B close conflict: {case['case']}")
        factor_days = [day]
        if previous:
            factor_days.append(previous[0].isoformat())
        factors = dict(db.execute(
            f"SELECT trade_date, factor FROM read_parquet('{_glob('adjustment_factor')}') "
            "WHERE security_id = ? AND trade_date IN (?, ?)",
            [code, *factor_days] if len(factor_days) == 2 else [code, day, day],
        ).fetchall())
        status = db.execute(
            f"SELECT is_xr_sec FROM read_parquet('{_glob('daily_status')}') "
            "WHERE security_id = ? AND trade_date = ?", [code, day],
        ).fetchone()
        phase = "IPO_DAY_1" if case["regime"] == "IPO_FIRST_FIVE" else "NORMAL_LISTED"
        board = {"MAIN": "SSE Main" if case["exchange"] == "SSE" else "SZSE Main",
                 "CHINEXT": "ChiNext", "STAR": "STAR"}[case["board"]]
        event = evaluate_close_limit_up_v2(
            board=board, listing_phase=phase, close=current[1], high=current[2],
            previous_raw_close=previous[1] if previous else None,
            factor=factors.get(current[0]),
            previous_factor=factors.get(previous[0]) if previous else None,
            is_xr_sec=status[0] if status else None,
            # Gate B's dated official case qualifies ordinary/no-limit phase.
            special_exception="CONFIRMED" if phase != "NORMAL_LISTED" else "CLEARED",
        )
        row = {"case": case["case"], "security_id": code, "trade_date": day,
               "board": board, "official_regime": case["regime"],
               "raw_previous_close": str(previous[1]) if previous else None,
               "raw_close": str(current[1]), "raw_high": str(current[2]),
               "official_limit_up_price": case["limit_up_price"],
               "v1_official_close_limit_up": case["close_limit_up"],
               "v2_status": event.status, "v2_reason": event.reason,
               "v2_pct_change": str(event.pct_change) if event.pct_change is not None else None,
               "reference_price_special": case["reference_origin"]["kind"] == "SSE_EX_DIVIDEND"}
        audit.append(row)
        if (case["regime"] == "NORMAL" and not row["reference_price_special"]
                and ((case["close_limit_up"] and event.status != "TRUE")
                     or (not case["close_limit_up"] and event.status == "TRUE"))):
            blockers.append(case["case"])
    result = {
        "schema": "close-limit-up-v2-gate-b-reconciliation/1",
        "status": ("BLOCKED_CLOSE_LIMIT_UP_V2_VALIDATION_FAILED" if blockers
                   else "GATE_B_RECONCILED"),
        "v2_contract": VERSION,
        "snapshot_id": "REAL_RESEARCH_SNAPSHOT_V1",
        "snapshot_manifest_sha256": _sha(SNAPSHOT / "metadata/manifest.json"),
        "gate_b_cases_sha256": _sha(CASES),
        "case_count": len(cases),
        "ordinary_10pct_disagreements": blockers,
        "cause": "An official 10% upper rounded to a 0.01-CNY tick can imply a raw return below the fixed 9.91% V2 floor.",
        "cases": audit,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "case_count": len(cases),
                      "ordinary_10pct_disagreements": blockers}, indent=2))


if __name__ == "__main__":
    main()
