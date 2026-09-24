"""Optional post-baseline D5 20-security batch comparison."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

PRIVATE = ROOT / ".local_research_data" / "stage1_amazingdata_benchmark"
ARTIFACTS = ROOT / "artifacts" / "stage1_amazingdata_100_benchmark"


def main() -> None:
    summary_path = ARTIFACTS / "benchmark_summary.json"
    if not summary_path.is_file():
        raise RuntimeError("Complete the serial 100-security baseline before optimization")
    sample = pd.read_csv(ARTIFACTS / "sample_universe.csv", dtype=str)
    codes = [code for board in ("SSE Main", "SZSE Main", "STAR", "ChiNext")
             for code in sample.loc[sample.board == board, "security_id"].iloc[:5]]
    if len(codes) != 20:
        raise ValueError("Expected 20 fixed-sample securities")
    logging.disable(logging.CRITICAL)
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
        cache_dir=PRIVATE / "sdk" / "optimized_factor20", retry_count=1,
        use_numba_compat=True)
    with open(os.devnull, "w", encoding="utf-8") as null_stream:
        with redirect_stdout(null_stream), redirect_stderr(null_stream):
            provider = adapter._call_with_retry(lambda session: session)
    start = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    request_finished = None
    error_type = None
    try:
        with open(os.devnull, "w", encoding="utf-8") as null_stream:
            with redirect_stdout(null_stream), redirect_stderr(null_stream):
                frame = provider.base.get_backward_factor(codes,
                    local_path=str(PRIVATE / "sdk" / "optimized_factor20") + os.sep,
                    is_local=False)
        request_finished = time.perf_counter()
        if not isinstance(frame, pd.DataFrame) or set(codes) - set(frame.columns):
            raise ValueError("Batch result missing requested securities")
        frame = frame.loc[(pd.to_datetime(frame.index) >= "2024-01-01") &
                          (pd.to_datetime(frame.index) <= "2026-09-23"), codes]
        listing = sample.set_index("security_id").listing_date
        for code in codes:
            listed = pd.Timestamp(listing[code])
            frame.loc[pd.to_datetime(frame.index) < listed, code] = pd.NA
        cache = PRIVATE / "optimized_factor20.csv.gz"
        frame.to_csv(cache, compression="gzip")
        cache_hash = sha256(cache.read_bytes()).hexdigest()
        rows = int(frame.notna().sum().sum())
    except Exception as exc:
        error_type = type(exc).__name__
        rows, cache_hash = 0, None
    seconds = round((request_finished or time.perf_counter()) - t0, 6)
    state = json.loads((PRIVATE / "checkpoint.json").read_text(encoding="utf-8"))
    baseline = [state["records"][f"D5_factor:{code}"]["elapsed_seconds"] for code in codes]
    payload = {"benchmark": "OPTIMIZED_20_SECURITY_RETEST", "source": "AmazingData",
        "endpoint": "D5_factor", "call_mode": "BATCH_SECURITIES", "batch_size": 20,
        "sdk_method_requests": 1, "started_at": start, "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_clock_seconds": seconds, "rows": rows, "cache_sha256": cache_hash,
        "error_type": error_type, "baseline_20_seconds": round(sum(baseline), 6),
        "speedup": round(sum(baseline) / seconds, 3) if not error_type and seconds else None,
        "comparison_note": "Five fixed codes per board; baseline uses the same 20 codes. Fresh SDK retrieval; batch returns a calendar-wide factor matrix."}
    with (ARTIFACTS / "optimized_factor_20.json").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
