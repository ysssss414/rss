from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .config import StrategyConfig


HOLDING_PERIODS = (1, 3, 5, 10)
ENTRY_PRICE_RULE = "NEXT_TRADABLE_OPEN"

EVENT_COLUMNS = [
    "signal_date",
    "candidate_cycle_id",
    "ts_code",
    "stock_name",
    "signal_type",
    "qualified_date",
    "qualified_board_count",
    "last_limit_up_date",
    "max_board_count",
    "observation_day",
    "signal_rsi14",
    "signal_previous_rsi14",
    "signal_close",
    "signal_ma5",
    "entry_status",
    "entry_date",
    "entry_delay_market_days",
    "entry_price",
    "signal_close_to_entry_gap",
    "entry_open_equals_high_equals_low_equals_close",
    *[f"return_{period}d" for period in HOLDING_PERIODS],
    *[f"mfe_{period}d" for period in HOLDING_PERIODS],
    *[f"mae_{period}d" for period in HOLDING_PERIODS],
    *[f"exit_date_{period}d" for period in HOLDING_PERIODS],
    *[f"max_high_date_{period}d" for period in HOLDING_PERIODS],
    *[f"min_low_date_{period}d" for period in HOLDING_PERIODS],
    *[f"holding_status_{period}d" for period in HOLDING_PERIODS],
    "price_adjustment",
    "run_as_of",
    "data_complete",
]

SUMMARY_COLUMNS = [
    "sample_scope",
    "holding_period",
    "sample_count",
    "completed_count",
    "missing_count",
    "win_rate",
    "mean_return",
    "median_return",
    "p25_return",
    "p75_return",
    "min_return",
    "max_return",
    "mean_mfe",
    "median_mfe",
    "mean_mae",
    "median_mae",
    "average_positive_return",
    "average_negative_return",
    "payoff_ratio",
    "return_gt_5pct_rate",
    "return_lt_minus5pct_rate",
]

STRATIFIED_COLUMNS = [
    "dimension",
    "group",
    "sample_count",
    "completed_5d_count",
    "5d_win_rate",
    "5d_mean_return",
    "5d_median_return",
    "5d_mean_mfe",
    "5d_mean_mae",
    "completed_10d_count",
    "10d_win_rate",
    "10d_mean_return",
    "10d_median_return",
]

INCOMPLETE_COLUMNS = [
    "signal_date",
    "candidate_cycle_id",
    "ts_code",
    "signal_type",
    "issue_type",
    "holding_period",
    "affected_date",
    "details",
]

REQUIRED_SIGNAL_COLUMNS = {
    "signal_date",
    "candidate_cycle_id",
    "ts_code",
    "stock_name",
    "signal_type",
    "qualified_date",
    "qualified_board_count",
    "last_limit_up_date",
    "max_board_count",
    "observation_day",
    "rsi14",
    "previous_rsi14",
    "close",
    "ma5",
}


@dataclass(frozen=True)
class BacktestResult:
    events: pd.DataFrame
    first_signal_events: pd.DataFrame
    signal_summary: pd.DataFrame
    stratified_summary: pd.DataFrame
    incomplete_samples: pd.DataFrame
    warnings: list[str]


def _date_value(value: object) -> date:
    return pd.Timestamp(value).date()


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _valid_ohlc(bar: dict[str, Any]) -> bool:
    values = [_number(bar.get(column)) for column in ("open", "high", "low", "close")]
    if any(value is None or value <= 0 for value in values):
        return False
    open_price, high, low, close = values
    assert open_price is not None and high is not None
    assert low is not None and close is not None
    return high >= max(open_price, low, close) and low <= min(
        open_price, high, close
    )


def _blank_event(signal: Any, data_complete: bool, as_of_date: date) -> dict[str, Any]:
    row: dict[str, Any] = {column: np.nan for column in EVENT_COLUMNS}
    row.update(
        {
            "signal_date": signal.signal_date,
            "candidate_cycle_id": signal.candidate_cycle_id,
            "ts_code": signal.ts_code,
            "stock_name": signal.stock_name,
            "signal_type": signal.signal_type,
            "qualified_date": signal.qualified_date,
            "qualified_board_count": signal.qualified_board_count,
            "last_limit_up_date": signal.last_limit_up_date,
            "max_board_count": signal.max_board_count,
            "observation_day": signal.observation_day,
            "signal_rsi14": _number(signal.rsi14),
            "signal_previous_rsi14": _number(signal.previous_rsi14),
            "signal_close": _number(signal.close),
            "signal_ma5": _number(signal.ma5),
            "entry_date": None,
            "entry_delay_market_days": None,
            "entry_open_equals_high_equals_low_equals_close": None,
            "price_adjustment": getattr(signal, "price_adjustment", None),
            "run_as_of": as_of_date,
            "data_complete": data_complete,
        }
    )
    for period in HOLDING_PERIODS:
        row[f"exit_date_{period}d"] = None
        row[f"max_high_date_{period}d"] = None
        row[f"min_low_date_{period}d"] = None
    return row


def _issue(
    signal: Any,
    issue_type: str,
    *,
    details: str,
    holding_period: int | None = None,
    affected_date: date | None = None,
) -> dict[str, Any]:
    return {
        "signal_date": signal.signal_date,
        "candidate_cycle_id": signal.candidate_cycle_id,
        "ts_code": signal.ts_code,
        "signal_type": signal.signal_type,
        "issue_type": issue_type,
        "holding_period": holding_period,
        "affected_date": affected_date,
        "details": details,
    }


def _metric_row(frame: pd.DataFrame, period: int) -> dict[str, Any]:
    returns = pd.to_numeric(frame.get(f"return_{period}d"), errors="coerce")
    completed = returns.dropna()
    mfe = pd.to_numeric(
        frame.loc[completed.index, f"mfe_{period}d"], errors="coerce"
    ).dropna()
    mae = pd.to_numeric(
        frame.loc[completed.index, f"mae_{period}d"], errors="coerce"
    ).dropna()
    positive = completed[completed > 0]
    negative = completed[completed < 0]
    positive_mean = float(positive.mean()) if not positive.empty else np.nan
    negative_mean = float(negative.mean()) if not negative.empty else np.nan
    payoff_ratio = (
        positive_mean / abs(negative_mean)
        if not pd.isna(positive_mean) and not pd.isna(negative_mean)
        else np.nan
    )
    return {
        "sample_count": int(len(frame)),
        "completed_count": int(len(completed)),
        "missing_count": int(len(frame) - len(completed)),
        "win_rate": float((completed > 0).mean()) if not completed.empty else np.nan,
        "mean_return": float(completed.mean()) if not completed.empty else np.nan,
        "median_return": float(completed.median()) if not completed.empty else np.nan,
        "p25_return": float(completed.quantile(0.25)) if not completed.empty else np.nan,
        "p75_return": float(completed.quantile(0.75)) if not completed.empty else np.nan,
        "min_return": float(completed.min()) if not completed.empty else np.nan,
        "max_return": float(completed.max()) if not completed.empty else np.nan,
        "mean_mfe": float(mfe.mean()) if not mfe.empty else np.nan,
        "median_mfe": float(mfe.median()) if not mfe.empty else np.nan,
        "mean_mae": float(mae.mean()) if not mae.empty else np.nan,
        "median_mae": float(mae.median()) if not mae.empty else np.nan,
        "average_positive_return": positive_mean,
        "average_negative_return": negative_mean,
        "payoff_ratio": payoff_ratio,
        "return_gt_5pct_rate": (
            float((completed > 0.05).mean()) if not completed.empty else np.nan
        ),
        "return_lt_minus5pct_rate": (
            float((completed < -0.05).mean()) if not completed.empty else np.nan
        ),
    }


def build_signal_summary(
    events: pd.DataFrame,
    first_signal_events: pd.DataFrame,
) -> pd.DataFrame:
    scopes = [
        ("ALL_SIGNALS", events),
        ("MD_1", events[events["signal_type"] == "MD_1"]),
        ("MD_2", events[events["signal_type"] == "MD_2"]),
        ("FIRST_SIGNAL_PER_CYCLE", first_signal_events),
    ]
    rows = []
    for scope_name, frame in scopes:
        for period in HOLDING_PERIODS:
            rows.append(
                {
                    "sample_scope": scope_name,
                    "holding_period": period,
                    **_metric_row(frame, period),
                }
            )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _board_bucket(value: object) -> str:
    number = _number(value)
    if number == 3:
        return "3板"
    if number == 4:
        return "4板"
    if number is not None and number >= 5:
        return "5板及以上"
    return "未知"


def _observation_bucket(value: object) -> str:
    number = _number(value)
    if number is None:
        return "未知"
    if 1 <= number <= 3:
        return "1—3日"
    if 4 <= number <= 7:
        return "4—7日"
    if 8 <= number <= 12:
        return "8—12日"
    if 13 <= number <= 20:
        return "13—20日"
    return "其他"


def _stratified_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"sample_count": int(len(frame))}
    for period in (5, 10):
        metrics = _metric_row(frame, period)
        result.update(
            {
                f"completed_{period}d_count": metrics["completed_count"],
                f"{period}d_win_rate": metrics["win_rate"],
                f"{period}d_mean_return": metrics["mean_return"],
                f"{period}d_median_return": metrics["median_return"],
            }
        )
        if period == 5:
            result["5d_mean_mfe"] = metrics["mean_mfe"]
            result["5d_mean_mae"] = metrics["mean_mae"]
    return result


def build_stratified_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=STRATIFIED_COLUMNS)
    working = events.copy()
    working["_signal_type"] = working["signal_type"].astype(str)
    working["_qualified_board_count"] = working["qualified_board_count"].map(
        _board_bucket
    )
    working["_max_board_count"] = working["max_board_count"].map(_board_bucket)
    working["_observation_day"] = working["observation_day"].map(
        _observation_bucket
    )
    working["_signal_year"] = pd.to_datetime(working["signal_date"]).dt.year.astype(
        str
    )
    dimensions = [
        ("signal_type", "_signal_type"),
        ("qualified_board_count", "_qualified_board_count"),
        ("max_board_count", "_max_board_count"),
        ("observation_day", "_observation_day"),
        ("signal_year", "_signal_year"),
    ]
    rows = []
    for dimension, column in dimensions:
        for group, frame in working.groupby(column, sort=True):
            rows.append(
                {
                    "dimension": dimension,
                    "group": group,
                    **_stratified_metrics(frame),
                }
            )
    return pd.DataFrame(rows, columns=STRATIFIED_COLUMNS)


def _select_first_signal_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    priority = events["signal_type"].map({"MD_1": 0, "MD_2": 1}).fillna(99)
    ordered = (
        events.assign(_signal_priority=priority)
        .sort_values(
            ["candidate_cycle_id", "signal_date", "_signal_priority"],
            kind="stable",
        )
        .drop_duplicates("candidate_cycle_id", keep="first")
        .sort_values(
            ["signal_date", "candidate_cycle_id", "_signal_priority"],
            kind="stable",
        )
    )
    return ordered.drop(columns="_signal_priority").reset_index(drop=True)


def run_event_backtest(
    *,
    signals: pd.DataFrame,
    candidate_cycles: pd.DataFrame,
    indicator_bars: pd.DataFrame,
    trading_days: Iterable[date],
    as_of_date: date,
    config: StrategyConfig,
) -> BacktestResult:
    missing = sorted(REQUIRED_SIGNAL_COLUMNS - set(signals.columns))
    if missing and not signals.empty:
        raise ValueError(f"Signals missing fields: {', '.join(missing)}")

    signal_data = signals.copy(deep=True)
    if signal_data.empty:
        events = pd.DataFrame(columns=EVENT_COLUMNS)
        first = pd.DataFrame(columns=EVENT_COLUMNS)
        return BacktestResult(
            events=events,
            first_signal_events=first,
            signal_summary=build_signal_summary(events, first),
            stratified_summary=build_stratified_summary(events),
            incomplete_samples=pd.DataFrame(columns=INCOMPLETE_COLUMNS),
            warnings=[],
        )
    for column in ("signal_date", "qualified_date", "last_limit_up_date"):
        signal_data[column] = pd.to_datetime(
            signal_data[column], errors="raise"
        ).dt.date
    signal_data = signal_data[signal_data["signal_date"] <= as_of_date].copy()

    calendar = sorted(
        {_date_value(value) for value in trading_days if _date_value(value) <= as_of_date}
    )
    calendar_position = {value: index for index, value in enumerate(calendar)}

    bars = indicator_bars.copy(deep=True)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="raise").dt.date
    bars = bars[bars["trade_date"] <= as_of_date].copy()
    duplicated = bars.duplicated(["trade_date", "ts_code"], keep=False)
    if duplicated.any():
        raise ValueError("Indicator bars contain duplicate trade_date/ts_code rows")
    bar_lookup = {
        (row.trade_date, row.ts_code): row._asdict()
        for row in bars.itertuples(index=False)
    }
    candidate_complete = (
        candidate_cycles.set_index("candidate_cycle_id")["data_complete"].to_dict()
        if not candidate_cycles.empty and "data_complete" in candidate_cycles
        else {}
    )

    warnings: list[str] = []
    issues: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    def warn(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    priority = signal_data["signal_type"].map({"MD_1": 0, "MD_2": 1}).fillna(99)
    signal_data = (
        signal_data.assign(_signal_priority=priority)
        .sort_values(
            ["signal_date", "candidate_cycle_id", "_signal_priority"],
            kind="stable",
        )
        .drop(columns="_signal_priority")
    )

    for signal in signal_data.itertuples(index=False):
        complete = bool(candidate_complete.get(signal.candidate_cycle_id, False))
        if signal.candidate_cycle_id not in candidate_complete:
            warn(f"Candidate cycle not found for signal {signal.candidate_cycle_id}")
        event = _blank_event(signal, complete, as_of_date)
        signal_position = calendar_position.get(signal.signal_date)
        if signal_position is None:
            event["entry_status"] = "MARKET_DATA_INCOMPLETE"
            event["data_complete"] = False
            details = f"Signal date {signal.signal_date} is absent from market calendar"
            warn(details)
            issues.append(
                _issue(signal, "MARKET_DATA_INCOMPLETE", details=details)
            )
            for period in HOLDING_PERIODS:
                event[f"holding_status_{period}d"] = "MARKET_DATA_INCOMPLETE"
            event_rows.append(event)
            continue

        entry_bar: dict[str, Any] | None = None
        entry_date: date | None = None
        entry_delay: int | None = None
        suspended_before_entry = 0
        entry_incomplete = False
        for delay, day in enumerate(calendar[signal_position + 1 :], start=1):
            bar = bar_lookup.get((day, signal.ts_code))
            if bar is None:
                details = f"{signal.ts_code} market row missing on {day.isoformat()}"
                warn(details)
                issues.append(
                    _issue(
                        signal,
                        "MARKET_DATA_INCOMPLETE",
                        details=details,
                        affected_date=day,
                    )
                )
                entry_incomplete = True
                break
            if bool(bar.get("suspended", False)):
                suspended_before_entry += 1
                continue
            if not _valid_ohlc(bar):
                details = f"{signal.ts_code} OHLC is invalid on {day.isoformat()}"
                warn(details)
                issues.append(
                    _issue(
                        signal,
                        "MARKET_DATA_INCOMPLETE",
                        details=details,
                        affected_date=day,
                    )
                )
                entry_incomplete = True
                break
            entry_bar = bar
            entry_date = day
            entry_delay = delay
            break

        if entry_incomplete:
            event["entry_status"] = "MARKET_DATA_INCOMPLETE"
            event["data_complete"] = False
            for period in HOLDING_PERIODS:
                event[f"holding_status_{period}d"] = "MARKET_DATA_INCOMPLETE"
            event_rows.append(event)
            continue
        if entry_bar is None or entry_date is None or entry_delay is None:
            event["entry_status"] = "NO_ENTRY_DATA"
            details = f"No tradable entry for {signal.ts_code} after {signal.signal_date}"
            issues.append(_issue(signal, "NO_ENTRY_DATA", details=details))
            for period in HOLDING_PERIODS:
                event[f"holding_status_{period}d"] = "NO_ENTRY_DATA"
            event_rows.append(event)
            continue

        entry_price = _number(entry_bar["open"])
        assert entry_price is not None
        event.update(
            {
                "entry_status": "COMPLETED",
                "entry_date": entry_date,
                "entry_delay_market_days": entry_delay,
                "entry_price": entry_price,
                "entry_open_equals_high_equals_low_equals_close": len(
                    {
                        _number(entry_bar[column])
                        for column in ("open", "high", "low", "close")
                    }
                )
                == 1,
            }
        )
        signal_close = _number(signal.close)
        if signal_close is not None and signal_close != 0:
            event["signal_close_to_entry_gap"] = entry_price / signal_close - 1.0
        if suspended_before_entry:
            issues.append(
                _issue(
                    signal,
                    "ENTRY_DELAYED_BY_SUSPENSION",
                    details=(
                        f"Entry delayed by {suspended_before_entry} suspended "
                        "market day(s)"
                    ),
                    affected_date=entry_date,
                )
            )

        entry_position = calendar_position[entry_date]
        valid_bars: list[tuple[date, dict[str, Any]]] = [(entry_date, entry_bar)]
        missing_days: list[date] = []
        for day in calendar[entry_position + 1 :]:
            bar = bar_lookup.get((day, signal.ts_code))
            if bar is None or (
                not bool(bar.get("suspended", False)) and not _valid_ohlc(bar)
            ):
                missing_days.append(day)
                details = f"{signal.ts_code} market data incomplete on {day.isoformat()}"
                warn(details)
                continue
            if bool(bar.get("suspended", False)):
                continue
            valid_bars.append((day, bar))
            if len(valid_bars) > max(HOLDING_PERIODS):
                break

        for period in HOLDING_PERIODS:
            status_column = f"holding_status_{period}d"
            if len(valid_bars) <= period:
                if missing_days:
                    event[status_column] = "MARKET_DATA_INCOMPLETE"
                    event["data_complete"] = False
                    issue_type = "MARKET_DATA_INCOMPLETE"
                    details = (
                        f"{period}d horizon affected by missing market data"
                    )
                    affected_date = missing_days[0]
                else:
                    event[status_column] = "INSUFFICIENT_FORWARD_DATA"
                    issue_type = "INSUFFICIENT_FORWARD_DATA"
                    details = f"{period}d horizon is beyond {as_of_date.isoformat()}"
                    affected_date = None
                issues.append(
                    _issue(
                        signal,
                        issue_type,
                        details=details,
                        holding_period=period,
                        affected_date=affected_date,
                    )
                )
                continue

            exit_date, exit_bar = valid_bars[period]
            window_missing = [
                value for value in missing_days if entry_date <= value <= exit_date
            ]
            if window_missing:
                event[status_column] = "MARKET_DATA_INCOMPLETE"
                event["data_complete"] = False
                issues.append(
                    _issue(
                        signal,
                        "MARKET_DATA_INCOMPLETE",
                        details=f"{period}d window contains missing market data",
                        holding_period=period,
                        affected_date=window_missing[0],
                    )
                )
                continue

            window = valid_bars[: period + 1]
            exit_close = _number(exit_bar["close"])
            assert exit_close is not None
            maximum = max(window, key=lambda item: float(item[1]["high"]))
            minimum = min(window, key=lambda item: float(item[1]["low"]))
            event.update(
                {
                    status_column: "COMPLETED",
                    f"return_{period}d": exit_close / entry_price - 1.0,
                    f"mfe_{period}d": float(maximum[1]["high"]) / entry_price
                    - 1.0,
                    f"mae_{period}d": float(minimum[1]["low"]) / entry_price
                    - 1.0,
                    f"exit_date_{period}d": exit_date,
                    f"max_high_date_{period}d": maximum[0],
                    f"min_low_date_{period}d": minimum[0],
                }
            )
        event_rows.append(event)

    events = pd.DataFrame(event_rows, columns=EVENT_COLUMNS)
    first = _select_first_signal_events(events)
    incomplete = pd.DataFrame(issues, columns=INCOMPLETE_COLUMNS)
    if not incomplete.empty:
        incomplete = incomplete.sort_values(
            [
                "signal_date",
                "candidate_cycle_id",
                "issue_type",
                "holding_period",
            ],
            kind="stable",
            na_position="last",
        ).reset_index(drop=True)
    return BacktestResult(
        events=events.reset_index(drop=True),
        first_signal_events=first,
        signal_summary=build_signal_summary(events, first),
        stratified_summary=build_stratified_summary(events),
        incomplete_samples=incomplete,
        warnings=warnings,
    )
