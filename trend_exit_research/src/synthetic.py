"""Deterministic labelled fixture for offline CLI and causal tests."""
from __future__ import annotations

from hashlib import sha256

import pandas as pd


def synthetic_frame() -> pd.DataFrame:
    # Rise, normal disagreement, support break, failed rebound, long decline.
    prices = ([30 + i * .7 for i in range(65)] + [75 + i * .1 for i in range(20)]
              + [75, 74, 73, 70, 68, 67, 65, 64, 65, 66, 67, 66, 65, 63, 60]
              + [59 - i * .6 for i in range(35)])
    days = pd.bdate_range("2024-01-02", periods=len(prices)).strftime("%Y-%m-%d")
    prices = [round(value, 2) for value in prices]
    rows = [{"security_id": "000001.SZ", "trade_date": day, "raw_open": close, "raw_high": close + .5,
             "raw_low": close - .5, "raw_close": close, "volume": 10000., "amount": close * 10000,
             "turnover_rate": None, "factor": 1., "trading_status": "TRADING", "issue": "",
             "is_xr_sec": False, "is_wd_sec": False, "raw_available_at": day + "T15:30:00+08:00",
             "factor_available_at": day + "T15:30:00+08:00", "availability_basis": "SYNTHETIC_NOT_REAL_DATA"}
            for day, close in zip(days, prices)]
    for row in rows:
        row["daily_constraint"] = {"security_id": row["security_id"], "trade_date": row["trade_date"],
            "available_at": row["trade_date"] + "T09:00:00+08:00", "validation_status": "VALID", "trading_allowed": True,
            "suspended": False, "limit_applicable": True, "limit_up_price": f'{row["raw_close"] * 1.1:.2f}',
            "limit_down_price": f'{row["raw_close"] * .9:.2f}', "price_tick": "0.01", "source_provenance": "SYNTHETIC_QUALIFIED_ONLY"}
    identity = sha256(pd.DataFrame(rows).to_csv(index=False).encode()).hexdigest()
    return pd.DataFrame(rows).assign(source_snapshot_id="SYNTHETIC/" + identity,
        snapshot_manifest_sha256=identity, factor_source_hash=identity)
