"""Materialize only four official pre-2024 sessions needed for IPO phase."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import duckdb

from research.pre_window_calendar_context import ipo_session_number


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
OUT = ROOT / "artifacts/stage1_limit_regime"
MANIFEST_HASH = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"
PRE_WINDOW_SESSIONS = tuple(date(2023, 12, day) for day in (26, 27, 28, 29))
HOLIDAY_SOURCES = {
    "SH": "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20231226_5733941.shtml",
    "SZ": "https://www.szse.cn/disclosure/notice/t20231226_605108.html",
}
LISTINGS = {
    "301526.SZ": ("2023-12-26", "SZ", "ChiNext",
                  "https://www.szse.cn/disclosure/notice/company/t20231225_605090.html"),
    "603004.SH": ("2023-12-27", "SH", "SSE Main",
                  "https://big5.sse.com.cn/disclosure/listedinfo/announcement/c/new/2023-12-26/603004_20231226_0WNX.pdf"),
    "301578.SZ": ("2023-12-28", "SZ", "ChiNext",
                  "https://www.szse.cn/disclosure/notice/t20231227_605117.html"),
    "301566.SZ": ("2023-12-29", "SZ", "ChiNext",
                  "https://www.szse.cn/disclosure/notice/t20231228_605136.html"),
}


def main() -> None:
    manifest_hash = hashlib.sha256((V1 / "metadata/manifest.json").read_bytes()).hexdigest()
    if manifest_hash != MANIFEST_HASH:
        raise ValueError("REAL_RESEARCH_SNAPSHOT_V1 manifest changed")
    db = duckdb.connect()
    calendar_path = str(V1 / "trading_calendar/part.parquet").replace("\\", "/")
    master_path = str(V1 / "security_master/part.parquet").replace("\\", "/")
    v1_calendar = tuple(row[0] for row in db.execute(
        f"SELECT trade_date FROM read_parquet('{calendar_path}') "
        "WHERE is_trading_day ORDER BY trade_date"
    ).fetchall())
    if v1_calendar[:4] != tuple(date(2024, 1, day) for day in (2, 3, 4, 5)):
        raise ValueError("Unexpected V1 calendar boundary")
    joined = PRE_WINDOW_SESSIONS + v1_calendar
    if tuple(sorted(set(joined))) != joined:
        raise ValueError("Pre-window sessions overlap V1")
    listings = []
    for code, (official_listing, exchange, board, source_url) in LISTINGS.items():
        row = db.execute(
            f"SELECT listing_date, exchange, board FROM read_parquet('{master_path}') "
            "WHERE security_id = ?", [code]
        ).fetchone()
        if row != (date.fromisoformat(official_listing), exchange, board):
            raise ValueError(f"V1 listing metadata conflicts with official notice: {code}")
        listing_day = date.fromisoformat(official_listing)
        listings.append({
            "security_id": code,
            "listing_date": official_listing,
            "official_listing_notice": source_url,
            "first_four_v1_sessions": [
                {"trade_date": day.isoformat(),
                 "ipo_session_number": ipo_session_number(listing_day, day, joined)}
                for day in v1_calendar[:4]
            ],
        })

    OUT.mkdir(parents=True, exist_ok=True)
    artifact = OUT / "pre_window_calendar_context_v1.parquet"
    db.execute("""
        CREATE TABLE context (
            exchange VARCHAR, trade_date DATE, is_trading_day BOOLEAN, source_url VARCHAR
        )
    """)
    db.executemany(
        "INSERT INTO context VALUES (?, ?, true, ?)",
        [(exchange, day, url)
         for exchange, url in HOLIDAY_SOURCES.items()
         for day in PRE_WINDOW_SESSIONS
         if exchange == "SZ" or day >= date(2023, 12, 27)]
    )
    db.execute(f"COPY (SELECT * FROM context ORDER BY exchange, trade_date) "
               f"TO '{str(artifact).replace(chr(92), '/')}' "
               "(FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
    receipt = {
        "schema": "pre-window-calendar-context/1",
        "dataset": "PRE_WINDOW_CALENDAR_CONTEXT_V1",
        "qualification_status": "QUALIFIED_MINIMAL_CONTEXT",
        "v1_manifest_sha256": manifest_hash,
        "scope": "SZ: 2023-12-26 through 2023-12-29; SH: 2023-12-27 through 2023-12-29",
        "rows": 7,
        "unique_pre_window_sessions": [day.isoformat() for day in PRE_WINDOW_SESSIONS],
        "exchange_holiday_sources": HOLIDAY_SOURCES,
        "listings": listings,
        "v1_calendar_modified": False,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    (OUT / "pre_window_calendar_context_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
