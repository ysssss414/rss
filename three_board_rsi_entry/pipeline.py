from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import StrategyConfig
from .indicators import add_indicators
from .input_excel import InputData, load_input_workbook
from .market_data import MarketDataProvider
from .models import ReplayResult
from .replay import run_replay


@dataclass(frozen=True)
class AnalysisRun:
    result: ReplayResult
    input_data: InputData
    checks: pd.DataFrame
    warnings: list[str]
    config: StrategyConfig
    start_date: date
    as_of_date: date
    input_path: Path
    input_hash: str
    market_source: str


def _input_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_analysis(
    *,
    input_path: Path | str,
    start_date: date,
    as_of_date: date,
    provider: MarketDataProvider,
    config: StrategyConfig,
    allow_incomplete: bool = False,
    force_refresh: bool = False,
) -> AnalysisRun:
    if start_date > as_of_date:
        raise ValueError("start_date cannot be later than as_of_date")
    input_file = Path(input_path)

    # A generously bounded calendar request is used only to locate exactly N
    # actual trading days of warmup. No market row after as_of_date is requested.
    calendar_probe_start = start_date - timedelta(
        days=config.warmup_trading_days * 4 + 365
    )
    full_calendar = provider.trading_days(calendar_probe_start, as_of_date)
    analysis_calendar = [
        value for value in full_calendar if start_date <= value <= as_of_date
    ]
    if not analysis_calendar:
        raise ValueError(
            f"No trading days returned between {start_date} and {as_of_date}"
        )
    prior_days = [value for value in full_calendar if value < start_date]
    warmup_days = prior_days[-config.warmup_trading_days :]
    market_start = warmup_days[0] if warmup_days else start_date

    input_data = load_input_workbook(
        input_file,
        start_date=start_date,
        as_of_date=as_of_date,
        trading_days=analysis_calendar,
        allow_incomplete=allow_incomplete,
    )
    codes = sorted(set(input_data.boards["ts_code"]))
    raw_bars = provider.daily_bars(
        codes,
        market_start,
        as_of_date,
        price_adjustment=config.price_adjustment,
        force_refresh=force_refresh,
    )
    indicator_bars = add_indicators(raw_bars, config.rsi_period)

    warnings = list(input_data.warnings)
    checks: list[dict[str, str]] = [
        {
            "check_name": "trading_day_confirmations",
            "status": "PASS" if input_data.data_complete else "WARN",
            "details": (
                "All trading days confirmed"
                if input_data.data_complete
                else f"{len(input_data.missing_confirmation_dates)} dates missing"
            ),
        },
        {
            "check_name": "future_data_cutoff",
            "status": "PASS",
            "details": f"Manual input and market data limited to {as_of_date.isoformat()}",
        },
        {
            "check_name": "price_adjustment",
            "status": "PASS",
            "details": config.price_adjustment,
        },
    ]

    if len(warmup_days) < config.warmup_trading_days:
        message = (
            f"Calendar supplied only {len(warmup_days)} of "
            f"{config.warmup_trading_days} requested warmup trading days"
        )
        warnings.append(message)
        checks.append(
            {"check_name": "calendar_warmup", "status": "WARN", "details": message}
        )
    else:
        checks.append(
            {
                "check_name": "calendar_warmup",
                "status": "PASS",
                "details": f"{len(warmup_days)} trading days loaded",
            }
        )

    expected = {
        (code, day)
        for code in codes
        for day in analysis_calendar
        if day >= input_data.boards.loc[
            input_data.boards["ts_code"] == code, "trade_date"
        ].min()
    }
    actual = set(zip(indicator_bars["ts_code"], indicator_bars["trade_date"]))
    missing_market = sorted(expected - actual)
    market_complete = not missing_market
    if missing_market:
        preview = ", ".join(f"{code} {day}" for code, day in missing_market[:20])
        message = (
            f"Missing {len(missing_market)} market rows in analysis window: {preview}"
        )
        warnings.append(message)
        checks.append(
            {"check_name": "market_data_coverage", "status": "WARN", "details": message}
        )
    else:
        checks.append(
            {
                "check_name": "market_data_coverage",
                "status": "PASS",
                "details": "Every candidate/date has a bar or explicit suspension row",
            }
        )

    for code in codes:
        valid_warmup = indicator_bars[
            (indicator_bars["ts_code"] == code)
            & (indicator_bars["trade_date"] < start_date)
            & (~indicator_bars["suspended"])
            & indicator_bars["close"].notna()
        ]
        if len(valid_warmup) < config.warmup_trading_days:
            message = (
                f"{code} has {len(valid_warmup)} valid warmup bars; "
                f"requested {config.warmup_trading_days}"
            )
            warnings.append(message)
            checks.append(
                {
                    "check_name": f"indicator_warmup:{code}",
                    "status": "WARN",
                    "details": message,
                }
            )
        else:
            checks.append(
                {
                    "check_name": f"indicator_warmup:{code}",
                    "status": "PASS",
                    "details": f"{len(valid_warmup)} valid bars",
                }
            )

    result = run_replay(
        boards=input_data.boards,
        indicator_bars=indicator_bars,
        trading_days=analysis_calendar,
        start_date=start_date,
        as_of_date=as_of_date,
        config=config,
        data_complete=input_data.data_complete and market_complete,
    )
    return AnalysisRun(
        result=result,
        input_data=input_data,
        checks=pd.DataFrame(
            checks, columns=["check_name", "status", "details"]
        ),
        warnings=warnings,
        config=config,
        start_date=start_date,
        as_of_date=as_of_date,
        input_path=input_file,
        input_hash=_input_hash(input_file),
        market_source=type(provider).__name__,
    )
