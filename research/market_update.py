"""Gap-driven updates for a mutable LocalMarketStore.

The vendor client is injected. Research reads remain offline, and only complete
monthly Parquet files are atomically replaced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .market_store import DATASETS, LocalMarketStore, _now, _stats, _write_json


def missing_trading_dates(available: list[str], present: list[str], end: str) -> list[str]:
    """Find interior holes as well as new trailing sessions."""
    return sorted(set(day for day in available if day <= end) - set(present))


def common_factor_scale(old: pd.Series, new: pd.Series, tolerance: float = 1e-7) -> float | None:
    """Return old/new common scale, or None when historical ratios changed."""
    joined = pd.concat([old.rename("old"), new.rename("new")], axis=1).dropna()
    if joined.empty:
        return 1.0
    ratio = joined.old / joined.new
    if not ratio.gt(0).all():
        return None
    anchor = float(ratio.iloc[0])
    return anchor if ((ratio / anchor - 1).abs() <= tolerance).all() else None


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    if temporary.exists():
        temporary.unlink()
    frame.to_parquet(temporary, index=False)
    if len(pd.read_parquet(temporary)) != len(frame):
        temporary.unlink()
        raise ValueError("Parquet write/read row-count mismatch")
    temporary.replace(path)


def _upsert(root: Path, dataset: str, incoming: pd.DataFrame) -> int:
    if incoming.empty:
        return 0
    if dataset == "security_master":
        path = root / dataset / "part.parquet"
        old = pd.read_parquet(path)
        merged = pd.concat([old, incoming], ignore_index=True).drop_duplicates("security_id", keep="last")
        merged = merged.sort_values("security_id").reset_index(drop=True)
        _atomic_parquet(merged, path)
        return len(merged) - len(old)
    if dataset == "trading_calendar":
        path = root / dataset / "part.parquet"
        old = pd.read_parquet(path)
        merged = pd.concat([old, incoming], ignore_index=True).drop_duplicates("trade_date", keep="last")
        merged = merged.sort_values("trade_date").reset_index(drop=True)
        _atomic_parquet(merged, path)
        return len(merged) - len(old)
    added = 0
    incoming = incoming.copy()
    incoming["trade_date"] = pd.to_datetime(incoming.trade_date).dt.normalize()
    for (year, month), part in incoming.groupby([incoming.trade_date.dt.year, incoming.trade_date.dt.month]):
        path = root / dataset / f"year={year}" / f"month={month:02d}" / "part.parquet"
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=part.columns)
        merged = pd.concat([old, part], ignore_index=True)
        if merged.duplicated(["security_id", "trade_date"], keep=False).any():
            # New rows win only for an explicitly requested bounded refresh.
            merged = merged.drop_duplicates(["security_id", "trade_date"], keep="last")
        merged = merged.sort_values(["trade_date", "security_id"]).reset_index(drop=True)
        added += len(merged) - len(old)
        _atomic_parquet(merged, path)
    return added


def _canonical_bars(raw: pd.DataFrame, code: str, wanted: set[str]) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["security_id", "trade_date", "open", "high", "low", "close", "volume", "amount"])
    if not raw.code.eq(code).all():
        raise ValueError("Bar response identity mismatch")
    frame = raw.rename(columns={"code": "security_id", "date": "trade_date"})
    frame = frame.loc[frame.trade_date.astype(str).isin(wanted),
        ["security_id", "trade_date", "open", "high", "low", "close", "volume", "amount"]].copy()
    return frame


def _canonical_status(raw: pd.DataFrame, code: str, wanted: set[str]) -> pd.DataFrame:
    names = {"MARKET_CODE": "security_id", "TRADE_DATE": "trade_date", "PRECLOSE": "preclose",
        "HIGH_LIMITED": "high_limited", "LOW_LIMITED": "low_limited",
        "PRICE_HIGH_LMT_RATE": "price_high_lmt_rate", "PRICE_LOW_LMT_RATE": "price_low_lmt_rate",
        "IS_ST_SEC": "is_st_sec", "IS_SUSP_SEC": "is_susp_sec", "IS_WD_SEC": "is_wd_sec",
        "IS_XR_SEC": "is_xr_sec"}
    if raw.empty:
        return pd.DataFrame(columns=list(names.values()))
    frame = raw.rename(columns=names).copy()
    if frame.security_id.dropna().ne(code).any():
        raise ValueError("Status response identity mismatch")
    frame["trade_date"] = pd.to_datetime(pd.to_numeric(frame.trade_date, errors="coerce").astype("Int64").astype(str),
                                           format="%Y%m%d", errors="coerce")
    frame = frame.loc[frame.security_id.notna() & frame.trade_date.dt.strftime("%Y-%m-%d").isin(wanted),
                      list(names.values())]
    for column in ("is_st_sec", "is_susp_sec", "is_wd_sec", "is_xr_sec"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").map({0: False, 1: True}).astype("boolean")
    return frame


def _validate_new(dataset: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    if frame[["security_id", "trade_date"]].isna().any().any() or frame.duplicated(
        ["security_id", "trade_date"]).any():
        raise ValueError(f"{dataset}: null or duplicate key")
    if dataset == "daily_bars":
        price = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        if (price.isna().any().any() or price.le(0).any().any() or
            (price.high < price[["open", "close"]].max(axis=1)).any() or
            (price.low > price[["open", "close"]].min(axis=1)).any() or
            pd.to_numeric(frame.volume).lt(0).any() or pd.to_numeric(frame.amount).lt(0).any()):
            raise ValueError("Invalid new OHLCV")
    if dataset == "adjustment_factor" and (frame.factor.isna().any() or frame.factor.le(0).any()):
        raise ValueError("Invalid new factor")


def update_market_store(root: Path, end: str, fetcher, *, refresh_start: str | None = None,
                        refresh_datasets: tuple[str, ...] = ("daily_bars", "daily_status"),
                        refresh_codes: tuple[str, ...] | None = None,
                        refresh_factors: bool = False, factor_trailing_days: int = 20) -> dict:
    """Incrementally sync missing sessions; factor refresh is explicit/periodic.

    ``fetcher`` supplies calendar, master, bars, status and factors methods.
    A no-op rerun returns before contacting it, including calendar.
    """
    root = Path(root)
    store = LocalMarketStore(root)
    manifest = store.manifest
    calendar = [str(day.date()) for day in pd.to_datetime(store.load_trading_calendar().trade_date)]
    first_remote_count = fetcher.remote_calls
    if end > manifest.get("calendar_checked_through", calendar[-1]):
        available = fetcher.calendar(end)
        if not available or calendar[-1] not in available:
            raise ValueError("Vendor calendar does not contain current local final session")
    else:
        available = calendar
    if set(refresh_datasets) - {"daily_bars", "daily_status"}:
        raise ValueError("Only bars/status support bounded date repair")
    plans, base_missing = {}, {}
    refresh_days = {day for day in available if refresh_start and refresh_start <= day <= end}
    for dataset in ("daily_bars", "daily_status"):
        paths = sorted((root / dataset).glob("year=*/month=*/part.parquet"))
        rows = store.con.execute("SELECT DISTINCT trade_date FROM read_parquet(?)",
                                 [[str(path) for path in paths]]).fetchall()
        present = [pd.Timestamp(day).date().isoformat() for (day,) in rows]
        base_missing[dataset] = missing_trading_dates(available, present, end)
        dates = sorted(set(base_missing[dataset]) | (refresh_days if dataset in refresh_datasets else set()))
        plans[dataset] = dates
    factor_dates = []
    if refresh_factors:
        factor_dates = [day for day in available if day <= end][-factor_trailing_days:]
    if not any(plans.values()) and not factor_dates and not (set(available) - set(calendar)):
        store.close()
        if end > manifest.get("calendar_checked_through", calendar[-1]):
            manifest.update(calendar_checked_through=end, updated_at=_now())
            _write_json(root / "metadata" / "manifest.json", manifest)
        return {"status": "NO_OP", "missing_dates": plans,
                "remote_calls": fetcher.remote_calls - first_remote_count,
                "new_rows": {dataset: 0 for dataset in DATASETS}, "factor_refresh_dates": []}
    master = store.load_security_master()
    store.close()
    new_master = fetcher.security_master(end)
    if new_master.security_id.isna().any() or new_master.security_id.duplicated().any():
        raise ValueError("Incremental master invalid")
    master_all = pd.concat([master, new_master]).drop_duplicates("security_id", keep="last")
    new_rows = {dataset: 0 for dataset in DATASETS}
    staging = root / "metadata" / "staging" / f"update_{end.replace('-', '')}"
    staging.mkdir(parents=True, exist_ok=True)
    staged = {}
    status_missing_fields = 0
    for dataset, operation, normalize in (
        ("daily_bars", fetcher.bars, _canonical_bars),
        ("daily_status", fetcher.status, _canonical_status)):
        if not plans[dataset]:
            continue
        parts = []
        for row in master_all.itertuples(index=False):
            wanted = set(base_missing[dataset])
            if dataset in refresh_datasets and (refresh_codes is None or row.security_id in refresh_codes):
                wanted |= refresh_days
            if not wanted or pd.Timestamp(row.listing_date).date().isoformat() > max(wanted):
                continue
            raw = operation(row.security_id, min(wanted), max(wanted))
            part = normalize(raw, row.security_id, wanted)
            if not part.empty:
                parts.append(part)
        frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        _validate_new(dataset, frame)
        if dataset == "daily_status" and not frame.empty:
            status_missing_fields = int(frame.drop(columns=["security_id", "trade_date"]).isna().any(axis=1).sum())
        staged[dataset] = frame
        frame.to_csv(staging / f"{dataset}.csv.gz", index=False, compression="gzip")
    factor_issues = []
    if factor_dates:
        old_store = LocalMarketStore(root)
        old_factors = old_store.load_adjustment_factor(start=min(factor_dates), end=max(factor_dates))
        factor_paths = sorted((root / "adjustment_factor").glob("year=*/month=*/part.parquet"))
        anchors = old_store.con.execute("""SELECT security_id, trade_date, factor FROM read_parquet(?)
            QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY trade_date) = 1""",
            [[str(path) for path in factor_paths]]).df()
        old_factors = pd.concat([old_factors, anchors], ignore_index=True).drop_duplicates(
            ["security_id", "trade_date"])
        old_store.close()
        parts = []
        codes = master_all.security_id.tolist()
        for offset in range(0, len(codes), 100):
            group = codes[offset:offset + 100]
            wide = fetcher.factors(group)
            if set(group) - set(wide.columns):
                raise ValueError("Factor response missing securities")
            for code in group:
                series = pd.to_numeric(wide[code], errors="coerce")
                series.index = pd.to_datetime(series.index)
                fresh = series.loc[series.index.strftime("%Y-%m-%d").isin(factor_dates)]
                old = old_factors.loc[old_factors.security_id.eq(code)].set_index("trade_date").factor
                scale = common_factor_scale(old, series)
                if scale is None:
                    factor_issues.append({"security_id": code, "issue": "HISTORICAL_RATIO_CHANGED"})
                    continue
                if not fresh.empty:
                    parts.append(pd.DataFrame({"security_id": code, "trade_date": fresh.index,
                        "factor": fresh.to_numpy() * scale}))
        frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        _validate_new("adjustment_factor", frame)
        staged["adjustment_factor"] = frame
        frame.to_csv(staging / "adjustment_factor.csv.gz", index=False, compression="gzip")
    # No canonical write occurs until every requested remote response and staged
    # dataset has passed validation.
    new_rows["security_master"] = _upsert(root, "security_master", new_master)
    calendar_frame = pd.DataFrame({"trade_date": pd.to_datetime(sorted(set(available) - set(calendar))),
                                   "is_trading_day": True})
    new_rows["trading_calendar"] = _upsert(root, "trading_calendar", calendar_frame)
    for dataset, frame in staged.items():
        new_rows[dataset] = _upsert(root, dataset, frame)
    con = LocalMarketStore(root)
    stats = {dataset: _stats(con.con, root, dataset) for dataset in DATASETS}
    con.close()
    lineage = manifest.setdefault("lineage", {})
    for dataset in DATASETS:
        if dataset in staged or dataset in ("security_master", "trading_calendar"):
            lineage[dataset] = {"source": "AmazingData", "retrieved_at": _now(),
                "date_start": stats[dataset].get("min_trade_date"),
                "date_end": stats[dataset].get("max_trade_date"),
                "row_count": stats[dataset]["rows"], "schema_version": 1}
    manifest.update(updated_at=_now(), calendar_checked_through=end,
        security_count=stats["security_master"]["rows"],
        trading_day_count=stats["trading_calendar"]["rows"],
        dataset_row_counts={dataset: stats[dataset]["rows"] for dataset in DATASETS},
        latest_trade_date={dataset: stats[dataset].get("max_trade_date") for dataset in DATASETS},
        coverage=stats)
    _write_json(root / "metadata" / "manifest.json", manifest)
    return {"status": "UPDATED", "missing_dates": plans,
        "remote_calls": fetcher.remote_calls - first_remote_count,
        "new_rows": new_rows, "factor_refresh_dates": factor_dates,
        "factor_issues": factor_issues, "status_missing_field_rows": status_missing_fields}
