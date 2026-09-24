"""Offline Parquet market store; no AmazingData import or network access."""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb
import pandas as pd


STORE_SCHEMA_VERSION = 1
DATASETS = ("security_master", "trading_calendar", "daily_bars", "daily_status", "adjustment_factor")
DAY_DATASETS = DATASETS[2:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def _copy_atomic(con: duckdb.DuckDBPyConnection, query: str, path: Path, args: list | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    if temporary.exists():
        temporary.unlink()  # Only this deterministic temporary file, never a canonical partition.
    con.execute(f"COPY ({query}) TO '{temporary.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)", args or [])
    # The temporary is readable before the current partition is replaced.
    if con.execute("SELECT count(*) FROM read_parquet(?)", [str(temporary)]).fetchone()[0] < 1:
        temporary.unlink()
        raise ValueError("Empty Parquet partition")
    temporary.replace(path)


def _partition_paths(root: Path, dataset: str) -> list[Path]:
    return sorted((root / dataset).glob("year=*/month=*/part.parquet"))


def _stats(con: duckdb.DuckDBPyConnection, root: Path, dataset: str) -> dict:
    paths = _partition_paths(root, dataset) if dataset in DAY_DATASETS else [root / dataset / "part.parquet"]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError(f"Missing canonical dataset: {dataset}")
    files = [str(path) for path in paths]
    if dataset == "security_master":
        row = con.execute("SELECT count(*), count(DISTINCT security_id) FROM read_parquet(?)", [files]).fetchone()
        return {"rows": row[0], "unique_securities": row[1], "disk_bytes": sum(p.stat().st_size for p in paths)}
    sql = ("SELECT count(*), min(trade_date), max(trade_date), "
           "count(DISTINCT trade_date), count(DISTINCT security_id) " if dataset in DAY_DATASETS else
           "SELECT count(*), min(trade_date), max(trade_date), count(DISTINCT trade_date), 0 ")
    row = con.execute(sql + "FROM read_parquet(?)", [files]).fetchone()
    return {"rows": row[0], "min_trade_date": pd.Timestamp(row[1]).date().isoformat() if row[1] else None,
        "max_trade_date": pd.Timestamp(row[2]).date().isoformat() if row[2] else None,
        "unique_trade_dates": row[3], "unique_securities": row[4],
        "disk_bytes": sum(p.stat().st_size for p in paths)}


def _market_dates(calendar_capture: Path, start: str, end: str) -> list[str]:
    evidence = json.loads(calendar_capture.read_text(encoding="utf-8"))
    sessions = [[day for day in evidence["sessions"][market] if start <= day <= end]
                for market in ("SH", "SZ")]
    if not sessions[0] or sessions[0] != sessions[1]:
        raise ValueError("SH/SZ trading calendars disagree")
    return sessions[0]


def _raw_paths(checkpoint: dict, dataset: str, expected_securities: int) -> list[str]:
    records = [row for row in checkpoint["records"].values() if row["dataset"] == dataset]
    codes = [code for row in records for code in row["security_ids"]]
    if len(codes) != expected_securities or len(set(codes)) != expected_securities:
        raise ValueError(f"Incomplete acquisition: {dataset} ({len(codes)} securities)")
    if any(row["status"] != "COMPLETED" or not Path(row["cache_file"]).is_file() for row in records):
        raise ValueError(f"Unfinished acquisition: {dataset}")
    if any(sha256(Path(row["cache_file"]).read_bytes()).hexdigest() != row["sha256"] for row in records):
        raise ValueError(f"Acquisition cache hash mismatch: {dataset}")
    return [row["cache_file"] for row in records]


def _quality(con: duckdb.DuckDBPyConnection, dataset: str) -> dict[str, int]:
    key = con.execute("SELECT count(*) - count(DISTINCT (security_id, trade_date)) FROM clean").fetchone()[0]
    if key:
        raise ValueError(f"{dataset}: {key} duplicate security-date keys")
    if dataset == "daily_bars":
        row = con.execute("""SELECT count(*) FILTER (WHERE open IS NULL OR high IS NULL OR low IS NULL
            OR "close" IS NULL OR open<=0 OR high<=0 OR low<=0 OR "close"<=0 OR high<low
            OR high<greatest(open,"close") OR low>least(open,"close")),
            count(*) FILTER (WHERE volume<0), count(*) FILTER (WHERE amount<0) FROM clean""").fetchone()
        return {"duplicate_keys": key, "invalid_ohlc": row[0],
                "negative_volume": row[1], "negative_amount": row[2]}
    if dataset == "daily_status":
        row = con.execute("""SELECT count(*) FILTER (WHERE preclose IS NULL OR high_limited IS NULL
            OR low_limited IS NULL OR price_high_lmt_rate IS NULL OR price_low_lmt_rate IS NULL
            OR is_st_sec IS NULL OR is_susp_sec IS NULL OR is_wd_sec IS NULL OR is_xr_sec IS NULL)
            FROM clean""").fetchone()
        return {"duplicate_keys": key, "missing_key_status_fields": row[0]}
    row = con.execute("SELECT count(*) FILTER (WHERE factor IS NULL), count(*) FILTER (WHERE factor<=0) FROM clean").fetchone()
    return {"duplicate_keys": key, "missing_factor": row[0], "non_positive_factor": row[1]}


def _deduplicate_clean(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Keep exact duplicates once; exclude conflicting keys without guessing a winner."""
    before = con.execute("SELECT count(*) FROM clean").fetchone()[0]
    con.execute("CREATE OR REPLACE TEMP TABLE distinct_rows AS SELECT DISTINCT * FROM clean")
    after_exact = con.execute("SELECT count(*) FROM distinct_rows").fetchone()[0]
    conflicting = con.execute("""SELECT count(*) FROM (
        SELECT count(*) OVER (PARTITION BY security_id, trade_date) occurrences
        FROM distinct_rows) WHERE occurrences>1""").fetchone()[0]
    if before - after_exact + conflicting > max(100, before // 100):
        raise ValueError("Systemic duplicate security-date keys")
    con.execute("""CREATE OR REPLACE TEMP TABLE clean AS SELECT * FROM distinct_rows
        QUALIFY count(*) OVER (PARTITION BY security_id, trade_date)=1""")
    con.execute("DROP TABLE distinct_rows")
    return {"exact_duplicate_rows": before - after_exact,
            "conflicting_duplicate_key_rows": conflicting,
            "excluded_duplicate_rows": before - con.execute("SELECT count(*) FROM clean").fetchone()[0]}


def materialize(store_root: Path, acquisition_root: Path, calendar_capture: Path,
                *, start: str = "2024-01-01", end: str = "2026-09-23",
                expected_security_count: int = 5222) -> dict:
    """Build canonical monthly files from a fully checkpointed raw acquisition."""
    state = json.loads((acquisition_root / "checkpoint.json").read_text(encoding="utf-8"))
    master = pd.read_csv(acquisition_root / "security_master.csv.gz", compression="gzip")
    if (len(master) != expected_security_count or master.security_id.isna().any()
            or master.security_id.duplicated().any()):
        raise ValueError("D1 canonical security master invalid")
    sessions = _market_dates(calendar_capture, start, end)
    store_root.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    quality: dict[str, dict] = {}
    con.register("master_input", master)
    quality["security_master"] = {"rows": len(master),
        "duplicate_security_id": int(master.security_id.duplicated().sum()),
        "missing_security_id": int(master.security_id.isna().sum()),
        "invalid_listing_date": int(pd.to_datetime(master.listing_date, errors="coerce").isna().sum()),
        "missing_board": int(master.board.isna().sum()),
        "missing_security_name": int(master.security_name.isna().sum())}
    _copy_atomic(con, """SELECT security_id, exchange, board, CAST(listing_date AS DATE) listing_date,
        CAST(delisting_date AS DATE) delisting_date, security_name
        FROM master_input ORDER BY security_id""", store_root / "security_master" / "part.parquet")
    con.register("calendar_input", pd.DataFrame({"trade_date": sessions, "is_trading_day": True}))
    quality["trading_calendar"] = {"rows": len(sessions), "duplicate_trade_date": len(sessions) - len(set(sessions)),
        "SH_SZ_equal": True}
    _copy_atomic(con, "SELECT CAST(trade_date AS DATE) trade_date, is_trading_day FROM calendar_input ORDER BY trade_date",
                 store_root / "trading_calendar" / "part.parquet")
    for endpoint, dataset in (("D3_bars", "daily_bars"), ("D4_D6_status", "daily_status"),
                              ("D5_factor", "adjustment_factor")):
        paths = _raw_paths(state, endpoint, expected_security_count)
        con.execute("CREATE OR REPLACE TEMP TABLE raw AS SELECT * FROM read_csv(?, header=true, all_varchar=true, compression='gzip')", [paths])
        if dataset == "daily_bars":
            select = """SELECT code security_id, TRY_CAST(date AS DATE) trade_date,
                TRY_CAST(open AS DOUBLE) open, TRY_CAST(high AS DOUBLE) high,
                TRY_CAST(low AS DOUBLE) low, TRY_CAST(close AS DOUBLE) "close",
                TRY_CAST(volume AS BIGINT) volume, TRY_CAST(amount AS DOUBLE) amount FROM raw"""
        elif dataset == "daily_status":
            select = """SELECT MARKET_CODE security_id,
                TRY_STRPTIME(CAST(TRY_CAST(TRADE_DATE AS BIGINT) AS VARCHAR), '%Y%m%d')::DATE trade_date,
                TRY_CAST(PRECLOSE AS DOUBLE) preclose, TRY_CAST(HIGH_LIMITED AS DOUBLE) high_limited,
                TRY_CAST(LOW_LIMITED AS DOUBLE) low_limited,
                TRY_CAST(PRICE_HIGH_LMT_RATE AS DOUBLE) price_high_lmt_rate,
                TRY_CAST(PRICE_LOW_LMT_RATE AS DOUBLE) price_low_lmt_rate,
                TRY_CAST(IS_ST_SEC AS BOOLEAN) is_st_sec,
                TRY_CAST(IS_SUSP_SEC AS BOOLEAN) is_susp_sec,
                TRY_CAST(IS_WD_SEC AS BOOLEAN) is_wd_sec,
                TRY_CAST(IS_XR_SEC AS BOOLEAN) is_xr_sec FROM raw"""
        else:
            select = """SELECT security_id, TRY_CAST(trade_date AS DATE) trade_date,
                TRY_CAST(factor AS DOUBLE) factor FROM raw"""
        con.execute(f"CREATE OR REPLACE TEMP TABLE normalized AS {select}")
        issues = con.execute("""SELECT count(*) FILTER (WHERE security_id IS NULL OR trade_date IS NULL),
            count(*) FILTER (WHERE trade_date < ? OR trade_date > ?) FROM normalized""",
            [start, end]).fetchone()
        con.execute("""CREATE OR REPLACE TEMP TABLE clean AS SELECT n.* FROM normalized n
            JOIN master_input m USING (security_id)
            WHERE n.security_id IS NOT NULL AND n.trade_date IS NOT NULL
            AND n.trade_date BETWEEN ? AND ? AND n.trade_date >= CAST(m.listing_date AS DATE)""", [start, end])
        duplicate_issues = _deduplicate_clean(con)
        q = _quality(con, dataset)
        before_rows = con.execute("SELECT count(*) FROM clean").fetchone()[0]
        invalid = (q.get("invalid_ohlc", 0) + q.get("negative_volume", 0)
                   + q.get("negative_amount", 0) + q.get("missing_factor", 0)
                   + q.get("non_positive_factor", 0))
        if invalid > max(100, before_rows // 100):
            raise ValueError(f"{dataset} has systemic invalid measurements: {q}")
        if dataset == "daily_bars" and invalid:
            con.execute("""DELETE FROM clean WHERE open IS NULL OR high IS NULL OR low IS NULL
                OR "close" IS NULL OR open<=0 OR high<=0 OR low<=0 OR "close"<=0 OR high<low
                OR high<greatest(open,"close") OR low>least(open,"close") OR volume<0 OR amount<0""")
        if dataset == "adjustment_factor" and invalid:
            con.execute("DELETE FROM clean WHERE factor IS NULL OR factor<=0")
        q.update({"raw_rows": con.execute("SELECT count(*) FROM raw").fetchone()[0],
                  "null_key_rows": issues[0], "out_of_window_rows": issues[1],
                  "canonical_rows": con.execute("SELECT count(*) FROM clean").fetchone()[0],
                  "excluded_invalid_measurement_rows": before_rows - con.execute("SELECT count(*) FROM clean").fetchone()[0]})
        q.update(duplicate_issues)
        quality[dataset] = q
        months = con.execute("SELECT DISTINCT year(trade_date), month(trade_date) FROM clean ORDER BY 1,2").fetchall()
        for year, month in months:
            path = store_root / dataset / f"year={year}" / f"month={month:02d}" / "part.parquet"
            _copy_atomic(con, """SELECT * FROM clean WHERE year(trade_date)=? AND month(trade_date)=?
                ORDER BY trade_date, security_id""", path, [year, month])
        con.execute("DROP TABLE raw")
        con.execute("DROP TABLE normalized")
        con.execute("DROP TABLE clean")
    stats = {dataset: _stats(con, store_root, dataset) for dataset in DATASETS}
    calendar_evidence = json.loads(calendar_capture.read_text(encoding="utf-8"))
    endpoint_for = {"security_master": "D1_basic", "daily_bars": "D3_bars",
                    "daily_status": "D4_D6_status", "adjustment_factor": "D5_factor"}
    lineage = {}
    for dataset in DATASETS:
        retrieved = (calendar_evidence["retrieved_at"] if dataset == "trading_calendar" else
            max(row["finished_at"] for row in state["records"].values()
                if row["dataset"] == endpoint_for[dataset]))
        lineage[dataset] = {"source": "AmazingData", "retrieved_at": retrieved,
            "date_start": stats[dataset].get("min_trade_date"),
            "date_end": stats[dataset].get("max_trade_date"),
            "row_count": stats[dataset]["rows"], "schema_version": 1}
    manifest = {"store_schema_version": STORE_SCHEMA_VERSION,
        "created_at": _now(), "updated_at": _now(), "source": "AmazingData",
        "sdk_version": "1.1.6", "initial_sync_start": start, "initial_sync_end": end,
        "calendar_checked_through": end,
        "datasets": list(DATASETS), "security_count": stats["security_master"]["rows"],
        "trading_day_count": stats["trading_calendar"]["rows"],
        "dataset_row_counts": {name: stats[name]["rows"] for name in DATASETS},
        "latest_trade_date": {name: stats[name].get("max_trade_date") for name in DATASETS},
        "schema_versions": {name: 1 for name in DATASETS}, "coverage": stats,
        "quality": quality, "lineage": lineage}
    _write_json(store_root / "metadata" / "manifest.json", manifest)
    con.close()
    return manifest


class LocalMarketStore:
    """DuckDB-backed offline reader over canonical Parquet, never a vendor client."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "metadata" / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest["store_schema_version"] != STORE_SCHEMA_VERSION:
            raise ValueError("Unsupported local market store schema")
        self.con = duckdb.connect()

    def close(self) -> None:
        self.con.close()

    def _load(self, dataset: str, codes: list[str] | None = None,
              start: date | str | None = None, end: date | str | None = None) -> pd.DataFrame:
        if dataset not in DATASETS:
            raise ValueError(dataset)
        paths = _partition_paths(self.root, dataset) if dataset in DAY_DATASETS else [self.root / dataset / "part.parquet"]
        filters, args = [], [[str(path) for path in paths]]
        if codes is not None:
            filters.append("security_id IN (SELECT UNNEST(?))")
            args.append(codes)
        if start is not None:
            filters.append("trade_date >= ?")
            args.append(str(start))
        if end is not None:
            filters.append("trade_date <= ?")
            args.append(str(end))
        predicate = " WHERE " + " AND ".join(filters) if filters else ""
        order = "trade_date, security_id" if dataset in DAY_DATASETS else "trade_date" if dataset == "trading_calendar" else "security_id"
        return self.con.execute(f"SELECT * FROM read_parquet(?, hive_partitioning=false) {predicate} ORDER BY {order}", args).df()

    def load_security_master(self, codes: list[str] | None = None) -> pd.DataFrame:
        return self._load("security_master", codes)

    def load_trading_calendar(self, start: date | str | None = None,
                              end: date | str | None = None) -> pd.DataFrame:
        return self._load("trading_calendar", start=start, end=end)

    def load_daily_bars(self, codes: list[str] | None = None, start=None, end=None) -> pd.DataFrame:
        return self._load("daily_bars", codes, start, end)

    def load_daily_status(self, codes: list[str] | None = None, start=None, end=None) -> pd.DataFrame:
        return self._load("daily_status", codes, start, end)

    def load_adjustment_factor(self, codes: list[str] | None = None, start=None, end=None) -> pd.DataFrame:
        return self._load("adjustment_factor", codes, start, end)
