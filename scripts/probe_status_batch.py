"""Bounded status batch probe against five already cached securities."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import argparse
import csv
import json
import logging
import os
from pathlib import Path
import sys
import time
from numbers import Number

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, choices=(5, 20, 50), default=5)
    args = parser.parse_args()
    with (ROOT / "artifacts" / "stage1_amazingdata_100_benchmark" / "sample_universe.csv").open(
            encoding="utf-8", newline="") as stream:
        codes = [row["security_id"] for _, row in zip(range(args.count), csv.DictReader(stream))]
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    private = ROOT / ".local_research_data" / "market_store" / "batch_probe"
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh", cache_dir=private / "sdk",
                                 retry_count=1, use_numba_compat=True)
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            provider = adapter._call_with_retry(lambda p: p)
    began = time.perf_counter()
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            values = provider.ad.InfoData().get_history_stock_status(codes,
                local_path=str(private / "sdk" / "status") + os.sep,
                is_local=False, begin_date=20240101, end_date=20260923)
    elapsed = time.perf_counter() - began
    if not isinstance(values, dict) or set(values) != set(codes):
        raise ValueError("Batch status response identity mismatch")
    old_root = ROOT / ".local_research_data" / "stage1_amazingdata_benchmark" / "raw"
    checks = {}
    def rows(frame):
        normalized = []
        for item in frame.itertuples(index=False, name=None):
            normalized.append(tuple(None if pd.isna(value) else round(float(value), 8)
                                    if isinstance(value, Number) else str(value) for value in item))
        return sorted(normalized, key=str)
    for code in codes:
        current = pd.DataFrame(values[code])
        old = pd.read_csv(old_root / f"D4_D6_status_{code.replace('.', '_')}.csv.gz")
        if set(current) != set(old) or len(current) != len(old):
            checks[code] = "SCHEMA_OR_ROW_MISMATCH"
        else:
            current = current[sorted(current)]
            old = old[sorted(old)]
            for column in current.columns:
                if column != "MARKET_CODE":
                    current[column] = pd.to_numeric(current[column], errors="coerce")
                    old[column] = pd.to_numeric(old[column], errors="coerce")
            checks[code] = "MATCH" if rows(current) == rows(old) else "VALUE_MISMATCH"
    print(json.dumps({"security_count": len(codes), "elapsed_seconds": round(elapsed, 3),
                      "total_rows": sum(len(pd.DataFrame(values[code])) for code in codes),
                      "matching_securities": sum(value == "MATCH" for value in checks.values()),
                      "mismatches": {code: value for code, value in checks.items() if value != "MATCH"}},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
