"""Independent readback checks of committed local Parquet datasets."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.market_store import DATASETS, DAY_DATASETS


def main() -> None:
    store = ROOT / "data" / "market_store"
    manifest = json.loads((store / "metadata" / "manifest.json").read_text(encoding="utf-8"))
    con = duckdb.connect()
    checks = {}
    for dataset in DATASETS:
        paths = (sorted((store / dataset).glob("year=*/month=*/part.parquet")) if dataset in DAY_DATASETS
                 else [store / dataset / "part.parquet"])
        if not paths or any(not path.is_file() for path in paths):
            raise ValueError(f"Missing dataset: {dataset}")
        files = [str(path) for path in paths]
        count = con.execute("SELECT count(*) FROM read_parquet(?)", [files]).fetchone()[0]
        if count != manifest["dataset_row_counts"][dataset]:
            raise ValueError(f"Manifest row mismatch: {dataset}")
        check = {"rows": count, "partition_files": len(paths)}
        if dataset in DAY_DATASETS:
            key = con.execute("""SELECT count(*) FILTER (WHERE security_id IS NULL OR trade_date IS NULL),
                count(*) - count(DISTINCT (security_id, trade_date)),
                count(DISTINCT security_id), count(DISTINCT trade_date),
                min(trade_date), max(trade_date) FROM read_parquet(?)""", [files]).fetchone()
            check.update(null_keys=key[0], duplicate_keys=key[1], unique_securities=key[2],
                unique_trade_dates=key[3], min_trade_date=key[4].isoformat(),
                max_trade_date=key[5].isoformat())
            if key[0] or key[1] or check["max_trade_date"] != manifest["latest_trade_date"][dataset]:
                raise ValueError(f"Canonical key/date readback failed: {dataset}")
        elif dataset == "security_master":
            key = con.execute("SELECT count(*) - count(DISTINCT security_id) FROM read_parquet(?)", [files]).fetchone()[0]
            check["duplicate_security_id"] = key
            if key or count != manifest["security_count"]:
                raise ValueError("Security master readback failed")
        else:
            if count != manifest["trading_day_count"]:
                raise ValueError("Calendar readback failed")
        checks[dataset] = check
    factor_files = [str(path) for path in sorted((store / "adjustment_factor").glob("year=*/month=*/part.parquet"))]
    bad_factor = con.execute("SELECT count(*) FROM read_parquet(?) WHERE factor IS NULL OR factor<=0",
                             [factor_files]).fetchone()[0]
    if bad_factor:
        raise ValueError(f"Invalid canonical factors: {bad_factor}")
    bar_files = [str(path) for path in sorted((store / "daily_bars").glob("year=*/month=*/part.parquet"))]
    bad_bars = con.execute("""SELECT count(*) FROM read_parquet(?) WHERE open IS NULL OR high IS NULL
        OR low IS NULL OR "close" IS NULL OR open<=0 OR high<=0 OR low<=0 OR "close"<=0
        OR high<greatest(open,"close") OR low>least(open,"close") OR volume<0 OR amount<0""",
        [bar_files]).fetchone()[0]
    if bad_bars:
        raise ValueError(f"Invalid canonical bars: {bad_bars}")
    status_files = [str(path) for path in sorted((store / "daily_status").glob("year=*/month=*/part.parquet"))]
    status_missing = con.execute("""SELECT count(*) FROM read_parquet(?) WHERE preclose IS NULL
        OR high_limited IS NULL OR low_limited IS NULL OR price_high_lmt_rate IS NULL
        OR price_low_lmt_rate IS NULL OR is_st_sec IS NULL OR is_susp_sec IS NULL
        OR is_wd_sec IS NULL OR is_xr_sec IS NULL""", [status_files]).fetchone()[0]
    result = {"status": "PASS", "source": "independent DuckDB Parquet readback",
              "datasets": checks, "invalid_factor_rows": bad_factor,
              "invalid_bar_rows": bad_bars, "status_missing_field_rows": status_missing}
    output = ROOT / "artifacts" / "stage1_local_market_store" / "verification_receipt.json"
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "row_counts": {name: row["rows"] for name, row in checks.items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
