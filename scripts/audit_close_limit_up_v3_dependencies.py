"""Classify V3 candidate dependencies without upgrading unresolved events."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from research.close_limit_up_v3 import evaluate_close_limit_up_v3, price_path_excludes_5pct_v2
from research.targeted_limit_regime import qualify_dependency
from research.trading_regime_rulebook import build_rulebook


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
SOURCE = ROOT / "artifacts/stage1_targeted_regime"
OUT = ROOT / "artifacts/stage1_close_limit_up_v3"
DEP = SOURCE / "targeted_regime_dependency_inventory_v3.csv"
CANDIDATES = SOURCE / "provisional_observation_candidate_v3.parquet"
PROVISIONAL_RECEIPT = SOURCE / "provisional_candidate_inventory_v3.json"
CASES = ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json"


def _parquet(dataset: str) -> str:
    suffix = "**/*.parquet" if dataset in {"daily_bars", "daily_status", "adjustment_factor"} else "part.parquet"
    return str(V1 / dataset / suffix).replace("\\", "/")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> None:
    receipt = json.loads(PROVISIONAL_RECEIPT.read_text(encoding="utf-8"))
    if (_sha(V1 / "metadata/manifest.json") != receipt["v1_manifest_sha256"]
            or _sha(DEP) != receipt["dependency_inventory_sha256"]
            or _sha(CANDIDATES) != receipt["candidate_parquet_sha256"]):
        raise ValueError("V3 provisional source changed")
    official = json.loads(CASES.read_text(encoding="utf-8"))["documents"]
    cases = {(row["security_id"], row["effective_session"]): row for row in official}
    if len(cases) != len(official):
        raise ValueError("Duplicate official effective-day case")
    db = duckdb.connect()
    db.execute("PRAGMA disable_progress_bar")
    db.execute("CREATE TEMP TABLE dep AS SELECT * FROM read_csv_auto(?)", [str(DEP)])
    rows = db.execute(f"""
        WITH bar AS (
            SELECT b.security_id, b.trade_date, b.close, b.high, f.factor,
                   lag(b.close) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) AS previous_close,
                   lag(f.factor) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) AS previous_factor
            FROM read_parquet('{_parquet('daily_bars')}') b
            JOIN read_parquet('{_parquet('daily_status')}') valid
              USING (security_id, trade_date)
            LEFT JOIN read_parquet('{_parquet('adjustment_factor')}') f USING (security_id, trade_date)
            WHERE valid.is_susp_sec IS NOT TRUE
        )
        SELECT d.security_id, d.trade_date, d.candidate_ids,
               m.exchange, m.board, b.close, b.high, b.factor,
               b.previous_close, b.previous_factor,
               s.is_xr_sec, s.is_wd_sec
        FROM dep d
        LEFT JOIN bar b USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_parquet('daily_status')}') s USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_parquet('security_master')}') m USING (security_id)
        ORDER BY d.security_id, d.trade_date
    """).fetchall()
    if len(rows) != receipt["dependency_security_dates"]:
        raise ValueError("Dependency join changed grain")
    rulebook = build_rulebook()
    counts, methods = Counter(), Counter()
    by_candidate = defaultdict(dict)
    classified = []
    official_intersections = set()
    for (code, day, ids, exchange, board, close, high, factor,
         prior_close, prior_factor, is_xr, is_wd) in rows:
        day_text = day.isoformat()
        case = cases.get((code, day_text))
        if case:
            official_intersections.add(case["source_document_id"])
        if exchange is None or board is None or close is None:
            raise ValueError("Missing candidate dependency bar/master")
        # Candidate construction already requires the IPO-derived phase to be
        # NORMAL_LISTED for all five dates; relisting and other exceptions are
        # deliberately not inferred from this fact.
        event = evaluate_close_limit_up_v3(
            board=board, listing_phase="NORMAL_LISTED", close=close, high=high,
            previous_raw_close=prior_close, factor=factor,
            previous_factor=prior_factor, is_xr_sec=is_xr, is_wd_sec=is_wd,
            special_exception="UNKNOWN",
        )
        reference_class = event.reference_day.classification
        if reference_class == "ORDINARY" and event.mechanical_hit:
            tier = "A_MECHANICAL_V3_HIT_EXCEPTION_PENDING"
        elif reference_class == "ORDINARY":
            tier = "B_NON_LIMIT_UP"
        elif reference_class in {"CORPORATE_ACTION_SPECIAL", "IPO_NO_LIMIT",
                                 "RELISTING_OR_OTHER_NO_LIMIT", "OTHER_SPECIAL"}:
            tier = "C_SPECIAL_REFERENCE_OR_PHASE"
        else:
            tier = "C_UNRESOLVED_REFERENCE"
        decision = qualify_dependency(
            rulebook=rulebook, exchange=exchange, board=board,
            security_id=code, day=day, listing_phase="NORMAL_LISTED",
            official_case=case,
        )
        # No independent exception-clearance record is present for a V3 hit.
        # This is a mathematical diagnostic, never an admitted qualification.
        counterfactual = evaluate_close_limit_up_v3(
            board=board, listing_phase="NORMAL_LISTED", close=close, high=high,
            previous_raw_close=prior_close, factor=factor,
            previous_factor=prior_factor, is_xr_sec=is_xr, is_wd_sec=is_wd,
            special_exception="CLEARED",
        )
        separated = price_path_excludes_5pct_v2(
            event=counterfactual, board=board, day=day,
        )
        counts[reference_class] += 1
        counts[tier] += 1
        counts[f"EVENT_{event.status}"] += 1
        counts[f"REGIME_{decision.status}"] += 1
        counts["POTENTIAL_OLD_RULE_PRICE_PATH_SEPARATED"] += separated
        methods[decision.method] += 1
        classified.append((code, day_text, tier, reference_class, event.status,
                           event.reason, event.mechanical_hit,
                           str(event.canonical_limit_up_cents) if event.canonical_limit_up_cents is not None else "",
                           decision.status, decision.method, decision.reason or "",
                           separated, ids))
        for identity in ids.split("|"):
            by_candidate[identity][day_text] = (event, counterfactual, decision)

    OUT.mkdir(parents=True, exist_ok=True)
    dep_path = OUT / "v3_dependency_classification.csv"
    with dep_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("security_id", "trade_date", "tier", "reference_day_class",
                         "v3_status", "v3_reason", "mechanical_hit",
                         "calculated_upper_cents", "regime_status", "regime_method",
                         "regime_reason", "counterfactual_price_path_separated",
                         "candidate_ids"))
        writer.writerows(classified)
    candidates = db.execute(
        f"SELECT candidate_id, security_id, trigger_date, five_observation_dates, rsi_filter_status "
        f"FROM read_parquet('{str(CANDIDATES).replace(chr(92), '/')}') "
        "ORDER BY security_id, trigger_date"
    ).fetchall()
    dispositions = Counter()
    candidate_path = OUT / "v3_candidate_disposition.csv"
    with candidate_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("candidate_id", "security_id", "trigger_date", "strict_mechanical_hits",
                         "possible_hits_including_special", "rsi_filter_status", "disposition"))
        for identity, code, trigger, dates, rsi_status in candidates:
            items = [by_candidate[identity][day] for day in json.loads(dates)]
            strict = sum(counterfactual.status == "TRUE" for _, counterfactual, _ in items)
            possible = strict + sum(event.reference_day.classification != "ORDINARY"
                                    for event, _, _ in items)
            disposition = ("REJECTED_STRICT_PREDICATE" if possible < 4 else
                           "UNRESOLVED_RSI_WARMUP" if rsi_status != "READY_PROVISIONAL" else
                           "UNRESOLVED_REGIME_OR_EXCEPTION")
            dispositions[disposition] += 1
            counts[f"STRICT_MECHANICAL_{strict}_OF_5"] += 1
            writer.writerow((identity, code, trigger.isoformat(), strict, possible,
                             rsi_status, disposition))
    # Post-hoc quality check only: D8 is outcome-only and never enters V3
    # classification, candidate discovery, regime decisions, or signal inputs.
    d8_crosscheck = {}
    for label in ("outcome_adjustment", "outcome_adjustment_exclusions"):
        joined = db.execute(
            f"SELECT c.reference_day_class, count(*) FROM "
            f"read_csv_auto('{str(dep_path).replace(chr(92), '/')}') c "
            f"JOIN read_parquet('{str(V1 / label / 'part.parquet').replace(chr(92), '/')}') d "
            "ON c.security_id = d.security_id AND c.trade_date = d.effective_date "
            "GROUP BY c.reference_day_class ORDER BY c.reference_day_class"
        ).fetchall()
        d8_crosscheck[label] = {key: value for key, value in joined}
    if any(overlap.get("ORDINARY", 0) for overlap in d8_crosscheck.values()):
        raise ValueError("Post-hoc D8 action overlaps an ordinary V3 dependency day")
    summary = {
        "schema": "close-limit-up-v3-targeted-dependency-audit/1",
        "status": "BLOCKED_TARGETED_REGIME_EVIDENCE_INSUFFICIENT",
        "snapshot_manifest_sha256": receipt["v1_manifest_sha256"],
        "v3_daily_classification_semantic_sha256": receipt["v3_daily_classification_semantic_sha256"],
        "rounding_contract_sha256": _sha(OUT / "rounding_contract.json"),
        "reference_day_contract_sha256": _sha(OUT / "reference_day_contract.json"),
        "v3_implementation_sha256": _source_sha(ROOT / "research/close_limit_up_v3.py"),
        "provisional_candidate_count": len(candidates),
        "dependency_security_dates": len(rows),
        "reference_and_tier_counts": dict(sorted(counts.items())),
        "regime_method_counts": dict(sorted(methods.items())),
        "official_case_documents_intersecting": sorted(official_intersections),
        "posthoc_outcome_only_d8_overlap_by_reference_class": d8_crosscheck,
        "formal_observations": 0,
        "candidate_disposition_counts": dict(sorted(dispositions.items())),
        "reason": "No independent per-dependency evidence clears no-limit exceptions or old-rule risk state for all five Observation days; V3 price hits remain mechanical leads only.",
        "dependency_csv_sha256": _sha(dep_path),
        "candidate_csv_sha256": _sha(candidate_path),
        "source_candidate_sha256": receipt["candidate_parquet_sha256"],
        "source_dependency_sha256": receipt["dependency_inventory_sha256"],
        "official_evidence_inventory_sha256": _sha(CASES),
    }
    (OUT / "targeted_qualification_receipt.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in (
        "status", "provisional_candidate_count", "dependency_security_dates",
        "reference_and_tier_counts", "candidate_disposition_counts",
    )}, indent=2))


if __name__ == "__main__":
    main()
