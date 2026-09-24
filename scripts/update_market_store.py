"""Scheduler-ready AmazingData -> local Parquet market store updater."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import logging
import os
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.data.amazingdata import checked_sdk_bars
from research.market_update import update_market_store
from three_board_rsi_entry.market_data import AmazingDataAdapter, _install_numba_compat


class AmazingDataFetcher:
    def __init__(self, root: Path):
        logging.disable(logging.CRITICAL)
        _install_numba_compat()
        self.adapter = AmazingDataAdapter(legacy_provider_root=ROOT.parent / "yh",
            cache_dir=root / "metadata" / "sdk", retry_count=1, use_numba_compat=True)
        self.provider = None
        self.root = root
        self.remote_calls = 0

    def _session(self):
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                return self.adapter._call_with_retry(lambda provider: provider)

    def _call(self, operation):
        if self.provider is None:
            self.provider = self._session()
        for attempt in range(3):
            try:
                self.remote_calls += 1
                with open(os.devnull, "w", encoding="utf-8") as sink:
                    with redirect_stdout(sink), redirect_stderr(sink):
                        return operation(self.provider)
            except Exception as exc:
                if attempt == 2 or not (isinstance(exc, (ConnectionError, TimeoutError, TypeError))
                                       or "rate" in str(exc).lower()):
                    raise
                self.adapter._session_provider = None
                self.provider = self._session()
                time.sleep(min(2 ** attempt, 4))
        raise AssertionError("unreachable")

    def calendar(self, end: str) -> list[str]:
        end_int = int(end.replace("-", ""))
        calendars = []
        for market in ("SH", "SZ"):
            values = self._call(lambda p: p.base.get_calendar(market=market, date=end_int))
            calendars.append([pd.Timestamp(str(day)).strftime("%Y-%m-%d") for day in values
                              if "2024-01-01" <= pd.Timestamp(str(day)).strftime("%Y-%m-%d") <= end])
        if calendars[0] != calendars[1]:
            raise ValueError("SH/SZ incremental calendar disagreement")
        return calendars[0]

    def security_master(self, end: str) -> pd.DataFrame:
        end_int = int(end.replace("-", ""))
        codes = self._call(lambda p: p.base.get_hist_code_list(
            security_type="EXTRA_STOCK_A", start_date=end_int, end_date=end_int,
            local_path=str(self.root / "metadata" / "sdk" / "universe") + os.sep))
        codes = sorted({code for code in codes if code.endswith((".SH", ".SZ"))})
        rows = []
        for offset in range(0, len(codes), 80):
            group = codes[offset:offset + 80]
            result = self._call(lambda p: p.ad.InfoData().get_stock_basic(group))
            rows.append(pd.DataFrame(result))
        raw = pd.concat(rows, ignore_index=True)
        if set(raw.MARKET_CODE) != set(codes):
            raise ValueError("Incremental basic metadata mismatch")
        plate = raw.LISTPLATE_NAME
        board = plate.map({"科创板": "STAR", "创业板": "ChiNext"})
        board.loc[plate.eq("主板") & raw.MARKET_CODE.str.endswith(".SH")] = "SSE Main"
        board.loc[plate.eq("主板") & raw.MARKET_CODE.str.endswith(".SZ")] = "SZSE Main"
        return pd.DataFrame({"security_id": raw.MARKET_CODE, "exchange": raw.MARKET_CODE.str[-2:],
            "board": board, "listing_date": pd.to_datetime(raw.LISTDATE.astype(str), format="%Y%m%d"),
            "delisting_date": pd.to_datetime(raw.DELISTDATE.astype("Int64").astype(str),
                format="%Y%m%d", errors="coerce"), "security_name": raw.SECURITY_NAME})

    def bars(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self._call(lambda p: checked_sdk_bars(p, code,
            pd.Timestamp(start).date(), pd.Timestamp(end).date()))

    def status(self, code: str, start: str, end: str) -> pd.DataFrame:
        values = self._call(lambda p: p.ad.InfoData().get_history_stock_status([code],
            local_path=str(self.root / "metadata" / "sdk" / "status") + os.sep,
            is_local=False, begin_date=int(start.replace("-", "")),
            end_date=int(end.replace("-", ""))))
        if not isinstance(values, dict) or set(values) != {code}:
            raise ValueError("Incremental status mapping mismatch")
        return pd.DataFrame(values[code])

    def factors(self, codes: list[str]) -> pd.DataFrame:
        # SDK 1.1.6 exposes no date range for this endpoint. Call only during
        # explicit periodic refresh, then normalize/upsert bounded trailing rows.
        return self._call(lambda p: p.base.get_backward_factor(codes,
            local_path=str(self.root / "metadata" / "sdk" / "factor") + os.sep,
            is_local=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "data" / "market_store")
    parser.add_argument("--end", required=True, help="Last eligible EOD session (YYYY-MM-DD)")
    parser.add_argument("--refresh-start", help="Explicit bounded D3/D4 repair start")
    parser.add_argument("--refresh-dataset", action="append", choices=("daily_bars", "daily_status"),
                        help="Limit explicit refresh to this dataset; repeat for both")
    parser.add_argument("--security", action="append", help="Limit explicit refresh to this security ID")
    parser.add_argument("--refresh-factors", action="store_true", help="Periodic D5 refresh; SDK downloads full factor response")
    parser.add_argument("--factor-trailing-days", type=int, default=20)
    args = parser.parse_args()
    fetcher = AmazingDataFetcher(args.root)
    receipt = update_market_store(args.root, args.end, fetcher,
        refresh_start=args.refresh_start,
        refresh_datasets=tuple(args.refresh_dataset or ("daily_bars", "daily_status")),
        refresh_codes=tuple(args.security) if args.security else None,
        refresh_factors=args.refresh_factors,
        factor_trailing_days=args.factor_trailing_days)
    print(json.dumps(receipt, ensure_ascii=False, default=str))


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
    os._exit(0)  # SDK 1.1.6 logout is unstable on this Windows runtime.
