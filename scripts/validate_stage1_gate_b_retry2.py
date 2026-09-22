"""Offline Gate B Retry 2 qualification guards; no strategy or provider calls."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_gate_b_retry2"
CANDIDATES = (1, 10, 100, 1000, 10000)


def load(name: str) -> dict:
    return json.loads((ART / name).read_bytes())


def matching_multipliers(row: dict) -> dict[str, list[int]]:
    """Compare raw Kline fields independently to exchange display precision."""
    volume = Decimal(str(row["vendor_volume"]))
    amount = Decimal(str(row["vendor_amount"]))
    official_volume = Decimal(str(row["official_volume"]).replace(",", ""))
    official_amount = Decimal(str(row["official_amount"]).replace(",", ""))
    if row["exchange"] == "SSE":
        return {
            "volume": [m for m in CANDIDATES if volume * m == official_volume],
            "amount": [m for m in CANDIDATES if abs(amount * m - official_amount) <= Decimal("0.50")],
        }
    # SZSE report prints 0.01 of 万股/万元; its footer covers all trading methods.
    def displayed(value: Decimal) -> Decimal:
        return (value / Decimal(10000)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return {
        "volume": [m for m in CANDIDATES if displayed(volume * m) == official_volume],
        "amount": [m for m in CANDIDATES if displayed(amount * m) == official_amount],
    }


def normalize_constraint(raw: dict, authority: dict | None) -> dict | None:
    """Admit a daily constraint only with confirmed source and coherent prices."""
    if not authority or authority.get("state") != "VERIFIED":
        return None
    required = ("security_id", "trade_date", "reference_price", "price_tick",
                "trading_allowed", "suspended", "source", "source_priority", "retrieved_at")
    if any(raw.get(key) is None for key in required):
        return None
    if raw["trading_allowed"] == raw["suspended"]:
        return None
    try:
        reference = Decimal(str(raw["reference_price"]))
        tick = Decimal(str(raw["price_tick"]))
        retrieved = datetime.fromisoformat(raw["retrieved_at"])
    except (InvalidOperation, ValueError, TypeError):
        return None
    if retrieved.tzinfo is None:
        return None
    if reference <= 0 or tick <= 0:
        return None
    if authority["limit_applicable"] is False:
        if authority.get("limit_type") != "NONE":
            return None
        up = down = None
    elif authority["limit_applicable"] is True:
        if authority.get("limit_type") not in {"RATE", "ABSOLUTE"}:
            return None
        try:
            up = Decimal(str(raw["limit_up_price"]))
            down = Decimal(str(raw["limit_down_price"]))
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return None
        if down <= 0 or not down < reference < up:
            return None
        if up % tick or down % tick or reference % tick:
            return None
        if (up - reference) / reference > Decimal("0.30") or (reference - down) / reference > Decimal("0.30"):
            return None
        # Official row or independently qualified vendor prices must match the authority.
        if str(up) != str(Decimal(str(authority["limit_up_price"]))) or str(down) != str(Decimal(str(authority["limit_down_price"]))):
            return None
    else:
        return None
    return {
        "security_id": raw["security_id"], "trade_date": raw["trade_date"],
        "reference_price": str(reference), "limit_applicable": authority["limit_applicable"],
        "limit_type": authority["limit_type"],
        "limit_up_price": str(up) if up is not None else None,
        "limit_down_price": str(down) if down is not None else None,
        "price_tick": str(tick), "trading_allowed": raw["trading_allowed"],
        "suspended": raw["suspended"], "source": raw["source"],
        "source_priority": raw["source_priority"], "retrieved_at": raw["retrieved_at"],
        "raw_provider_value": raw.get("raw_provider_value"),
    }


def select_preopen_version(rows: list[dict], session_open: str) -> dict | None:
    cutoff = datetime.fromisoformat(session_open)
    if cutoff.tzinfo is None:
        return None
    valid = [r for r in rows if (t := datetime.fromisoformat(r["published_at"])).tzinfo
             and t <= cutoff and r["trade_date"] == cutoff.date().isoformat()]
    if not valid:
        return None
    valid.sort(key=lambda r: (r["source_priority"], -datetime.fromisoformat(r["published_at"]).timestamp()))
    best = valid[0]
    tied = [r for r in valid if r["source_priority"] == best["source_priority"]
            and r["published_at"] == best["published_at"]]
    return best if len(tied) == 1 else None


def input_available_at(item: dict) -> datetime | None:
    kind = item.get("availability_class")
    if kind == "DERIVED_EOD":
        times = [input_available_at(source) for source in item.get("inputs", [])]
        return max(times) if times and all(times) else None
    if kind not in {"PRE_OPEN_KNOWN", "INTRADAY_OBSERVED", "EOD_FINALIZED"}:
        return None
    if item.get("source_type") == "CONTEXT_UNVERIFIED":
        return None
    field = {"PRE_OPEN_KNOWN": "published_at", "INTRADAY_OBSERVED": "observed_at",
             "EOD_FINALIZED": "finalized_at"}[kind]
    if kind == "EOD_FINALIZED" and any(not item.get(key) for key in
                                        ("retrieved_at", "provider_version", "request_hash", "content_sha256")):
        return None
    try:
        value = datetime.fromisoformat(item[field])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value.tzinfo else None


def causal_admitted(item: dict, decision_at: str) -> bool:
    try:
        decision = datetime.fromisoformat(decision_at)
    except ValueError:
        return False
    available = input_available_at(item)
    return bool(decision.tzinfo and available and available <= decision)


def verify_manifest() -> dict[str, bool]:
    manifest = load("qualification_manifest.json")
    result = {}
    for path, expected in manifest["evidence_hashes"].items():
        try:
            data = subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            data = subprocess.check_output(["git", "show", f":{path}"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL)
        result[path] = hashlib.sha256(data).hexdigest() == expected
    return result


def validate() -> dict:
    units = load("market_unit_reconciliation.json")
    matched = [r for r in units["samples"] if r["reconciliation"] == "MATCH"]
    for row in matched:
        assert matching_multipliers(row) == {"volume": [1], "amount": [1]}, row["id"]
    assert sum(r["exchange"] == "SSE" for r in matched) >= 3
    assert sum(r["exchange"] == "SZSE" for r in matched) >= 3
    assert units["provider_version"] == "1.1.6"
    assert load("core_data_dependency.json")["core_gates"] == ["C1", "C2", "C3"]
    matrix = load("daily_constraint_validation.json")
    assert {r["case"] for r in matrix["cases"]} >= {
        "normal_10", "risk_warning_5", "risk_warning_10_2026", "twenty_percent",
        "ipo_no_limit", "ex_dividend", "risk_warning_removal", "risk_warning_introduction",
        "suspension", "near_limit", "actual_close_limit"}
    assert matrix["core_status"] == "OPEN"
    assert load("auxiliary_status_quality.json")["causal_admission"] == "PROHIBITED"
    integrity = verify_manifest()
    assert all(integrity.values()), [p for p, ok in integrity.items() if not ok]
    return {"C1": "CLOSED", "C2": "OPEN", "C3": "CLOSED", "A1": "UNTRUSTED",
            "gate_b": "GATE_B_STOP", "manifest_hashes": len(integrity)}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
