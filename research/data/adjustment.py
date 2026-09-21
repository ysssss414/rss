from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .contracts import RAW_PRICES, DataContractError
from .validation import available_mask


FACTOR_COLUMNS = ["ts_code", "trade_date", "factor", "available_at"]


def validate_factors(factors: pd.DataFrame, codes, end: date) -> pd.DataFrame:
    if not set(FACTOR_COLUMNS).issubset(factors):
        raise DataContractError("INVALID_FACTOR", "Factor schema incomplete")
    clean = factors[FACTOR_COLUMNS].copy()
    if not clean.ts_code.isin(codes).all():
        raise DataContractError("SYMBOL_MISMATCH", "Factor security mismatch")
    try:
        dates = pd.to_datetime(clean.trade_date, format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError) as exc:
        raise DataContractError("INVALID_FACTOR", "Invalid factor dates") from exc
    if dates.isna().any():
        raise DataContractError("INVALID_FACTOR", "Missing factor dates")
    clean["trade_date"] = dates.dt.strftime("%Y-%m-%d")
    if dates.dt.date.gt(end).any() or not available_mask(clean, end).all():
        raise DataContractError("AS_OF_VIOLATION", "Unavailable factor observation")
    clean["factor"] = pd.to_numeric(clean.factor, errors="coerce")
    if not np.isfinite(clean.factor).all() or clean.factor.le(0).any():
        raise DataContractError("INVALID_FACTOR", "Factors must be finite and positive")
    clean = clean.drop_duplicates()
    if clean.duplicated(["ts_code", "trade_date"]).any():
        raise DataContractError("INVALID_FACTOR", "Conflicting factor observations; use a separate snapshot for revisions")
    return clean.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)


def adjusted_bars(raw: pd.DataFrame, factors: pd.DataFrame, *, mode: str,
                  anchor: date, factor_schema: str, as_of: date | None = None) -> pd.DataFrame:
    output = raw.copy()
    if mode not in {"none", "qfq"}:
        raise DataContractError("INVALID_ARGUMENT", "Unsupported adjustment mode")
    output["adjustment_ratio"] = 1.0
    if mode == "qfq" and not raw.empty:
        if factor_schema not in {"daily", "effective_events"}:
            raise DataContractError("UNSUPPORTED_CAPABILITY", "Factor density/semantics not declared")
        if not set(FACTOR_COLUMNS).issubset(factors):
            raise DataContractError("INVALID_FACTOR", "Factor schema incomplete")
        # Select the causal prefix before validating values in later versions/dates.
        try:
            dated = pd.to_datetime(factors.trade_date, errors="raise").dt.date
        except (ValueError, TypeError) as exc:
            raise DataContractError("INVALID_FACTOR", "Invalid factor dates") from exc
        cutoff = as_of or anchor
        selected = factors.loc[(dated <= cutoff) & available_mask(factors, cutoff)].copy()
        if selected.empty:
            raise DataContractError("INVALID_FACTOR", "No factors available at anchor")
        selected = validate_factors(selected, raw.ts_code.unique(), cutoff)
        for code, positions in output.groupby("ts_code", sort=False).groups.items():
            part = selected.loc[selected.ts_code == code].sort_values("trade_date")
            if part.empty:
                raise DataContractError("INVALID_FACTOR", "Security factor missing")
            series = pd.Series(part.factor.to_numpy(), index=pd.to_datetime(part.trade_date))
            dates = pd.to_datetime(output.loc[positions, "trade_date"])
            if factor_schema == "daily":
                aligned = series.reindex(dates)
            else:
                aligned = series.reindex(series.index.union(pd.DatetimeIndex(dates))).sort_index().ffill().reindex(dates)
            if aligned.isna().any():
                raise DataContractError("INVALID_FACTOR", "Factor coverage incomplete")
            anchor_values = aligned.loc[aligned.index.date <= anchor]
            if anchor_values.empty:
                raise DataContractError("INVALID_FACTOR", "No bar/factor at or before price anchor")
            output.loc[positions, "adjustment_ratio"] = aligned.to_numpy() / float(anchor_values.iloc[-1])
    for column in RAW_PRICES:
        output[column.removeprefix("raw_")] = output[column] * output.adjustment_ratio
    output["adjustment_mode"] = mode
    output["adjustment_anchor"] = anchor.isoformat()
    return output
