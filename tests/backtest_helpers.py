from __future__ import annotations

from datetime import date

import pandas as pd

from three_board_rsi_entry.config import StrategyConfig

from .helpers import CODE


def signal_frame(
    records: list[tuple[date, str, str]] | None = None,
) -> pd.DataFrame:
    records = records or [(date(2025, 1, 2), "cycle-a", "MD_1")]
    rows = []
    for signal_date, cycle_id, signal_type in records:
        rows.append(
            {
                "signal_date": signal_date,
                "candidate_cycle_id": cycle_id,
                "ts_code": CODE,
                "stock_name": "测试股",
                "signal_type": signal_type,
                "qualified_date": signal_date,
                "qualified_board_count": 3,
                "last_limit_up_date": signal_date,
                "max_board_count": 4,
                "observation_day": 2,
                "rsi14": 75.0,
                "previous_rsi14": 69.0,
                "close": 100.0,
                "high": 102.0,
                "low": 98.0,
                "ma5": 99.0,
                "below70_trading_days": 1,
                "below70_min_rsi": 65.0,
                "price_adjustment": "qfq",
                "run_as_of": signal_date,
            }
        )
    return pd.DataFrame(rows)


def candidate_frame(cycle_ids: list[str] | None = None) -> pd.DataFrame:
    cycle_ids = cycle_ids or ["cycle-a"]
    return pd.DataFrame(
        [
            {
                "candidate_cycle_id": cycle_id,
                "data_complete": True,
            }
            for cycle_id in cycle_ids
        ]
    )


def market_bars(
    days: list[date],
    *,
    suspended: set[int] | None = None,
    omitted: set[int] | None = None,
    prices: dict[int, tuple[float, float, float, float]] | None = None,
) -> pd.DataFrame:
    suspended = suspended or set()
    omitted = omitted or set()
    prices = prices or {}
    rows = []
    for index, day in enumerate(days):
        if index in omitted:
            continue
        is_suspended = index in suspended
        open_price, high, low, close = prices.get(
            index,
            (100.0 + index, 102.0 + index, 98.0 + index, 101.0 + index),
        )
        rows.append(
            {
                "trade_date": day,
                "ts_code": CODE,
                "open": None if is_suspended else open_price,
                "high": None if is_suspended else high,
                "low": None if is_suspended else low,
                "close": None if is_suspended else close,
                "amount": 0.0 if is_suspended else 1_000_000.0,
                "suspended": is_suspended,
                "rsi14": None if is_suspended else 75.0,
                "ma5": None if is_suspended else 99.0,
            }
        )
    return pd.DataFrame(rows)


def run_backtest(
    *,
    days: list[date],
    signals: pd.DataFrame | None = None,
    candidates: pd.DataFrame | None = None,
    bars: pd.DataFrame | None = None,
    as_of_date: date | None = None,
):
    from three_board_rsi_entry.backtest import run_event_backtest

    signals = signals if signals is not None else signal_frame([(days[0], "cycle-a", "MD_1")])
    candidates = (
        candidates
        if candidates is not None
        else candidate_frame(signals["candidate_cycle_id"].drop_duplicates().tolist())
    )
    bars = bars if bars is not None else market_bars(days)
    return run_event_backtest(
        signals=signals,
        candidate_cycles=candidates,
        indicator_bars=bars,
        trading_days=days,
        as_of_date=as_of_date or days[-1],
        config=StrategyConfig(),
    )
