from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .backtest import ENTRY_PRICE_RULE, HOLDING_PERIODS, BacktestResult
from .outputs import _format_workbook, _normalize_xlsx_archive
from .pipeline import AnalysisRun


BACKTEST_DATE_COLUMNS = {
    "signal_date",
    "qualified_date",
    "last_limit_up_date",
    "entry_date",
    "run_as_of",
    "affected_date",
    *{f"exit_date_{period}d" for period in HOLDING_PERIODS},
    *{f"max_high_date_{period}d" for period in HOLDING_PERIODS},
    *{f"min_low_date_{period}d" for period in HOLDING_PERIODS},
}


def _serializable(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in BACKTEST_DATE_COLUMNS & set(result.columns):
        result[column] = result[column].map(
            lambda value: value.isoformat()
            if isinstance(value, (date, datetime))
            else ("" if pd.isna(value) else value)
        )
    return result


def _run_summary(backtest: BacktestResult, analysis: AnalysisRun) -> dict[str, Any]:
    events = backtest.events
    warnings = list(dict.fromkeys([*analysis.warnings, *backtest.warnings]))
    summary: dict[str, Any] = {
        "run_as_of": analysis.as_of_date.isoformat(),
        "start_date": analysis.start_date.isoformat(),
        "holding_periods": list(HOLDING_PERIODS),
        "entry_price_rule": ENTRY_PRICE_RULE,
        "price_adjustment": analysis.config.price_adjustment,
        "raw_signal_count": int(len(events)),
        "md1_signal_count": int((events["signal_type"] == "MD_1").sum()),
        "md2_signal_count": int((events["signal_type"] == "MD_2").sum()),
        "first_signal_count": int(len(backtest.first_signal_events)),
        "entry_completed_count": int((events["entry_status"] == "COMPLETED").sum()),
        "no_entry_count": int((events["entry_status"] == "NO_ENTRY_DATA").sum()),
        "warnings": warnings,
    }
    for period in HOLDING_PERIODS:
        summary[f"completed_{period}d_count"] = int(
            events[f"return_{period}d"].notna().sum()
        )
    return summary


def write_backtest_outputs(
    backtest: BacktestResult,
    analysis: AnalysisRun,
    output_dir: Path | str,
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    events_path = destination / "backtest_events.csv"
    first_path = destination / "first_signal_events.csv"
    workbook_path = destination / "backtest_summary.xlsx"
    summary_path = destination / "backtest_run_summary.json"

    events = _serializable(backtest.events)
    first = _serializable(backtest.first_signal_events)
    signal_summary = _serializable(backtest.signal_summary)
    stratified = _serializable(backtest.stratified_summary)
    incomplete = _serializable(backtest.incomplete_samples)
    events.to_csv(events_path, index=False, lineterminator="\n", float_format="%.10g")
    first.to_csv(first_path, index=False, lineterminator="\n", float_format="%.10g")

    parameters = pd.DataFrame(
        [
            {"parameter": "holding_periods", "value": ",".join(map(str, HOLDING_PERIODS))},
            {"parameter": "entry_price_rule", "value": ENTRY_PRICE_RULE},
            {"parameter": "rsi_period", "value": analysis.config.rsi_period},
            {
                "parameter": "candidate_rsi_threshold",
                "value": analysis.config.candidate_rsi_threshold,
            },
            {"parameter": "md2_rsi_floor", "value": analysis.config.md2_rsi_floor},
            {
                "parameter": "candidate_max_observation_days",
                "value": analysis.config.candidate_max_observation_days,
            },
            {
                "parameter": "price_adjustment",
                "value": analysis.config.price_adjustment,
            },
            {"parameter": "run_as_of", "value": analysis.as_of_date.isoformat()},
        ]
    )
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        events.to_excel(writer, sheet_name="事件明细", index=False)
        first.to_excel(writer, sheet_name="首信号明细", index=False)
        signal_summary.to_excel(writer, sheet_name="信号汇总", index=False)
        stratified.to_excel(writer, sheet_name="分层统计", index=False)
        incomplete.to_excel(writer, sheet_name="未完成样本", index=False)
        parameters.to_excel(writer, sheet_name="参数", index=False)
    _format_workbook(workbook_path, analysis.as_of_date)
    _normalize_xlsx_archive(workbook_path, analysis.as_of_date)

    summary_path.write_text(
        json.dumps(
            _run_summary(backtest, analysis),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "backtest_events": events_path,
        "first_signal_events": first_path,
        "backtest_summary": workbook_path,
        "backtest_run_summary": summary_path,
    }
