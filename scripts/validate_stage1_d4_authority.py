"""Offline D4 source integrity, PIT replay and bounded-sample receipt validator."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from research.d4_authority import expand_rule_matrix, resolve


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_d4_authority_closure"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def replay() -> dict:
    manifest = read(ART / "authority_manifest.json")
    matrix = read(ART / "rule_matrix.json")
    sample = read(ART / "bounded_sample_facts.json")
    ids = set()
    admitted = set()
    for source in manifest["sources"]:
        sid = source["source_id"]
        if sid in ids:
            raise ValueError(f"duplicate source: {sid}")
        ids.add(sid)
        path = source["local_evidence"]
        if path is not None:
            actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            if actual != source["sha256"]:
                raise ValueError(f"source hash mismatch: {sid}")
        if source["admitted_for_replay"]:
            if path is None or source["sha256"] is None:
                raise ValueError(f"unfrozen admitted source: {sid}")
            admitted.add(sid)
    rules = expand_rule_matrix(matrix)
    if any(r["source_id"] not in ids for r in rules):
        raise ValueError("unknown rule source")
    if any(f["source_id"] not in ids for f in sample["facts"]):
        raise ValueError("unknown fact source")
    for first, rule in enumerate(rules):
        if rule["effective_to"] is not None and rule["effective_to"] < rule["effective_from"]:
            raise ValueError("inverted rule period")
        for other in rules[first + 1:]:
            if (rule["exchange"], rule["board"], rule["category"]) != (other["exchange"], other["board"], other["category"]):
                continue
            upper = min(rule["effective_to"] or "9999-12-31", other["effective_to"] or "9999-12-31")
            lower = max(rule["effective_from"], other["effective_from"])
            if lower <= upper:
                raise ValueError("overlapping frozen rule periods")
    cases = read(ROOT / sample["sample_case_source"])["cases"] + sample["additional_cases"]
    outputs = []
    for case in cases:
        result = resolve(case["security_id"], case["trade_date"], sample["facts"], rules, admitted)
        outputs.append({"case": case["case"], "security_id": case["security_id"],
                        "trading_date": case["trade_date"],
                        "qualification_state": result["qualification_state"],
                        "limit_regime": result["limit_regime"],
                        "reason_codes": result["reason_codes"],
                        "authority_trace": result["authority_trace"]})
    states = Counter(row["qualification_state"] for row in outputs)
    regimes = Counter(row["limit_regime"] for row in outputs if row["qualification_state"] == "VALID")
    return {
        "schema": "stage1-d4-bounded-sample-summary/1",
        "sample_source": sample["sample_case_source"],
        "tested_security_days": len(outputs),
        "counts": {key: states[key] for key in ("VALID", "INVALID", "MISSING", "CONFLICT")},
        "valid_regimes": {key: regimes[key] for key in ("LIMIT_5", "LIMIT_10", "LIMIT_20", "NO_DAILY_LIMIT", "OTHER_EXPLICIT")},
        "cases": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", action="store_true", help="print computed replay for artifact authoring")
    args = parser.parse_args()
    result = replay()
    if args.dump:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    frozen = read(ART / "bounded_sample_summary.json")
    if result != frozen:
        raise SystemExit("D4 bounded sample differs from frozen summary")
    receipt = read(ART / "qualification_receipt.json")
    if receipt["gate_result"] != "STAGE1_D4_AUTHORITY_CLOSURE_STOP":
        raise SystemExit("D4 receipt must remain STOP")
    if receipt["tested_security_days"] != result["tested_security_days"] or receipt["counts"] != result["counts"]:
        raise SystemExit("D4 receipt counts mismatch")
    print(json.dumps({"validation": "PASS", "gate_result": receipt["gate_result"],
                      "tested_security_days": result["tested_security_days"],
                      "counts": result["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
