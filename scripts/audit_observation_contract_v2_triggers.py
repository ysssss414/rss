"""Recompute V3 lookbacks and qualify only Observation V2 trigger dates."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb

from research.close_limit_up_v3 import evaluate_close_limit_up_v3, price_path_excludes_5pct_v2
from research.observation_contract_v2 import evaluate_observation_trigger_v2
from research.provisional_candidates import candidate_id_v3
from research.targeted_limit_regime import RegimeDecision, qualify_dependency
from research.trading_regime_rulebook import build_rulebook


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
SOURCE = ROOT / "artifacts/stage1_targeted_regime"
OUT = ROOT / "artifacts/stage1_observation_contract_v2"
CANDIDATES = SOURCE / "provisional_observation_candidate_v3.parquet"
SOURCE_RECEIPT = SOURCE / "provisional_candidate_inventory_v3.json"
OLD_RECEIPT = ROOT / "artifacts/stage1_close_limit_up_v3/targeted_qualification_receipt.json"
OFFICIAL = ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parquet(name: str) -> str:
    suffix = "**/*.parquet" if name != "security_master" else "part.parquet"
    return str(V1 / name / suffix).replace("\\", "/")


def _official_clears_exception(case: dict | None, code: str, day: date) -> bool:
    return bool(case and case.get("security_id") == code
                and case.get("effective_session") == day.isoformat()
                and case.get("source_publish_date", "9999") < day.isoformat()
                and case.get("transition") in {
                    "NORMAL_TO_RISK_WARNING_REGIME", "RISK_WARNING_TO_NORMAL_REGIME"
                }
                and case.get("source_document_id")
                and case.get("source_url", "").startswith((
                    "https://static.cninfo.com.cn/", "https://disc.static.szse.cn/",
                    "https://www.sse.com.cn/")))


def main() -> None:
    source = json.loads(SOURCE_RECEIPT.read_text(encoding="utf-8"))
    old = json.loads(OLD_RECEIPT.read_text(encoding="utf-8"))
    if (source["schema"] != "provisional-observation-candidate-inventory/3"
            or _sha(V1 / "metadata/manifest.json") != source["v1_manifest_sha256"]
            or _sha(CANDIDATES) != source["candidate_parquet_sha256"]
            or old["source_candidate_sha256"] != source["candidate_parquet_sha256"]):
        raise ValueError("Frozen V3 candidate or Snapshot V1 source changed")
    db = duckdb.connect()
    db.execute("PRAGMA disable_progress_bar")
    candidates = db.execute(
        f"SELECT candidate_id, security_id, trigger_date, five_observation_dates, "
        f"rsi14_provisional, rsi_filter_status, board FROM "
        f"read_parquet('{str(CANDIDATES).replace(chr(92), '/')}') "
        "ORDER BY security_id, trigger_date"
    ).fetchall()
    if len(candidates) != source["candidate_count"]:
        raise ValueError("V3 candidate count changed")
    identities, trigger_keys, lookback_keys = set(), set(), set()
    windows = {}
    for identity, code, trigger, encoded, _rsi, _rsi_status, board in candidates:
        dates = tuple(date.fromisoformat(item) for item in json.loads(encoded))
        if (identity != candidate_id_v3(code, trigger.isoformat())
                or identity in identities or (code, trigger) in trigger_keys
                or len(dates) != 5 or len(set(dates)) != 5
                or tuple(sorted(dates)) != dates or dates[-1] != trigger
                or board not in {"SSE Main", "SZSE Main"}):
            raise ValueError("Invalid candidate grain, identity, board or lookback")
        identities.add(identity)
        trigger_keys.add((code, trigger))
        lookback_keys.update((code, day) for day in dates)
        windows[identity] = dates
    if len(lookback_keys) != old["dependency_security_dates"]:
        raise ValueError("Rebuilt V3 lookback grain differs from frozen baseline")

    db.execute("CREATE TEMP TABLE needed (security_id VARCHAR, trade_date DATE)")
    db.executemany("INSERT INTO needed VALUES (?, ?)", sorted(lookback_keys))
    rows = db.execute(f"""
        WITH valid_bar AS (
            SELECT b.security_id, b.trade_date, b.close, b.high, f.factor,
                   lag(b.close) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) AS previous_close,
                   lag(f.factor) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) AS previous_factor
            FROM read_parquet('{_parquet('daily_bars')}') b
            JOIN read_parquet('{_parquet('daily_status')}') s USING (security_id, trade_date)
            LEFT JOIN read_parquet('{_parquet('adjustment_factor')}') f USING (security_id, trade_date)
            WHERE s.is_susp_sec IS NOT TRUE
        )
        SELECT n.security_id, n.trade_date, m.exchange, m.board, b.close, b.high,
               b.factor, b.previous_close, b.previous_factor, s.is_xr_sec, s.is_wd_sec
        FROM needed n
        LEFT JOIN valid_bar b USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_parquet('daily_status')}') s USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_parquet('security_master')}') m USING (security_id)
        ORDER BY n.security_id, n.trade_date
    """).fetchall()
    if len(rows) != len(lookback_keys):
        raise ValueError("V3 lookback join changed grain")
    official_rows = json.loads(OFFICIAL.read_text(encoding="utf-8"))["documents"]
    cases = {(item["security_id"], item["effective_session"]): item for item in official_rows}
    if len(cases) != len(official_rows):
        raise ValueError("Duplicate dated official case")
    events, counterfactual, details = {}, {}, {}
    for (code, day, exchange, board, close, high, factor, prior_close,
         prior_factor, is_xr, is_wd) in rows:
        if (code, day) not in lookback_keys or exchange not in {"SH", "SZ"} or board is None or close is None:
            raise ValueError("Missing or invalid lookback source row")
        case = cases.get((code, day.isoformat()))
        values = dict(board=board, listing_phase="NORMAL_LISTED", close=close, high=high,
                      previous_raw_close=prior_close, factor=factor,
                      previous_factor=prior_factor, is_xr_sec=is_xr, is_wd_sec=is_wd)
        events[(code, day)] = evaluate_close_limit_up_v3(
            **values, special_exception=("CLEARED" if _official_clears_exception(case, code, day)
                                         else "UNKNOWN"))
        counterfactual[(code, day)] = evaluate_close_limit_up_v3(
            **values, special_exception="CLEARED")
        details[(code, day)] = (exchange, board, case)

    rulebook = build_rulebook()
    summary_counts, reasons, method_counts = Counter(), Counter(), Counter()
    official_intersections = set()
    output_rows = []
    examples = {}
    for identity, code, trigger, _encoded, rsi, rsi_status, board in candidates:
        window = windows[identity]
        t_event = events[(code, trigger)]
        t_counterfactual = counterfactual[(code, trigger)]
        exchange, actual_board, case = details[(code, trigger)]
        if actual_board != board:
            raise ValueError("Candidate board differs from snapshot master")
        if case:
            official_intersections.add(case["source_document_id"])
        regime = qualify_dependency(
            rulebook=rulebook, exchange=exchange, board=board,
            security_id=code, day=trigger, listing_phase="NORMAL_LISTED",
            official_case=case,
        )
        if (regime.status == "UNRESOLVED" and t_event.status == "TRUE"
                and price_path_excludes_5pct_v2(event=t_event, board=board, day=trigger)):
            regime = RegimeDecision("QUALIFIED_10PCT", "PRICE_PATH_EXCLUDES_5PCT_REGIME_V2",
                                    None, source_id=case["source_document_id"] if case else None)
        lookback = [events[(code, day)] for day in window]
        # A provisional RSI is not the formally replayed full PIT prefix.
        decision = evaluate_observation_trigger_v2(
            board=board, lookback=lookback, trigger_regime=regime.status,
            rsi14_status="UNVERIFIED_PROVISIONAL", rsi14=None,
        )
        potential_path = price_path_excludes_5pct_v2(
            event=t_counterfactual, board=board, day=trigger)
        if regime.status == "UNRESOLVED":
            evidence_need = (
                "SPECIAL_REFERENCE_OR_PHASE" if t_event.reference_day.classification != "ORDINARY"
                else "POST_RULE_EXCEPTION_CLEARANCE" if trigger >= date(2026, 7, 6)
                else "V3_PATH_EXCEPTION_CLEARANCE" if potential_path
                else "TARGETED_RISK_STATE_AND_EXCEPTION" if t_event.status == "FALSE"
                else "LOW_PRICE_OR_OTHER_EXCEPTION_REVIEW"
            )
        else:
            evidence_need = "NONE"
        summary_counts[f"TRIGGER_REGIME_{regime.status}"] += 1
        summary_counts[f"TRIGGER_V3_{t_event.status}"] += 1
        summary_counts[f"TRIGGER_REFERENCE_{t_event.reference_day.classification}"] += 1
        summary_counts[f"PROVISIONAL_RSI_{rsi_status}"] += 1
        summary_counts[f"V2_DECISION_{decision.status}"] += 1
        summary_counts["POTENTIAL_PRICE_PATH_5PCT_SEPARATED"] += int(potential_path)
        summary_counts[f"EVIDENCE_NEED_{evidence_need}"] += 1
        reasons[decision.reason] += 1
        method_counts[regime.method] += 1
        output_rows.append((identity, code, trigger.isoformat(), board,
                            t_event.reference_day.classification, t_event.status,
                            t_event.mechanical_hit, t_counterfactual.status,
                            potential_path, regime.status, regime.method,
                            regime.reason or "", evidence_need,
                            decision.definite_limit_up_count,
                            decision.possible_limit_up_count, rsi_status, rsi or "",
                            decision.status, decision.reason))
        example = {
            "candidate_id": identity, "security_id": code,
            "trigger_date": trigger.isoformat(),
            "five_observation_dates": [day.isoformat() for day in window],
            "qualified_v3_hits": decision.definite_limit_up_count,
            "possible_v3_hits": decision.possible_limit_up_count,
            "trigger_v3_status": t_event.status,
            "trigger_regime_status": regime.status,
            "evidence_need": evidence_need,
            "rsi_filter_status": rsi_status,
            "observation_v2_status": decision.status,
        }
        labels = (
            ("POSSIBLE_4_OF_5", decision.possible_limit_up_count == 4),
            ("POSSIBLE_5_OF_5", decision.possible_limit_up_count == 5),
            ("TRIGGER_MECHANICAL_HIT_PENDING_EXCEPTION", t_event.mechanical_hit),
            ("TRIGGER_NON_LIMIT", t_event.status == "FALSE"),
            ("TRIGGER_SPECIAL_REFERENCE", t_event.reference_day.classification != "ORDINARY"),
            ("POST_RULE_TRIGGER_PENDING_EXCEPTION", trigger >= date(2026, 7, 6)),
            ("RSI_WARMUP_PENDING", rsi_status == "UNVERIFIED_WARMUP"),
        )
        for label, matches in labels:
            if matches and label not in examples:
                examples[label] = example

    OUT.mkdir(parents=True, exist_ok=True)
    inventory = OUT / "observation_v2_trigger_regime_dependency_inventory.csv"
    with inventory.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("candidate_id", "security_id", "trigger_date", "board",
                         "trigger_reference_class", "trigger_v3_status", "trigger_mechanical_hit",
                         "counterfactual_v3_status", "potential_5pct_path_separation",
                         "trigger_regime_status", "trigger_regime_method", "trigger_regime_reason",
                         "evidence_need", "qualified_v3_hits_in_five", "possible_v3_hits_in_five",
                         "rsi_filter_status", "rsi14_provisional", "observation_v2_status",
                         "observation_v2_reason"))
        writer.writerows(output_rows)
    case_path = OUT / "blocked_case_audit.json"
    case_path.write_text(json.dumps({
        "schema": "observation-v2-blocked-case-audit/1",
        "scope": "V1 Snapshot inputs through each trigger T only; no post-Entry or outcome fields",
        "status": "DIAGNOSTIC_NOT_FORMAL_EVENT_AUDIT",
        "cases": examples,
    }, indent=2) + "\n", encoding="utf-8")
    result = {
        "schema": "observation-v2-trigger-regime-audit/1",
        "status": "BLOCKED_TRIGGER_DATE_REGIME_EVIDENCE_INSUFFICIENT",
        "snapshot_manifest_sha256": source["v1_manifest_sha256"],
        "candidate_parquet_sha256": source["candidate_parquet_sha256"],
        "old_all_five_day_dependency_count": len(lookback_keys),
        "new_trigger_only_dependency_count": len(trigger_keys),
        "dependency_reduction_pct": round(100 * (1 - len(trigger_keys) / len(lookback_keys)), 6),
        "trigger_qualification_calls": len(candidates),
        "lookback_regime_qualification_calls": 0,
        "v3_self_qualified_count": method_counts["PRICE_PATH_EXCLUDES_5PCT_REGIME_V2"],
        "direct_rule_qualified_count": method_counts["DIRECT_OFFICIAL_POST_2026_RULE"],
        "targeted_official_status_qualified_count": method_counts["OFFICIAL_EFFECTIVE_DAY"],
        "official_case_documents_intersecting_triggers": sorted(official_intersections),
        "formal_observation_count": None,
        "formal_observation_materialized": False,
        "formal_lifecycle_and_entries_run": False,
        "counts": dict(sorted(summary_counts.items())),
        "observation_decision_reasons": dict(sorted(reasons.items())),
        "trigger_regime_methods": dict(sorted(method_counts.items())),
        "inventory_sha256": _sha(inventory),
        "blocked_case_audit_sha256": _sha(case_path),
        "official_inventory_sha256": _sha(OFFICIAL),
        "observation_contract_sha256": _sha(OUT / "observation_pool_contract_v2.json"),
        "episode_contract_sha256": _sha(OUT / "episode_start_eligibility_v1.json"),
        "reason": "Trigger-day no-limit exception clearance and historical risk-state evidence do not overlap provisional triggers; counterfactual V3 hits and provisional RSI cannot be promoted to formal events.",
    }
    (OUT / "trigger_qualification_receipt.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in (
        "status", "old_all_five_day_dependency_count", "new_trigger_only_dependency_count",
        "dependency_reduction_pct", "v3_self_qualified_count",
        "direct_rule_qualified_count", "targeted_official_status_qualified_count",
        "counts", "observation_decision_reasons")}, indent=2))


if __name__ == "__main__":
    main()
