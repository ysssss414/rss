"""Capture frozen SSE/SZSE vendor sessions privately in an isolated SDK worker.

Only an ignored local file receives full session rows. Public evidence is derived
later by exact reconciliation and hashes, never by committing this output.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local_research_data"


def worker(output: Path) -> None:
    from importlib.metadata import version

    import pandas as pd

    sys.path.insert(0, str(ROOT))
    from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

    logging.disable(logging.CRITICAL)
    try:
        from loguru import logger
        logger.remove()
    except ImportError:
        pass
    _install_numba_compat()
    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
                                cache_dir=Path.cwd(), retry_count=1,
                                retry_delay_seconds=0, use_numba_compat=True)
    provider = adapter._call_with_retry(lambda session: session)
    result = {"schema": "private-research-calendar-capture/1",
              "provider": "AmazingData", "provider_version": version("AmazingData"),
              "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "request": {"markets": ["SH", "SZ"], "as_of": 20260923},
              "sessions": {}}
    for market in ("SH", "SZ"):
        values = provider.base.get_calendar(market=market, date=20260923)
        dates = [pd.Timestamp(str(value)).strftime("%Y-%m-%d") for value in values]
        if dates != sorted(set(dates)):
            raise ValueError("SDK calendar not unique and sorted")
        result["sessions"][market] = dates
    output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        raise SystemExit("Output must be a new file in ignored .local_research_data")
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        try:
            worker(output)
        except BaseException as exc:
            output.with_suffix(".error.json").write_text(
                json.dumps({"completed": False, "error_type": type(exc).__name__}),
                encoding="utf-8")
        os._exit(0)  # SDK destructor/logout behavior is not independently qualified.
    work = PRIVATE / "calendar_sdk_worker"
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
               "--output", str(output)]
    try:
        process = subprocess.run(command, cwd=work, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=180)
        print(json.dumps({"worker_exit_code": process.returncode,
                          "capture_exists": output.exists()}))
    except subprocess.TimeoutExpired:
        print(json.dumps({"worker_state": "TIMED_OUT", "capture_exists": output.exists()}))


if __name__ == "__main__":
    main()
