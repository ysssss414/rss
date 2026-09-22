"""Offline, exchange-specific Gate B C2 evidence qualification; no strategy calls."""
from __future__ import annotations

import hashlib
import json
import subprocess
import argparse
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_gate_b_retry3"


def load(name: str) -> dict:
    return json.loads((ART / name).read_bytes())


def number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite price")
    return result


def select_rule(rules: list[dict], exchange: str, board: str, regime: str, day: str) -> dict:
    found = [r for r in rules if (r["exchange"], r["board"], r["regime"]) ==
             (exchange, board, regime) and r["effective_from"] <= day and
             (r["effective_to"] is None or day <= r["effective_to"])]
    if len(found) != 1:
        raise ValueError("missing or overlapping historical rule")
    return found[0]


def tick_round(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0 or value <= 0:
        raise ValueError("invalid price or tick")
    return (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick


def rate_limits(reference: object, rate: object, tick: object) -> tuple[Decimal, Decimal]:
    ref, pct, step = number(reference), number(rate), number(tick)
    if ref <= 0 or not 0 < pct < 1 or step <= 0 or ref % step:
        raise ValueError("invalid rate-mode input")
    up = tick_round(ref * (1 + pct), step)
    down = tick_round(ref * (1 - pct), step)
    # Both exchanges' cited trading rules require at least one price tick.
    down = min(down, ref - step)
    if down <= 0:
        raise ValueError("nonpositive minimum-tick lower price")
    return max(up, ref + step), down


def szse_cashauctionparams(row: dict, reference: object) -> tuple[Decimal | None, Decimal | None]:
    """Interpret the *actual* interface mode, never its display-only rate-mode absolutes."""
    if row.get("HasPriceLimit") == "N":
        return None, None
    if row.get("HasPriceLimit") != "Y" or row.get("ReferPriceType") != "1":
        raise ValueError("unknown SZSE limit/reference mode")
    ref, tick = number(reference), number(row["PriceTick"])
    if ref <= 0 or tick <= 0 or ref % tick:
        raise ValueError("invalid SZSE reference/tick")
    if row.get("LimitType") == "1":
        up_rate, down_rate = number(row["LimitUpRate"]), number(row["LimitDownRate"])
        if not 0 < up_rate < 1 or not 0 < down_rate < 1:
            raise ValueError("invalid SZSE rates")
        up = max(tick_round(ref * (1 + up_rate), tick), ref + tick)
        down = min(tick_round(ref * (1 - down_rate), tick), ref - tick)
        return up, down
    if row.get("LimitType") == "2":
        # The interface names these fields upper/lower limit *prices*.
        up, down = number(row["LimitUpAbsolute"]), number(row["LimitDownAbsolute"])
        if not 0 < down < ref < up or up % tick or down % tick:
            raise ValueError("invalid SZSE absolute limit prices")
        return up, down
    raise ValueError("unknown SZSE LimitType")


def close_limit_up(close: object | None, upper: Decimal | None, tick: object,
                   trading_allowed: bool) -> bool:
    if close is None or upper is None or not trading_allowed:
        return False
    price, step = number(close), number(tick)
    if step <= 0 or price % step or upper % step:
        raise ValueError("off-tick close or limit")
    return int(price / step) == int(upper / step)


def private_vendor_row(capture: dict, case: dict) -> dict:
    """Used only when an authorized reviewer supplies the private capture."""
    target = case["trade_date"].replace("-", "")
    rows = [sample for call in capture["calls"] if call.get("api") == "history_status"
            for sample in call.get("response", {}).get("tables", {}).get(case["security_id"],
                                                                            {}).get("sample", [])
            if sample["TRADE_DATE"] == target]
    if len(rows) != 1:
        raise ValueError("missing or duplicate frozen vendor status")
    return rows[0]


def private_comparison(case: dict, rule: dict, reference: Decimal,
                       upper: Decimal | None, lower: Decimal | None, vendor: dict) -> dict:
    if upper is None:
        absolute_match = rate_match = None
        sentinel = (number(vendor["HIGH_LIMITED"]) == number(vendor["LOW_LIMITED"]) == 0 and
                    number(vendor["PRICE_HIGH_LMT_RATE"]) ==
                    number(vendor["PRICE_LOW_LMT_RATE"]) == 999)
    else:
        absolute_match = (number(vendor["HIGH_LIMITED"]) == upper and
                          number(vendor["LOW_LIMITED"]) == lower)
        rate_match = (number(vendor["PRICE_HIGH_LMT_RATE"]) == number(rule["limit_rate"]) and
                      number(vendor["PRICE_LOW_LMT_RATE"]) == number(rule["limit_rate"]))
        sentinel = None
    return {"reference_matches_official": number(vendor["PRECLOSE"]) == reference,
            "absolute_matches_reconstruction": absolute_match,
            "rate_matches_rule": rate_match,
            "status_label_matches_regime": (vendor["IS_ST_SEC"] == "1") ==
                                           (case["regime"] == "RISK_WARNING"),
            "suspension_matches_official": (vendor["IS_SUSP_SEC"] == "1") ==
                                           case["suspended"],
            "no_limit_sentinel_normalized": sentinel}


def official_reference(case: dict) -> Decimal:
    origin = case["reference_origin"]
    if origin["kind"] == "SSE_PREVIOUS_CLOSE":
        data = json.loads((ROOT / origin["path"]).read_bytes())
        rows = [r for r in data["kline"] if str(r[0]) == origin["date"].replace("-", "")]
        if len(rows) != 1:
            raise ValueError("missing official SSE previous close")
        return number(rows[0][4])
    if origin["kind"] == "SZSE_QSS":
        data = json.loads((ROOT / origin["path"]).read_bytes())
        rows = [r for block in data for r in block["data"] if r["jyrq"] == case["trade_date"]]
        if len(rows) != 1:
            raise ValueError("missing official SZSE qss")
        return number(rows[0]["qss"].replace(",", ""))
    if origin["kind"] == "SSE_EX_DIVIDEND":
        raw = official_reference({"reference_origin": {"kind": "SSE_PREVIOUS_CLOSE",
                                                 "path": origin["path"], "date": origin["date"]}})
        if raw != number(origin["raw_prior_close"]):
            raise ValueError("dividend raw previous close mismatch")
        return tick_round(raw - number(origin["cash_dividend_per_share"]),
                          number(case["price_tick"]))
    if origin["kind"] == "ISSUER_IPO_PRICE":
        # Used solely as a recorded reference for a no-limit IPO day.
        return number(origin["issue_price"])
    raise ValueError("unknown official reference basis")


def official_close(case: dict) -> Decimal | None:
    path = case["official_close_path"]
    data = json.loads((ROOT / path).read_bytes())
    if case["exchange"] == "SSE":
        rows = [r for r in data["kline"] if str(r[0]) == case["trade_date"].replace("-", "")]
        if case["suspended"]:
            if rows:
                raise ValueError("official bar on suspended day")
            return None
        if len(rows) != 1:
            raise ValueError("missing official SSE close")
        return number(rows[0][4])
    rows = [r for block in data for r in block["data"] if r["jyrq"] == case["trade_date"]]
    if len(rows) != 1:
        raise ValueError("missing official SZSE close")
    return number(rows[0]["ss"].replace(",", ""))


def validate_case(case: dict, rules: list[dict]) -> dict:
    rule = select_rule(rules, case["exchange"], case["board"], case["regime"],
                       case["trade_date"])
    reference = official_reference(case)
    if reference != number(case["reference_price"]) or reference <= 0:
        raise ValueError("official reference mismatch")
    close = official_close(case)
    if close != (number(case["close"]) if case["close"] is not None else None):
        raise ValueError("official close mismatch")
    if case.get("source_type") != "PRIVATE_LOCAL_EVIDENCE" or case.get("public_payload") is not False:
        raise ValueError("private vendor provenance missing")
    if not re.fullmatch(r"[0-9a-f]{64}", case.get("private_source_sha256", "")):
        raise ValueError("private vendor source hash missing")
    comparison = case["vendor_comparison"]
    if comparison.get("reference_matches_official") is not True:
        raise ValueError("vendor reference mismatch")
    if comparison.get("suspension_matches_official") is not True:
        raise ValueError("suspension mismatch")
    if not isinstance(comparison.get("status_label_matches_regime"), bool):
        raise ValueError("invalid status-label comparison")
    if rule["limit_mode"] == "NONE":
        upper = lower = None
        if (comparison.get("no_limit_sentinel_normalized") is not True or
                comparison.get("absolute_matches_reconstruction") is not None or
                comparison.get("rate_matches_rule") is not None):
            raise ValueError("unrecognized no-limit sentinel")
    elif rule["limit_mode"] == "RATE":
        upper, lower = rate_limits(reference, rule["limit_rate"], rule["price_tick"])
        if comparison.get("absolute_matches_reconstruction") is not True:
            raise ValueError("vendor absolute prices disagree with official reconstruction")
        if not isinstance(comparison.get("rate_matches_rule"), bool):
            raise ValueError("invalid vendor rate comparison")
        if comparison.get("no_limit_sentinel_normalized") is not None:
            raise ValueError("invalid limited-mode sentinel")
    else:
        raise ValueError("unsupported rule mode")
    if upper != (number(case["limit_up_price"]) if case["limit_up_price"] is not None else None):
        raise ValueError("recorded upper price mismatch")
    if lower != (number(case["limit_down_price"]) if case["limit_down_price"] is not None else None):
        raise ValueError("recorded lower price mismatch")
    if case["suspended"] and case["close"] is not None:
        raise ValueError("suspended day has a close")
    is_limit_close = close_limit_up(case["close"], upper, rule["price_tick"],
                                    not case["suspended"])
    if is_limit_close != case["close_limit_up"]:
        raise ValueError("recorded close-limit result mismatch")
    return {"case": case["case"], "rule_id": rule["id"], "reference_price": str(reference),
            "limit_applicable": upper is not None, "limit_up_price": str(upper) if upper else None,
            "limit_down_price": str(lower) if lower else None, "close_limit_up": is_limit_close,
            "trading_allowed": not case["suspended"], "suspended": case["suspended"],
            "vendor_rate_admission": "NOT_APPLICABLE" if upper is None else
            ("QUALIFIED" if comparison["rate_matches_rule"] else "UNTRUSTED"),
            "vendor_absolute_admission": "NORMALIZE_TO_NULL" if upper is None
            else ("QUALIFIED" if case["exchange"] == "SSE" else "CORROBORATING_ONLY"),
            "vendor_status_label_admission": "CONSISTENT_ONLY" if
            comparison["status_label_matches_regime"] else "UNTRUSTED"}


def qualify_security_day(case: dict, rules: list[dict]) -> dict:
    """Fail closed with a structured reason; never admit an ambiguous day."""
    try:
        result = validate_case(case, rules)
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        reason = str(error)
        code = ("REFERENCE_MISMATCH" if "reference mismatch" in reason else
                "UNKNOWN_RULE_VERSION" if "historical rule" in reason else
                "INVALID_SENTINEL" if "sentinel" in reason else
                "VENDOR_CONFLICT" if "vendor" in reason else
                "INVALID_CONSTRAINT")
        return {"validation_status": "INVALID", "causal_admitted": False,
                "reason": {"code": code, "detail": reason}}
    admitted = result["trading_allowed"]
    return {**result, "validation_status": "VALID", "causal_admitted": admitted,
            "reason": None if admitted else {"code": "SUSPENDED", "detail": "not tradable"}}


def verify_manifest() -> dict[str, bool]:
    result = {}
    for path, expected in load("qualification_manifest.json")["evidence_hashes"].items():
        try:
            data = subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            data = subprocess.check_output(["git", "show", f":{path}"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL)
        result[path] = hashlib.sha256(data).hexdigest() == expected
    return result


def verify_private_sources(paths: list[Path]) -> int:
    """Optional local replay; paths and provider payloads are never serialized publicly."""
    registry = load("source_registry.json")
    expected = {source["source_hash"] for source in registry["private_sources"]}
    captures = {}
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in expected or digest in captures:
            raise ValueError("unexpected or duplicate private source")
        captures[digest] = json.loads(raw)
    if set(captures) != expected:
        raise ValueError("private source hash missing")
    rules = load("exchange_constraint_semantics.json")["rules"]
    cases = load("historical_validation_cases.json")["cases"]
    for case in cases:
        rule = select_rule(rules, case["exchange"], case["board"], case["regime"],
                           case["trade_date"])
        reference = official_reference(case)
        upper, lower = (None, None) if rule["limit_mode"] == "NONE" else rate_limits(
            reference, rule["limit_rate"], rule["price_tick"])
        vendor = private_vendor_row(captures[case["private_source_sha256"]], case)
        if private_comparison(case, rule, reference, upper, lower, vendor) != case["vendor_comparison"]:
            raise ValueError("private vendor comparison does not match public receipt")
    return len(cases)


def validate() -> dict:
    registry = load("source_registry.json")
    for source in registry["frozen_bytes"]:
        if hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("frozen source byte mismatch: " + source["path"])
    private_hashes = [source["source_hash"] for source in registry["private_sources"]]
    if len(set(private_hashes)) != len(private_hashes) or any(
            source.get("source_type") != "PRIVATE_LOCAL_EVIDENCE" or
            source.get("public_payload") is not False or
            not re.fullmatch(r"[0-9a-f]{64}", source["source_hash"])
            for source in registry["private_sources"]):
        raise ValueError("invalid private source receipts")
    rules = load("exchange_constraint_semantics.json")["rules"]
    cases = load("historical_validation_cases.json")["cases"]
    if any(case["private_source_sha256"] not in private_hashes for case in cases):
        raise ValueError("case lacks private source receipt")
    results = [validate_case(case, rules) for case in cases]
    if load("vendor_field_admission.json")["case_results"] != results:
        raise ValueError("vendor admission results drifted from frozen cases")
    contract = load("daily_constraint_contract.json")
    if (contract["C1"], contract["C2"], contract["C3"], contract["A1"]) != (
            "CLOSED", "CLOSED", "CLOSED", "UNTRUSTED"):
        raise ValueError("frozen Gate B state changed")
    required = {"sse_normal_10", "sse_risk_5", "sse_risk_10_2026", "sse_risk_intro_pre",
                "sse_risk_intro_post", "sse_risk_removal", "sse_ipo_no_limit", "sse_exdiv",
                "szse_main_10", "szse_chinext_20", "szse_ipo_no_limit", "suspension",
                "near_limit", "actual_close_limit"}
    if not required <= {label for row in cases for label in row["coverage"]}:
        raise ValueError("C2 representative coverage incomplete")
    if not all(verify_manifest().values()):
        raise ValueError("Retry 3 evidence hash mismatch")
    return {"C1": "CLOSED", "C2": "CLOSED", "C3": "CLOSED", "A1": "UNTRUSTED",
            "gate_b": "GATE_B_CORE_DATA_CONTRACT_PASS", "cases": len(results),
            "manifest_hashes": len(verify_manifest())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-capture", action="append", type=Path, default=[])
    args = parser.parse_args()
    result = validate()
    if args.private_capture:
        result["private_evidence_cases_verified"] = verify_private_sources(args.private_capture)
    print(json.dumps(result, ensure_ascii=False))
