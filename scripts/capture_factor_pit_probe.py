"""Bounded six-security AmazingData D5 capture; all vendor rows stay ignored/local."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local_research_data"
CODES = ("600519.SH", "600030.SH", "300059.SZ", "002230.SZ", "000001.SZ", "600887.SH")
FIRST_ANNOUNCEMENT = 20140101
LAST_ANNOUNCEMENT = 20260923


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def event_payload(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): event_payload(frame) for key, frame in value.items()}
    if isinstance(value, pd.DataFrame):
        return {"columns": [str(column) for column in value.columns],
                "rows": json.loads(value.to_json(orient="records", date_format="iso"))}
    raise TypeError("Unexpected corporate-action result shape")


def capture(output: Path, codes: tuple[str, ...]) -> None:
    sys.path.insert(0, str(ROOT))
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
    receipt = {"schema": "private-factor-pit-probe/1", "provider": "AmazingData",
               "provider_version": version("AmazingData"),
               "retrieved_at": datetime.now(timezone.utc).isoformat(),
               "request": {"codes": codes, "announcement_start": FIRST_ANNOUNCEMENT,
                           "announcement_end": LAST_ANNOUNCEMENT, "is_local": False},
               "calls": {}}
    for name, operation in (
        ("backward_factor", lambda local: provider.base.get_backward_factor(
            list(codes), local_path=local, is_local=False)),
        ("single_event_factor", lambda local: provider.base.get_adj_factor(
            list(codes), local_path=local, is_local=False)),
        ("dividend", lambda local: info.get_dividend(
            list(codes), local_path=local, is_local=False,
            begin_date=FIRST_ANNOUNCEMENT, end_date=LAST_ANNOUNCEMENT)),
        ("right_issue", lambda local: info.get_right_issue(
            list(codes), local_path=local, is_local=False,
            begin_date=FIRST_ANNOUNCEMENT, end_date=LAST_ANNOUNCEMENT)),
    ):
        cache = output / "sdk" / name
        cache.mkdir(parents=True, exist_ok=True)
        try:
            result = operation(str(cache) + os.sep)
            if name.endswith("factor"):
                if not isinstance(result, pd.DataFrame):
                    raise TypeError("Unexpected factor result shape")
                path = output / f"{name}.h5"
                result.to_hdf(path, key=name, mode="w")
                count = len(result)
                columns = [str(column) for column in result.columns]
            else:
                path = output / f"{name}.json"
                payload = event_payload(result)
                path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False), encoding="utf-8")
                count = sum(len(item["rows"]) for item in payload.values()) if isinstance(payload, dict) and "rows" not in payload else len(payload["rows"])
                columns = None
            receipt["calls"][name] = {"status": "RETURNED", "row_count": count,
                                      "columns": columns, "sha256": digest(path),
                                      "completed_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            receipt["calls"][name] = {"status": "ERROR", "error_type": type(exc).__name__}
        (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codes", nargs="+", default=CODES)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    codes = tuple(args.codes)
    if not 1 <= len(codes) <= 6 or len(set(codes)) != len(codes) or any(
            not re.fullmatch(r"\d{6}\.(?:SH|SZ)", code) for code in codes):
        raise SystemExit("Provide one to six distinct SSE/SZSE securities")
    output = args.output.resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        raise SystemExit("New capture directory must be under ignored .local_research_data")
    output.mkdir(parents=True)
    if args.worker:
        try:
            capture(output, codes)
        except BaseException as exc:
            (output / "error.json").write_text(json.dumps({"error_type": type(exc).__name__}), encoding="utf-8")
        os._exit(0)  # SDK native shutdown is not separately qualified.
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--output",
               str(output), "--codes", *codes]
    # Worker must create the directory itself after this preflight.
    output.rmdir()
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
