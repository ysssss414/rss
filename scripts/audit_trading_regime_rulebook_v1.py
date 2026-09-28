"""Freeze official rule receipts and audit whether V1 can support daily constraints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from research.trading_regime_rulebook import SOURCES, build_rulebook


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
OUTPUT = ROOT / "artifacts/stage1_trading_regime"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name: str, value: dict) -> None:
    path = OUTPUT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    expected_manifest = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"
    manifest = SNAPSHOT / "metadata/manifest.json"
    actual_manifest = sha256(manifest)
    if actual_manifest != expected_manifest:
        raise ValueError("REAL_RESEARCH_SNAPSHOT_V1 manifest changed")

    book = build_rulebook()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json("trading_regime_rulebook_v1.json", book)
    write_json("trading_regime_rule_sources.json", {
        "schema": "trading-regime-rule-sources/1",
        "retrieved_date": "2026-09-28",
        "sources": [{"source_id": key, **source} for key, source in SOURCES.items()],
        "explanatory_cross_check": {
            "authority": "中国证监会上海监管局",
            "url": "https://www.csrc.gov.cn/shanghai/c105566/c7643909/content.shtml",
            "finding": "2026-07-06: SSE/SZSE main risk warning 5% to 10%; ChiNext/STAR 20% and BSE 30% unchanged",
            "not_rule_authority": True,
        },
        "effective_date_note": "SSE/SZSE 2023 rules took effect with the first registration-system main-board IPO on 2023-04-10; the rulebook is clipped to the research window.",
    })

    db = duckdb.connect()
    master = str(SNAPSHOT / "security_master/part.parquet").replace("\\", "/")
    status = str(SNAPSHOT / "daily_status/**/*.parquet").replace("\\", "/")
    boards = db.execute(
        f"SELECT exchange, board, count(*) FROM read_parquet('{master}') "
        "GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall()
    counts = db.execute(
        f"SELECT count(*), count(DISTINCT security_id), sum(is_st_sec::int), "
        f"sum(is_susp_sec::int) FROM read_parquet('{status}')"
    ).fetchone()
    case = db.execute(
        f"SELECT security_id, trade_date, is_st_sec, price_high_lmt_rate, "
        f"high_limited FROM read_parquet('{status}') "
        "WHERE (security_id = '600518.SH' AND trade_date = '2024-07-04') "
        "OR (security_id = '600187.SH' AND trade_date = '2026-07-06') "
        "ORDER BY security_id, trade_date"
    ).fetchall()
    schema = [row[0] for row in db.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{status}')"
    ).fetchall()]
    master_schema = [row[0] for row in db.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{master}')"
    ).fetchall()]
    delisted = db.execute(
        f"SELECT count(*) FROM read_parquet('{master}') WHERE delisting_date IS NOT NULL"
    ).fetchone()[0]
    prewindow_ipos = db.execute(
        f"SELECT security_id, listing_date FROM read_parquet('{master}') "
        "WHERE listing_date BETWEEN '2023-12-26' AND '2023-12-29' "
        "ORDER BY listing_date, security_id"
    ).fetchall()

    write_json("trading_regime_rulebook_qualification.json", {
        "schema": "trading-regime-rulebook-qualification/1",
        "result": "BLOCKED_PIT_RISK_WARNING_STATE_UNQUALIFIED",
        "rulebook_source_qualification": "PASS",
        "daily_fact_qualification": "FAIL",
        "v1_manifest_sha256": actual_manifest,
        "rulebook_sha256": sha256(OUTPUT / "trading_regime_rulebook_v1.json"),
        "source_receipt_sha256": sha256(OUTPUT / "trading_regime_rule_sources.json"),
        "rule_count": len(book["rules"]),
        "v1_universe": sum(row[2] for row in boards),
        "v1_board_counts": [{"exchange": row[0], "board": row[1], "securities": row[2]} for row in boards],
        "bse_in_v1": any(row[0] == "BJ" for row in boards),
        "v1_daily_status_rows": counts[0],
        "v1_daily_status_securities": counts[1],
        "v1_vendor_st_true_rows": counts[2],
        "v1_vendor_suspended_rows": counts[3],
        "v1_master_dated_delistings": delisted,
        "prewindow_ipo_needing_2023_sessions": [
            {"security_id": row[0], "listing_date": row[1].isoformat()}
            for row in prewindow_ipos
        ],
        "v1_master_has_security_type": "security_type" in master_schema,
        "v1_status_has_effective_date": "status_effective_date" in schema,
        "v1_status_has_available_at": "available_at" in schema,
        "observed_vendor_rows": [{
            "security_id": row[0], "trade_date": row[1].isoformat(),
            "vendor_is_st_sec": row[2], "vendor_limit_up_rate": row[3],
            "vendor_limit_up_price": row[4],
        } for row in case],
        "counterexamples": [
            {
                "security_id": "600518.SH", "trade_date": "2024-07-04",
                "official_fact": "Risk warning removed at market open; ordinary 10% limit applies",
                "official_issuer_notice": "https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF",
                "vendor_conflict": "is_st_sec remains true on removal date",
            },
            {
                "security_id": "600187.SH", "trade_date": "2026-07-06",
                "official_rule": "SSE main-board risk-warning rate changed to 10%",
                "vendor_conflict": "price_high_lmt_rate remains 0.05; absolute high_limited is 1.62",
            },
        ],
        "missing_to_unblock": [
            "Dated official issuer/exchange risk-warning introduction, removal and conversion events with comprehensive universe coverage",
            "Per-security-day effective status and as-of provenance, reconciled against V1 vendor labels",
            "Explicit qualified security type and lifecycle provenance where Gate C requires them",
            "Official late-2023 exchange sessions to classify IPO days crossing into the 2024 window",
        ],
        "not_materialized": ["DAILY_TRADING_CONSTRAINT_V1", "REAL_RESEARCH_SNAPSHOT_V2"],
        "forbidden_fallback": "Vendor ST or limit-rate fields cannot define canonical status or rule",
    })


if __name__ == "__main__":
    main()
