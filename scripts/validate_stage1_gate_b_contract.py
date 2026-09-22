"""Offline Gate B diagnostics. They never qualify unknown data as PIT safe."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_gate_b"
LIVE = ROOT / "artifacts" / "stage1_gate_a" / "live_core_01.json"


def load(name: str) -> dict:
    return json.loads((ART / name).read_bytes())


def live_calls() -> dict:
    return {call["id"]: call for call in json.loads(LIVE.read_bytes())["calls"]}


def sample(calls: dict, call_id: str, code: str, day: str) -> dict:
    rows = calls[call_id]["response"]["tables"][code]["sample"]
    matches = [row for row in rows if row.get("TRADE_DATE") == day.replace("-", "")
               or row.get("kline_time", "")[:10] == day]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one captured row: {call_id} {code} {day}")
    return matches[0]


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def diagnostic_limit(reference, rate, direction: int) -> Decimal:
    return (Decimal(str(reference)) * (1 + direction * Decimal(str(rate)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def admitted_asof(record: dict, decision_at: str) -> bool:
    """A minimal provenance guard for a fixture, not a historical status engine."""
    if not record.get("source_version") or not record.get("first_published_at"):
        return False
    decision = datetime.fromisoformat(decision_at)
    published = datetime.fromisoformat(record["first_published_at"])
    if decision.tzinfo is None or published.tzinfo is None:
        return False
    revised = record.get("revision_published_at")
    if revised:
        revision_time = datetime.fromisoformat(revised)
        if revision_time.tzinfo is None or revision_time > decision:
            return False
    return published <= decision


def effective_status(records: list[dict], trade_date: str, decision_at: str) -> str:
    """Validate dated fixture joins; missing or late records stay UNKNOWN."""
    active = [row["status"] for row in records
              if row["effective_from"] <= trade_date < row["effective_to_exclusive"]
              and admitted_asof(row, decision_at)]
    if len(active) > 1:
        raise ValueError("Overlapping effective status records")
    return active[0] if active else "UNKNOWN"


def validate() -> dict:
    cases, calls = load("validation_cases.json"), live_calls()
    units = load("field_unit_contract.json")
    assert units["volume"]["raw_unit"] is None and units["amount"]["raw_unit"] is None
    assert units["volume"]["conversion_multiplier"] is None and units["amount"]["conversion_multiplier"] is None
    for case in cases["unit_ratio_cases"]:
        row = sample(calls, case["call"], case["security_id"], case["date"])
        assert all(money(row[source]) == money(case[target]) for source, target in
                   (("volume", "raw_volume"), ("amount", "raw_amount"),
                    ("low", "raw_low"), ("high", "raw_high")))
        ratio = Decimal(str(row["amount"])) / Decimal(str(row["volume"]))
        assert Decimal(str(row["low"])) <= ratio <= Decimal(str(row["high"]))
    limits = []
    for case in cases["limit_cases"]:
        bar = sample(calls, case["bar_call"], case["security_id"], case["date"])
        status = sample(calls, case["status_call"], case["security_id"], case["date"])
        if case["rate"] is None:
            assert money(status["HIGH_LIMITED"]) == money(case["vendor_upper"]) == 0
            assert money(status["LOW_LIMITED"]) == money(case["vendor_lower"]) == 0
            assert Decimal(str(status["PRICE_HIGH_LMT_RATE"])) == Decimal(case["vendor_rate"])
            limits.append({"id": case["id"], "observation": "VENDOR_NO_LIMIT_SENTINEL_ONLY"})
            continue
        rate = Decimal(case["rate"])
        assert rate == Decimal(str(status["PRICE_HIGH_LMT_RATE"]))
        assert diagnostic_limit(status["PRECLOSE"], rate, 1) == money(case["upper"]) == money(status["HIGH_LIMITED"])
        assert diagnostic_limit(status["PRECLOSE"], rate, -1) == money(case["lower"]) == money(status["LOW_LIMITED"])
        assert (money(bar["close"]) == money(status["HIGH_LIMITED"])) is case["close_at_vendor_upper"]
        if "prior_raw_close" in case:
            assert money(status["PRECLOSE"]) == money(case["vendor_reference"]) != money(case["prior_raw_close"])
        limits.append({"id": case["id"], "observation": "VENDOR_FIELD_DIAGNOSTIC_MATCH"})
    st = cases["status_cases"][0]
    observed = sample(calls, st["call"], st["security_id"], st["date"])
    assert (observed["IS_ST_SEC"] == "1") != st["issuer_expected_st"]
    for fixture in cases["synthetic_effective_status"]:
        for check in fixture["assertions"]:
            assert effective_status(fixture["records"], check["date"], check["decision_at"]) == check["expected"]
    conflict = cases["conflict_case"]
    row = sample(calls, conflict["status_call"], conflict["security_id"], conflict["date"])
    assert diagnostic_limit(row["PRECLOSE"], row["PRICE_HIGH_LMT_RATE"], 1) != money(row["HIGH_LIMITED"])
    rounding, near = cases["synthetic_limit_cases"]
    assert diagnostic_limit(rounding["reference"], rounding["rate"], 1) == money(rounding["expected_upper"])
    assert money(near["high"]) == money(near["official_upper"]) and money(near["close"]) != money(near["official_upper"])
    assert admitted_asof(cases["pit_negative"], cases["pit_negative"]["decision_at"]) is False
    return {"unit_ratio_checks": len(cases["unit_ratio_cases"]), "vendor_limit_checks": len(limits),
            "status_conflict_detected": True, "rate_price_conflict_detected": True,
            "synthetic_status_checks": sum(len(f["assertions"]) for f in cases["synthetic_effective_status"]),
            "synthetic_limit_checks": len(cases["synthetic_limit_cases"]), "pit_future_leak_rejected": True,
            "gate_b": "STOP", "limits": limits}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
