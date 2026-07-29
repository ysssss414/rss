from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_sma(values: pd.Series, period: int) -> pd.Series:
    """Return the Tonghuashun/Wilder recurrence with a first-valid-value seed.

    The first non-NaN input is used as the fixed seed. Later NaN inputs keep the
    previous state internally but remain NaN in the returned series. This lets
    suspension rows avoid becoming artificial trading observations.
    """

    if period < 1:
        raise ValueError("period must be at least 1")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    output = pd.Series(np.nan, index=numeric.index, dtype=float)
    previous: float | None = None
    for index, value in numeric.items():
        if pd.isna(value):
            continue
        current = float(value) if previous is None else (
            float(value) + (period - 1) * previous
        ) / period
        output.at[index] = current
        previous = current
    return output


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI using the requested Tonghuashun SMA recurrence.

    The first price has no LC and therefore no RSI. The first valid price
    change seeds both recursive averages. If the smoothed absolute change is
    zero, the result is NaN rather than infinity.
    """

    numeric_close = pd.to_numeric(close, errors="coerce").astype(float)
    delta = numeric_close.diff()
    gain = delta.clip(lower=0)
    absolute_change = delta.abs()
    average_gain = wilder_sma(gain, period)
    average_change = wilder_sma(absolute_change, period)
    denominator = average_change.replace(0.0, np.nan)
    return average_gain.div(denominator).mul(100.0)


def add_indicators(bars: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    required = {"ts_code", "trade_date", "close"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"Market bars missing fields: {', '.join(missing)}")

    result = bars.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.date
    if "suspended" not in result:
        result["suspended"] = False
    result = result.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    result["rsi14"] = np.nan
    result["ma5"] = np.nan

    for _, positions in result.groupby("ts_code", sort=False).groups.items():
        group = result.loc[positions]
        valid = (~group["suspended"].astype(bool)) & group["close"].notna()
        valid_positions = group.index[valid]
        valid_close = pd.to_numeric(group.loc[valid_positions, "close"], errors="coerce")
        result.loc[valid_positions, "rsi14"] = calculate_rsi(
            valid_close, rsi_period
        ).to_numpy()
        result.loc[valid_positions, "ma5"] = (
            valid_close.rolling(5, min_periods=5).mean().to_numpy()
        )
    return result
