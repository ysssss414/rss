from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.replay import run_replay


CODE = "000001.SZ"
TEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".test_workspace"
# pytest assigns each test an isolated directory; importing helpers never writes.


def trading_days(count: int = 30, start: str = "2025-01-02") -> list[date]:
    return [value.date() for value in pd.bdate_range(start, periods=count)]


def board_frame(records: list[tuple[date, str, int, str]] | None = None) -> pd.DataFrame:
    records = records or []
    return pd.DataFrame(
        [
            {
                "trade_date": trade_date,
                "ts_code": code,
                "stock_name": name,
                "board_count": board_count,
                "limit_up_type": "",
                "note": "",
            }
            for trade_date, code, board_count, name in records
        ],
        columns=[
            "trade_date",
            "ts_code",
            "stock_name",
            "board_count",
            "limit_up_type",
            "note",
        ],
    )


def indicator_bars(
    days: list[date],
    *,
    code: str = CODE,
    rsi: dict[int, float] | None = None,
    low: dict[int, float] | None = None,
    close: dict[int, float] | None = None,
    ma5: dict[int, float] | None = None,
) -> pd.DataFrame:
    rsi = rsi or {}
    low = low or {}
    close = close or {}
    ma5 = ma5 or {}
    rows = []
    for index, day in enumerate(days):
        row_close = close.get(index, 11.0)
        rows.append(
            {
                "trade_date": day,
                "ts_code": code,
                "open": row_close,
                "high": max(row_close, 12.0),
                "low": low.get(index, 11.0),
                "close": row_close,
                "amount": 1_000_000.0,
                "suspended": False,
                "rsi14": rsi.get(index, 80.0),
                "ma5": ma5.get(index, 10.0),
            }
        )
    return pd.DataFrame(rows)


def replay_one(
    *,
    days: list[date] | None = None,
    boards: pd.DataFrame | None = None,
    bars: pd.DataFrame | None = None,
    as_of_index: int | None = None,
    config: StrategyConfig | None = None,
):
    days = days or trading_days()
    boards = (
        boards
        if boards is not None
        else board_frame([(days[0], CODE, 3, "测试股")])
    )
    bars = bars if bars is not None else indicator_bars(days)
    as_of_index = len(days) - 1 if as_of_index is None else as_of_index
    return run_replay(
        boards=boards,
        indicator_bars=bars,
        trading_days=days,
        start_date=days[0],
        as_of_date=days[as_of_index],
        config=config or StrategyConfig(),
    )


def write_input_workbook(
    path: Path,
    confirmations: list[tuple[date, int, str]],
    boards: list[tuple[date, str, int]],
) -> None:
    confirmation_frame = pd.DataFrame(
        confirmations, columns=["trade_date", "input_complete", "note"]
    )
    board_data = pd.DataFrame(
        [
            {
                "trade_date": day,
                "ts_code": code,
                "stock_name": "测试股",
                "board_count": count,
                "limit_up_type": "",
                "note": "",
            }
            for day, code, count in boards
        ]
    )
    if board_data.empty:
        board_data = pd.DataFrame(
            columns=[
                "trade_date",
                "ts_code",
                "stock_name",
                "board_count",
                "limit_up_type",
                "note",
            ]
        )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        confirmation_frame.to_excel(writer, sheet_name="交易日确认", index=False)
        board_data.to_excel(writer, sheet_name="三连板股票", index=False)
