"""Offline Retry 1 evidence guards. Unknown source semantics remain inadmissible."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_gate_b_retry1"
SAFE_STATES = {"PIT_SAFE_RAW", "PIT_SAFE_DERIVED", "PIT_SAFE_ASOF_VERIFIED"}


def load(name: str) -> dict:
    return json.loads((ART / name).read_bytes())


def timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def admitted(source: dict, decision_at: str, target_date: str | None = None) -> bool:
    decision = timestamp(decision_at)
    published = timestamp(source.get("available_at"))
    if source.get("admission_state") not in SAFE_STATES or not decision or not published:
        return False
    if not source.get("historical_version") or source.get("revision_state") != "FROZEN_VERSION":
        return False
    if published > decision:
        return False
    if target_date and source.get("effective_from"):
        return source["effective_from"] <= target_date
    return True


def effective_status(events: list[dict], trade_date: str, decision_at: str) -> str:
    """Use an official visible interval; conflicting equal-priority rows fail closed."""
    active = [event for event in events
              if event["effective_from"] <= trade_date
              and (event.get("effective_to") is None or trade_date < event["effective_to"])
              and admitted(event, decision_at, trade_date)]
    if not active:
        return "UNKNOWN"
    best = min(event["source_priority"] for event in active)
    winners = [event for event in active if event["source_priority"] == best]
    return winners[0]["status_type"] if len(winners) == 1 else "UNKNOWN"


def select_rule(rules: list[dict], exchange: str, board: str, status: str, day: str) -> dict | None:
    selected = [rule for rule in rules if rule["exchange"] == exchange
                and rule["board"] == board and rule["security_status"] == status
                and rule["effective_from"] <= day
                and (rule.get("effective_to") is None or day < rule["effective_to"])]
    return selected[0] if len(selected) == 1 else None


def normalize_limit(*, official_no_limit: bool | None, official_upper: str | None,
                    official_lower: str | None, vendor_upper: str | None = None,
                    vendor_rate: str | None = None) -> dict | None:
    """Only official no-limit/price evidence can authorize a normalized result."""
    if official_no_limit is True:
        if official_upper is not None or official_lower is not None:
            return None
        return {"is_limit_applicable": False, "limit_rate": None,
                "limit_up_price": None, "limit_down_price": None}
    if official_no_limit is not False or official_upper is None or official_lower is None:
        return None
    upper, lower = Decimal(official_upper), Decimal(official_lower)
    if upper <= 0 or lower <= 0 or lower >= upper:
        return None
    return {"is_limit_applicable": True, "limit_up_price": str(upper),
            "limit_down_price": str(lower)}


def diagnostic_round(reference: str, rate: str, tick: str = "0.01") -> str:
    return str((Decimal(reference) * (1 + Decimal(rate))).quantize(
        Decimal(tick), rounding=ROUND_HALF_UP))


def validate() -> dict:
    units = load("unit_reconciliation.json")
    assert units["provider_version"] == "AmazingData 1.1.6"
    assert units["volume"]["raw_unit"] is None and units["amount"]["raw_unit"] is None
    assert units["external_exact_reconciliations"] == []
    status = load("historical_status_reconciliation.json")
    conflict = status["600518_conflict"]
    assert conflict["official_effective_from"] == "2024-07-04"
    assert conflict["vendor_is_st_on_effective_date"] == "1"
    assert effective_status(status["validated_events"], "2024-07-04",
                            "2024-07-04T15:00:00+08:00") == "NORMAL"
    assert effective_status(status["validated_events"], "2024-07-03",
                            "2024-07-03T15:00:00+08:00") == "UNKNOWN"
    rules = load("limit_price_rule_registry.json")["rules"]
    for day, expected in (("2026-07-03", "0.05"), ("2026-07-06", "0.10"),
                          ("2026-07-07", "0.10")):
        assert select_rule(rules, "SSE", "MAIN", "RISK_WARNING", day)["limit_rate"] == expected
    assert normalize_limit(official_no_limit=None, official_upper=None, official_lower=None,
                           vendor_upper="0", vendor_rate="999") is None
    assert normalize_limit(official_no_limit=True, official_upper=None, official_lower=None) == {
        "is_limit_applicable": False, "limit_rate": None,
        "limit_up_price": None, "limit_down_price": None}
    pit = load("pit_admission_registry.json")
    for fixture in pit["real_source_fixtures"]:
        assert admitted(fixture["source"], fixture["decision_at"], fixture["target_date"]) is fixture["expected_admission"]
    return {"B1": "OPEN", "B2": "OPEN", "B3": "OPEN", "B4": "OPEN",
            "gate_b": "GATE_B_STOP", "real_source_fixtures": len(pit["real_source_fixtures"])}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
