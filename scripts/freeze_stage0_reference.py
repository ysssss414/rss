"""One-time capture against the unmodified strategy. Never overwrite a reference."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.backtest import run_event_backtest
from three_board_rsi_entry.market_data import AmazingDataMarketDataProvider
from three_board_rsi_entry.pipeline import run_analysis
from tests.helpers import CODE, board_frame, indicator_bars, trading_days
from three_board_rsi_entry.replay import run_replay

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "baselines" / "stage0"
ATOL = 1e-10
RTOL = 1e-12


def records(frame):
    return json.loads(frame.to_json(orient="table", index=False, date_format="iso", double_precision=15))["data"]


def outputs(result, bars, days, config):
    event = run_event_backtest(signals=result.signals, candidate_cycles=result.candidate_cycles,
                               indicator_bars=bars, trading_days=days, as_of_date=days[-1], config=config)
    return {"candidate_cycles": records(result.candidate_cycles), "signals": records(result.signals),
            **{name: records(getattr(event, name)) for name in
               ("events", "first_signal_events", "signal_summary", "stratified_summary", "incomplete_samples")}}


def main():
    if DEST.exists():
        raise SystemExit("Reference already exists; refusing overwrite")
    expected_head = "fb09873473188a8671d2cff0132b9d932e9c6cb6"
    actual_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    runtime_changes = subprocess.check_output(["git", "diff", "HEAD", "--", "three_board_rsi_entry"], cwd=ROOT)
    if actual_head != expected_head or runtime_changes:
        raise SystemExit("Capture requires the original HEAD with an unchanged runtime")
    cases = []
    specs = [
        ("md1_success", {"low": {1: 9.0}}),
        ("md1_first_failure", {"low": {1: 9.0, 2: 9.0}, "close": {1: 9.0}}),
        ("md2_recovery_70", {"rsi": {1: 65.0, 2: 70.0}}),
        ("md2_floor", {"rsi": {1: 59.0, 2: 75.0}}),
        ("md2_nine_days", {"rsi": {**{i: 65.0 for i in range(1, 10)}, 10: 70.0}}),
        ("md2_timeout", {"rsi": {**{i: 65.0 for i in range(1, 11)}, 11: 75.0}}),
        ("qualification_exact_70", {"rsi": {0: 70.0}}),
        ("expiry_20", {"low": {20: 9.0}}),
        ("expiry_21", {"low": {21: 9.0}}),
        ("supersession", {"low": {1: 9.0, 5: 9.0}}),
        ("suspension_entry", {"low": {1: 9.0}}),
        ("missing_outcome", {"low": {1: 9.0}}),
    ]
    config = StrategyConfig()
    for name, kwargs in specs:
        days = trading_days(24)
        boards = board_frame([(days[0], CODE, 3, "fixture")])
        if name == "supersession":
            boards = board_frame([(days[0], CODE, 3, "fixture"), (days[4], CODE, 3, "fixture")])
        bars = indicator_bars(days, **kwargs)
        if name == "suspension_entry":
            bars.loc[2, "suspended"] = True
            bars.loc[2, ["open", "high", "low", "close", "rsi14", "ma5"]] = None
        if name == "missing_outcome":
            bars = bars.drop(index=7)
        result = run_replay(boards=boards, indicator_bars=bars, trading_days=days,
                            start_date=days[0], as_of_date=days[-1], config=config)
        cases.append({"name": name, "days": [str(d) for d in days], "boards": records(boards),
                      "bars": records(bars), "expected": outputs(result, bars, days, config)})

    # The checked-in input is an unfinished worksheet, not a runnable historical baseline.
    head = subprocess.check_output(["git", "show", "HEAD:inputs/three_board_daily_input.xlsx"], cwd=ROOT)
    current_path = ROOT / "inputs" / "three_board_daily_input.xlsx"
    current = current_path.read_bytes()
    sheets = pd.read_excel(io.BytesIO(current), sheet_name=None)
    confirmations, boards = list(sheets.values())
    boards = boards.loc[boards.ts_code == "000020.SZ"].copy()
    if boards.empty:
        raise SystemExit("Fixed historical security missing from user input")
    raw_path = ROOT / ".cache/three_board_rsi_entry/raw_daily/000020_SZ.csv"
    raw = pd.read_csv(raw_path)
    factor_path = ROOT / ".cache/three_board_rsi_entry/factors/basedata/backward_factor/backward_factor.h5"
    factors = pd.read_hdf(factor_path, key="backward_factor")
    if list(factors.columns) != ["000020.SZ"]:
        raise SystemExit("Historical factor identity changed")
    start, end = date(2026, 1, 7), date(2026, 7, 29)
    days = sorted(pd.to_datetime(raw.trade_date).dt.date.unique())
    # Fixed observed dates, explicitly not asserted to be an exchange calendar.
    raw.trade_date = pd.to_datetime(raw.trade_date).dt.date
    adjusted = AmazingDataMarketDataProvider._apply_forward_adjustment(raw, factors, "000020.SZ")

    DEST.mkdir(parents=True)
    (DEST / "head_input.xlsx").write_bytes(head)
    with pd.ExcelWriter(DEST / "historical_input.xlsx", engine="openpyxl") as writer:
        confirmations.to_excel(writer, sheet_name="交易日确认", index=False)
        boards.to_excel(writer, sheet_name="三连板股票", index=False)
    raw.to_csv(DEST / "historical_raw.csv", index=False)
    factors.loc[(factors.index.date >= days[0]) & (factors.index.date <= end)].to_csv(DEST / "historical_factors.csv", index_label="trade_date")

    class FixedProvider:
        def trading_days(self, first, last):
            return [d for d in days if first <= d <= last]
        def daily_bars(self, codes, first, last, **kwargs):
            return adjusted.loc[adjusted.ts_code.isin(codes) & adjusted.trade_date.between(first, last)].copy()
    run = run_analysis(input_path=DEST / "historical_input.xlsx", start_date=start, as_of_date=end,
                       provider=FixedProvider(), config=config, allow_incomplete=True)
    historical = {"start": str(start), "end": str(end), "calendar": [str(d) for d in days],
                  "expected": outputs(run.result, run.indicator_bars, list(run.trading_days), config),
                  "quality": ["volume_not_retained_by_legacy_cache", "calendar_is_observed_dates", "historical_status_unverified"],
                  "research_eligible": False}
    identity = {"git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                "atol": ATOL, "rtol": RTOL, "config": config.as_dict(),
                "head_input_sha256": hashlib.sha256(head).hexdigest(),
                "current_user_input_sha256": hashlib.sha256(current).hexdigest(),
                "head_input_runnable": False,
                "historical_input_origin": "explicit 000020.SZ subset of user input, independent from HEAD identity",
                "raw_origin_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "factor_origin_sha256": hashlib.sha256(factor_path.read_bytes()).hexdigest()}
    payload = {"identity": identity, "semantic_cases": cases, "historical": historical}
    (DEST / "reference.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.iterdir())}
    (DEST / "checksums.json").write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("Frozen", len(cases), "semantic cases; historical cycles/signals:", len(run.result.candidate_cycles), len(run.result.signals))


if __name__ == "__main__":
    main()
