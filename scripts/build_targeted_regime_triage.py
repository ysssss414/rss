"""Fail-closed triage of provisional dependencies; no formal event upgrade."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import duckdb

from research.targeted_limit_regime import candidate_disposition, qualify_dependency
from research.trading_regime_rulebook import build_rulebook


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
OUT = ROOT / "artifacts/stage1_targeted_regime"
CASES = ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json"
TARGETED_SEARCH_ATTEMPTS = [
    {"security_id": "603099.SH", "candidate_trigger_date": "2024-01-08",
     "outcome": "NO_PRE_EFFECTIVE_OFFICIAL_DOCUMENT_ADMITTED"},
    {"security_id": "600289.SH", "candidate_trigger_date": "2024-02-22",
     "outcome": "LATER_ISSUER_DISCLOSURE_FOUND_NOT_PIT_FOR_TRIGGER"},
    {"security_id": "000609.SZ", "candidate_trigger_date": "2024-06-14",
     "outcome": "NO_PRE_EFFECTIVE_OFFICIAL_DOCUMENT_ADMITTED"},
]


def _path(dataset: str) -> str:
    suffix = "**/*.parquet" if dataset in {"daily_status", "daily_bars"} else "part.parquet"
    return str(V1 / dataset / suffix).replace("\\", "/")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    receipt = json.loads((OUT / "provisional_candidate_inventory.json").read_text())
    candidate_path = OUT / "provisional_observation_candidate_v1.parquet"
    dependency_path = OUT / "targeted_regime_dependency_inventory.csv"
    if (_sha(V1 / "metadata/manifest.json") != receipt["v1_manifest_sha256"]
            or _sha(candidate_path) != receipt["candidate_parquet_sha256"]
            or _sha(dependency_path) != receipt["dependency_inventory_sha256"]):
        raise ValueError("Provisional source fingerprint changed")
    evidence = json.loads(CASES.read_text(encoding="utf-8"))
    cases = {(row["security_id"], row["effective_session"]): row
             for row in evidence["documents"]}
    if len(cases) != len(evidence["documents"]):
        raise ValueError("Conflicting official effective-day cases")
    db = duckdb.connect()
    db.execute("PRAGMA disable_progress_bar")
    db.execute("CREATE TEMP TABLE deps AS SELECT * FROM read_csv_auto(?)", [str(dependency_path)])
    rows = db.execute(f"""
        SELECT d.security_id, d.trade_date, d.candidate_ids,
               m.exchange, m.board, s.preclose, b.close
        FROM deps d
        LEFT JOIN read_parquet('{_path('security_master')}') m USING (security_id)
        LEFT JOIN read_parquet('{_path('daily_status')}') s USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_path('daily_bars')}') b USING (security_id, trade_date)
        ORDER BY d.security_id, d.trade_date
    """).fetchall()
    if len(rows) != receipt["dependency_security_dates"]:
        raise ValueError("Dependency join changed grain")
    rulebook = build_rulebook()
    qualified_rows = []
    candidate_dependencies = defaultdict(list)
    method_counts, reason_counts, year_counts = Counter(), Counter(), Counter()
    intersected_documents = set()
    price_hints = 0
    for security_id, day, candidate_ids, exchange, board, preclose, close in rows:
        day_string = day.isoformat()
        case = cases.get((security_id, day_string))
        if case:
            intersected_documents.add(case["source_document_id"])
        if exchange is None or board is None or close is None:
            # A broken dependency can never become a formal event.
            from research.targeted_limit_regime import RegimeDecision
            decision = RegimeDecision("UNRESOLVED", "NONE", "MISSING_V1_DEPENDENCY")
        else:
            decision = qualify_dependency(
                rulebook=rulebook, exchange=exchange, board=board,
                security_id=security_id, day=day,
                listing_phase="NORMAL_LISTED", official_case=case,
            )
        if (close is not None and preclose is not None and preclose > 0
                and Decimal(str(close)) / Decimal(str(preclose)) >= Decimal("1.05")):
            price_hints += 1
        method_counts[decision.method] += 1
        if decision.reason:
            reason_counts[decision.reason] += 1
        year_counts[day.year] += 1
        qualified_rows.append((
            security_id, day_string, decision.status, decision.method,
            decision.reason, decision.rule_id, decision.source_id,
            "UNQUALIFIED_OFFICIAL_REFERENCE_MISSING",
            "REAL_RESEARCH_SNAPSHOT_V1",
        ))
        for candidate_id in candidate_ids.split("|"):
            candidate_dependencies[candidate_id].append(decision)

    db.execute("""CREATE TABLE targeted (
        security_id VARCHAR, trade_date DATE, qualification_status VARCHAR,
        qualification_method VARCHAR, unresolved_reason VARCHAR,
        rule_id VARCHAR, official_source_id VARCHAR,
        price_path_qualification VARCHAR, snapshot_id VARCHAR
    )""")
    if qualified_rows:
        db.executemany("INSERT INTO targeted VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", qualified_rows)
    qualification_path = OUT / "targeted_limit_regime_qualification_v1.parquet"
    db.execute(f"COPY (SELECT * FROM targeted ORDER BY security_id, trade_date) "
               f"TO '{str(qualification_path).replace(chr(92), '/')}' "
               "(FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")

    candidates = db.execute(
        f"SELECT candidate_id, security_id, trigger_date "
        f"FROM read_parquet('{str(candidate_path).replace(chr(92), '/')}') "
        "ORDER BY security_id, trigger_date"
    ).fetchall()
    disposition = Counter()
    outputs = {
        "REJECTED_NON_10PCT": OUT / "provisional_candidate_rejection_inventory.csv",
        "UNRESOLVED": OUT / "provisional_candidate_unresolved_inventory.csv",
    }
    files = {kind: path.open("w", newline="", encoding="utf-8")
             for kind, path in outputs.items()}
    try:
        writers = {kind: csv.writer(handle) for kind, handle in files.items()}
        for writer in writers.values():
            writer.writerow(("candidate_id", "security_id", "trigger_date",
                             "dependency_count", "rejected_dependencies", "unresolved_dependencies"))
        for identity, security_id, trigger in candidates:
            deps = candidate_dependencies[identity]
            if len(deps) != 5:
                raise ValueError(f"Candidate dependency count is not five: {identity}")
            result = candidate_disposition(deps)
            disposition[result] += 1
            if result in writers:
                writers[result].writerow((
                    identity, security_id, trigger.isoformat(), 5,
                    sum(d.status == "REJECTED_NON_10PCT" for d in deps),
                    sum(d.status == "UNRESOLVED" for d in deps),
                ))
    finally:
        for handle in files.values():
            handle.close()
    if sum(disposition.values()) != receipt["candidate_count"]:
        raise ValueError("Candidate triage does not reconcile")
    status_counts = Counter(row[2] for row in qualified_rows)
    targeted_evidence = {
        "schema": "targeted-limit-regime-evidence-inventory/1",
        "source_inventory": str(CASES.relative_to(ROOT)).replace("\\", "/"),
        "source_inventory_sha256": _sha(CASES),
        "existing_official_documents_reused": len(evidence["documents"]),
        "documents_intersecting_dependencies": sorted(intersected_documents),
        "new_targeted_candidate_searches": TARGETED_SEARCH_ATTEMPTS,
        "new_official_documents_admitted": 0,
        "caveat": "Three bounded candidate searches yielded no pre-trigger official fact admitted; the six prior case documents do not intersect dependency dates. No complete exchange status/reference archive is claimed.",
    }
    evidence_path = OUT / "targeted_limit_regime_evidence_inventory.json"
    evidence_path.write_text(json.dumps(targeted_evidence, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema": "targeted-limit-regime-qualification-receipt/1",
        "status": "BLOCKED_CANONICAL_LIMIT_UP_UNQUALIFIED",
        "provisional_candidates": len(candidates),
        "dependency_security_dates": len(qualified_rows),
        "full_market_old_rule_main_board_security_dates": 1902063,
        "dependency_reduction_vs_full_market": 1 - len(qualified_rows) / 1902063,
        "qualification_status_counts": dict(sorted(status_counts.items())),
        "qualification_method_counts": dict(sorted(method_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "dependency_year_counts": dict(sorted(year_counts.items())),
        "raw_price_move_ge_5pct_discovery_hints_not_qualification": price_hints,
        "candidate_disposition_counts": dict(sorted(disposition.items())),
        "candidate_qualified_formal_upgrade_count": 0,
        "candidate_formal_upgrade_status": "NOT_RUN_CANONICAL_DAILY_UPPER_LIMIT_UNQUALIFIED",
        "qualified_daily_absolute_limit_reference_rows_acquired": 0,
        "smoke_run_manifest_written": False,
        "qualification_parquet_sha256": _sha(qualification_path),
        "targeted_evidence_sha256": _sha(evidence_path),
        "rejection_inventory_sha256": _sha(outputs["REJECTED_NON_10PCT"]),
        "unresolved_inventory_sha256": _sha(outputs["UNRESOLVED"]),
    }
    (OUT / "targeted_qualification_receipt.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in (
        "dependency_security_dates", "qualification_status_counts", "candidate_disposition_counts",
    )}, indent=2))


if __name__ == "__main__":
    main()
