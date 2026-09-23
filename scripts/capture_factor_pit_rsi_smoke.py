"""One-security, listing-prefix OHLC/status capture for a private D5 RSI smoke."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local_research_data"
CODE = "001331.SZ"
START = "2022-09-08"
END = "2025-06-05"


def capture(output: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from research.data.amazingdata import checked_sdk_bars
    from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat

    logging.disable(logging.CRITICAL)
    try:
        from loguru import logger
        logger.remove()
    except ImportError:
        pass
    _install_numba_compat()
    import AmazingData as ad

    adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh", cache_dir=output / "sdk",
                                retry_count=1, retry_delay_seconds=0, use_numba_compat=True)
    provider = adapter._call_with_retry(lambda session: session)
    info = ad.InfoData()
    receipt = {"schema": "private-factor-pit-rsi-smoke/1", "security": CODE,
               "start": START, "end": END, "retrieved_at": datetime.now(timezone.utc).isoformat(),
               "calls": {}}
    for name, operation in (
        ("bars", lambda: checked_sdk_bars(provider, CODE, pd.Timestamp(START).date(),
                                          pd.Timestamp(END).date())),
        ("status", lambda: info.get_history_stock_status(
            [CODE], local_path=str(output / "sdk" / "status") + os.sep,
            is_local=False, begin_date=20220908, end_date=20250605)),
        ("calendar", lambda: provider.base.get_calendar(market="SH", date=20250605)),
    ):
        try:
            if name == "status":
                (output / "sdk" / "status").mkdir(parents=True, exist_ok=True)
            value = operation()
            if name == "calendar":
                days = [pd.Timestamp(str(day)).strftime("%Y-%m-%d") for day in value]
                days = [day for day in days if START <= day <= END]
                path = output / "calendar.json"
                path.write_text(json.dumps(days), encoding="utf-8")
                rows = len(days)
                columns = None
            elif name == "status" and isinstance(value, dict):
                if set(value) != {CODE} or not isinstance(value[CODE], pd.DataFrame):
                    raise TypeError("Unexpected status mapping")
                frame = value[CODE]
                path = output / "status.h5"
                frame.to_hdf(path, key="status", mode="w")
                rows = len(frame)
                columns = [str(column) for column in frame.columns]
            else:
                if not isinstance(value, pd.DataFrame):
                    raise TypeError("Expected status or bar DataFrame")
                path = output / f"{name}.h5"
                value.to_hdf(path, key=name, mode="w")
                rows = len(value)
                columns = [str(column) for column in value.columns]
            receipt["calls"][name] = {"status": "RETURNED", "rows": rows, "columns": columns,
                                      "sha256": sha256(path.read_bytes()).hexdigest()}
        except Exception as exc:
            receipt["calls"][name] = {"status": "ERROR", "error_type": type(exc).__name__}
        (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        raise SystemExit("Choose a new ignored private output directory")
    output.mkdir(parents=True)
    if args.worker:
        try:
            capture(output)
        except BaseException as exc:
            (output / "error.json").write_text(json.dumps({"error_type": type(exc).__name__}),
                                                 encoding="utf-8")
        os._exit(0)  # Native SDK logout is not separately qualified.
    output.rmdir()
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--output", str(output)]
    try:
        process = subprocess.run(command, cwd=PRIVATE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=480)
        print(json.dumps({"worker_exit_code": process.returncode,
                          "receipt_exists": (output / "receipt.json").exists()}))
    except subprocess.TimeoutExpired:
        print(json.dumps({"worker_state": "TIMED_OUT",
                          "receipt_exists": (output / "receipt.json").exists()}))


if __name__ == "__main__":
    main()
